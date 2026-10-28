"""Engineer sign-in with a second factor, and the UI shell: customers, collectors and jobs.

Signing in takes two steps. The password step only remembers who is signing in; the session
becomes signed in (and the sign-in is logged) after a valid authenticator code. An engineer
without an authenticator yet adds one at that point, with the one-time enrollment code an admin
issued (`cartio-controller enroll_engineer`), before the first sign-in completes.
"""

import time
from functools import wraps

from django.conf import settings
from django.contrib import auth
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.signals import user_login_failed
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.http import Http404, HttpResponse, HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.cache import patch_vary_headers
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods, require_POST

from cartio_controller.collectors import audit
from cartio_controller.collectors.models import CollectionRun, Customer, Site

from . import inventory as inventory_screen
from . import onboarding, overview, shell, totp
from .health import ONLINE_WITHIN
from .health import collectors as _collectors
from .models import EngineerMFA

PENDING = "cartio_pending_sign_in"
# The second step must follow the password soon after, with a few tries at most.
PENDING_SECONDS = 300
MAX_CODE_TRIES = 5

TOO_MANY_FAILURES = "Too many failed sign-ins. Wait {minutes} minutes and try again."


def _too_many_failures_error() -> str:
    return TOO_MANY_FAILURES.format(minutes=settings.SIGN_IN_LOCKOUT_MINUTES)


def _is_engineer(user) -> bool:
    return user.is_active and user.is_staff


def engineer_required(view):
    """Signed in (password and code), and a staff account."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not _is_engineer(request.user):
            raise PermissionDenied
        return view(request, *args, **kwargs)

    return wrapper


def _next_url(request, value: str) -> str:
    if value and url_has_allowed_host_and_scheme(
        value, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return value
    return reverse("engineers:customers")


def _pending_user(request):
    """The account that passed the password step in this session, if it's still fresh."""
    pending = request.session.get(PENDING)
    if not pending or time.time() - pending["at"] > PENDING_SECONDS:
        request.session.pop(PENDING, None)
        return None
    user = auth.get_user_model()._default_manager.filter(pk=pending["user"]).first()
    # A pending sign-in ends when the account runs out of failed sign-ins, before another code.
    if (
        user is None
        or not _is_engineer(user)
        or audit.too_many_failed_sign_ins(request, user.get_username())
    ):
        request.session.pop(PENDING, None)
        return None
    user.backend = pending["backend"]
    return user


def _code_failed(request, user, mfa) -> bool:
    """Log a wrong code. True when the tries are used up and the sign-in starts over."""
    user_login_failed.send(
        sender=__name__,
        credentials={auth.get_user_model().USERNAME_FIELD: user.get_username()},
        request=request,
    )
    tries = mfa.record_failure() - request.session[PENDING]["failed_before"]
    # Wrong codes count toward the account's failed sign-ins too, so restarting from the password
    # doesn't give anyone with the password unlimited guesses at the code.
    if tries >= MAX_CODE_TRIES or audit.too_many_failed_sign_ins(request, user.get_username()):
        request.session.pop(PENDING)
        return True
    return False


def _complete_sign_in(request, user):
    next_url = request.session[PENDING]["next"]
    # Rotates the session key and logs the sign-in (collectors.audit).
    auth.login(request, user, backend=user.backend)
    request.session.pop(PENDING, None)
    return redirect(_next_url(request, next_url))


@sensitive_post_parameters("password")
@csrf_protect
@never_cache
def sign_in(request):
    if request.user.is_authenticated and _is_engineer(request.user):
        return redirect(_next_url(request, request.GET.get("next", "")))
    form = AuthenticationForm(request, data=request.POST or None)
    # The username as the form cleans it (trimmed, NFKC), which is how failures are logged.
    username = form.fields["username"].to_python(request.POST.get("username", ""))
    if request.method == "POST" and audit.too_many_failed_sign_ins(request, username):
        # Refused before the password is checked, and not logged, so a flood of guesses
        # neither learns anything nor grows the sign-in log.
        return render(
            request,
            "engineers/sign_in.html",
            {
                "form": AuthenticationForm(request),
                "error": _too_many_failures_error(),
                "next": request.POST.get("next", ""),
            },
            status=429,
        )
    if request.method == "POST" and form.is_valid():
        user = form.get_user()
        if not _is_engineer(user):
            user_login_failed.send(
                sender=__name__,
                credentials={auth.get_user_model().USERNAME_FIELD: user.get_username()},
                request=request,
            )
            # The same answer as a wrong password, so it doesn't confirm the password.
            form.add_error(None, form.get_invalid_login_error())
        else:
            # A fresh session key, so a key planted before sign-in is worthless.
            request.session.cycle_key()
            mfa = EngineerMFA.objects.filter(user=user).first()
            request.session[PENDING] = {
                "user": user.pk,
                "backend": user.backend,
                "at": time.time(),
                "failed_before": mfa.failed_codes if mfa else 0,
                "next": request.POST.get("next", ""),
            }
            confirmed = mfa is not None and mfa.confirmed_at is not None
            return redirect("engineers:verify" if confirmed else "engineers:mfa-setup")
    return render(
        request,
        "engineers/sign_in.html",
        {"form": form, "next": request.GET.get("next", "")},
    )


@sensitive_post_parameters("code")
@csrf_protect
@never_cache
def verify(request):
    user = _pending_user(request)
    if user is None:
        return redirect("engineers:sign-in")
    mfa = EngineerMFA.objects.filter(user=user, confirmed_at__isnull=False).first()
    if mfa is None:
        return redirect("engineers:mfa-setup")
    error = ""
    if request.method == "POST":
        if mfa.use_code(request.POST.get("code", "")):
            return _complete_sign_in(request, user)
        if _code_failed(request, user, mfa):
            return redirect("engineers:sign-in")
        error = "That code didn't work. Enter the current code from your authenticator app."
    return render(request, "engineers/verify.html", {"error": error})


@sensitive_post_parameters("code", "enrollment_code")
@csrf_protect
@never_cache
def mfa_setup(request):
    user = _pending_user(request)
    if user is None:
        return redirect("engineers:sign-in")
    mfa = EngineerMFA.objects.filter(user=user).first()
    if mfa is not None and mfa.confirmed_at is not None:
        return redirect("engineers:verify")
    if mfa is None or not mfa.enrollment_open:
        request.session.pop(PENDING, None)
        return render(request, "engineers/mfa_setup.html", {"closed": True})
    pending = request.session[PENDING]
    error = ""
    # First the admin's one-time enrollment code, then a code from the newly added app.
    if not pending.get("enrolling"):
        if request.method == "POST":
            if mfa.check_enrollment_token(request.POST.get("enrollment_code", "")):
                pending["enrolling"] = True
                request.session.modified = True
                return redirect("engineers:mfa-setup")
            if _code_failed(request, user, mfa):
                return redirect("engineers:sign-in")
            error = "That enrollment code didn't work. Check it with the admin who issued it."
        return render(request, "engineers/mfa_setup.html", {"need_token": True, "error": error})
    if request.method == "POST":
        if mfa.use_code(request.POST.get("code", "")):
            EngineerMFA.objects.filter(pk=mfa.pk).update(
                confirmed_at=timezone.now(), enrollment_token_hash="", enrollment_expires_at=None
            )
            return _complete_sign_in(request, user)
        if _code_failed(request, user, mfa):
            return redirect("engineers:sign-in")
        error = "That code didn't work. Check the key in your app and enter its current code."
    key = totp.to_base32(mfa.secret)
    return render(
        request,
        "engineers/mfa_setup.html",
        {
            # In groups of four, easier to type into an app by hand.
            "key": " ".join(key[i : i + 4] for i in range(0, len(key), 4)),
            "uri": totp.provisioning_uri(mfa.secret, user.get_username()),
            "error": error,
        },
    )


@require_POST
@csrf_protect
def sign_out(request):
    auth.logout(request)
    return redirect("engineers:sign-in")


@never_cache
@engineer_required
def customers(request):
    """All customers (DESIGN.md section 1). Search and tabs swap the list in place (htmx)."""
    view = overview.Overview(request.GET)
    context = {
        "view": view,
        "can_onboard": onboarding.can_onboard(request.user),
        "can_add_engagement": request.user.has_perm("collectors.add_engagement"),
    }
    # htmx restoring history without a cached snapshot needs the whole page, not the list.
    if request.headers.get("HX-Request") and not request.headers.get("HX-History-Restore-Request"):
        response = render(request, "engineers/_customer_results.html", context)
        response["HX-Push-Url"] = request.get_full_path()
    else:
        response = render(request, "engineers/customers.html", context)
    patch_vary_headers(response, ["HX-Request", "HX-History-Restore-Request"])
    return response


def _onboarding_page(request, state, errors=None, status=200):
    errors = errors or {}
    step = state["step"]
    context = {
        "state": state,
        "step": step,
        "steps": [
            {
                "number": number,
                "label": label,
                "done": number != step
                and number <= state["reached"]
                and not onboarding.errors_for(state, number),
                "open": onboarding.can_open(state, number),
            }
            for number, label in enumerate(onboarding.STEPS, start=1)
        ],
        "last": onboarding.LAST,
        "errors": errors,
        "summary": onboarding.summary(state),
        "compliance_choices": Customer.Compliance.choices,
        "suggest_on_site": {"hipaa", "pci"} & set(state["compliance"]),
        "max_sites": onboarding.MAX_SITES,
        "site_rows": [
            {
                "index": index,
                "site": site,
                "errors": {
                    "name": errors.get(f"site-{index}-name"),
                    "location": errors.get(f"site-{index}-location"),
                    "seeds": errors.get(f"site-{index}-seeds"),
                    "collector_name": errors.get(f"collector-{index}-name"),
                },
            }
            for index, site in enumerate(state["sites"])
        ],
    }
    return render(request, "engineers/onboarding.html", context, status=status)


@require_http_methods(["GET", "POST"])
@csrf_protect
@never_cache
@engineer_required
def onboard(request):
    """Onboard a customer (DESIGN.md section 2). State stays in the session until created."""
    if not onboarding.can_onboard(request.user):
        raise PermissionDenied
    state = request.session.get(onboarding.SESSION_KEY) or onboarding.fresh()
    if request.method == "GET":
        request.session[onboarding.SESSION_KEY] = state
        return _onboarding_page(request, state)

    action = request.POST.get("action", "")
    if action == "restart":
        request.session[onboarding.SESSION_KEY] = onboarding.fresh()
        return redirect("engineers:onboard")
    if action == "create":
        if state["step"] != onboarding.LAST:
            # A reload of the done page, or a wizard started over in another tab.
            return redirect("engineers:onboard")
        return _create_customer(request, state)

    onboarding.save_input(state, request.POST)
    request.session[onboarding.SESSION_KEY] = state
    request.session.modified = True
    step = state["step"]
    if action == "add_site" and step == 3:
        onboarding.add_site(state)
    elif action.startswith("remove_site:") and step == 3:
        index = action.partition(":")[2]
        if index.isdigit():
            onboarding.remove_site(state, int(index))
    elif action == "back":
        onboarding.go_to(state, step - 1)
    elif action.startswith("goto:") and action[5:].isdigit():
        target = int(action[5:])
        errors = onboarding.errors_for(state, step) if target > step else {}
        if errors:
            return _onboarding_page(request, state, errors, status=400)
        if onboarding.can_open(state, target):
            onboarding.go_to(state, target)
    elif action == "next" and step < onboarding.LAST:
        errors = onboarding.errors_for(state, step)
        if errors:
            return _onboarding_page(request, state, errors, status=400)
        onboarding.go_to(state, step + 1)
    return redirect("engineers:onboard")


def _create_customer(request, state):
    incomplete = onboarding.first_incomplete(state)
    if incomplete is not None:
        onboarding.go_to(state, incomplete)
        request.session.modified = True
        return _onboarding_page(
            request, state, onboarding.errors_for(state, incomplete), status=400
        )
    try:
        customer, tokens = onboarding.create(state, request.user.get_username())
    except IntegrityError:
        # Another engineer created a customer with this name meanwhile.
        onboarding.go_to(state, 1)
        request.session.modified = True
        return _onboarding_page(request, state, onboarding.errors_for(state, 1), status=409)
    # The wizard is done: the next visit, or "Onboard another customer", starts a fresh one.
    request.session.pop(onboarding.SESSION_KEY, None)
    controller = request.build_absolute_uri("/").rstrip("/")
    for token in tokens:
        token["command"] = (
            f"uv run cartio-collector enroll --controller {controller} --token {token['token']}"
        )
    engagements = shell.engagements_of(customer)
    return render(
        request,
        "engineers/onboarding_done.html",
        {
            "created": customer,
            "summary": onboarding.summary(state),
            "tokens": tokens,
            "overview_url": shell.screen_url(
                "customer", customer, engagements[0] if engagements else None
            ),
        },
    )


def _can_see_jobs(user) -> bool:
    return user.has_perm("collectors.view_collectionrun")


def render_screen(
    request, screen, template, extra, customer, engagements, engagement, *, network_data=False
):
    """Render a per-customer screen inside the shell.

    A screen that shows network data (inventory, maps, configs, findings) shows the inside access
    only notice instead for an On site customer without live view (ADR-0005). Customer and
    collector health, which the controller keeps, still show.
    """
    context = shell.context(screen, customer, engagements, engagement)
    context["can_see_jobs"] = _can_see_jobs(request.user)
    if network_data and customer.inside_access_only:
        context.update({"screen_title": shell.SCREENS[screen], "collectors": _collectors(customer)})
        return render(request, "engineers/inside_access.html", context)
    context.update(extra)
    return render(request, template, context)


@never_cache
@engineer_required
def customer_detail(request, customer, engagement=None):
    customer, engagements, engagement = shell.resolve(customer, engagement)
    return render_screen(
        request,
        "customer",
        "engineers/customer_detail.html",
        {
            "sites": Site.objects.for_customer(customer).filter(archived_at__isnull=True),
            "collectors": _collectors(customer),
            "online_within_minutes": int(ONLINE_WITHIN.total_seconds() // 60),
        },
        customer,
        engagements,
        engagement,
    )


@require_http_methods(["GET", "POST"])
@csrf_protect
@never_cache
@engineer_required
def inventory(request, customer, engagement=None):
    customer, engagements, engagement = shell.resolve(customer, engagement)
    # Filters come in the URL. htmx posts the selection in the body, since hundreds of selected
    # ids don't fit in a URL; the body's filters, from the search box, win over the URL's.
    params = request.GET.copy()
    for key, values in request.POST.lists():
        params.setlist(key, values)
    view = inventory_screen.View(customer, engagement, params)
    extra = {"view": view, "lifecycle": inventory_screen.LIFECYCLE}
    # Filters, sorting and selection swap the results in place (htmx), without the shell.
    if request.headers.get("HX-Request") and not customer.inside_access_only:
        context = shell.context("inventory", customer, engagements, engagement) | extra
        response = render(request, "engineers/_inventory_results.html", context)
        # A new search goes into the history, as a link with the filters and no selection.
        if request.headers.get("HX-Trigger") == "inventory-filters":
            response["HX-Push-Url"] = request.path + view.here
        return response
    if request.method == "POST":
        return HttpResponseBadRequest("Post only from the inventory screen.")
    return render_screen(
        request,
        "inventory",
        "engineers/inventory.html",
        extra,
        customer,
        engagements,
        engagement,
        network_data=True,
    )


EXPORT_TYPES = {
    "csv": "text/csv; charset=utf-8",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


@require_POST
@csrf_protect
@never_cache
@engineer_required
def inventory_export(request, customer, engagement):
    """The selected visible rows as CSV or XLSX, audit-logged."""
    customer, _, engagement = shell.resolve(customer, engagement)
    # Inside access only: the controller holds no network data to export (ADR-0005).
    if customer.inside_access_only:
        raise Http404
    fmt = request.POST.get("format", "")
    if fmt not in EXPORT_TYPES:
        return HttpResponseBadRequest("Export as csv or xlsx.")
    view = inventory_screen.View(customer, engagement, request.POST)
    if not view.selected_visible:
        return HttpResponseBadRequest("Select the devices to export.")
    with transaction.atomic():
        content = inventory_screen.export(view, request.user.get_username(), fmt)
    response = HttpResponse(content, content_type=EXPORT_TYPES[fmt])
    name = inventory_screen.filename(customer, engagement, fmt)
    response["Content-Disposition"] = f'attachment; filename="{name}"'
    return response


JOBS_SHOWN = 100


@never_cache
@engineer_required
def jobs(request, customer, engagement=None):
    # The same permission the runs API asks for.
    if not _can_see_jobs(request.user):
        raise PermissionDenied
    customer, engagements, engagement = shell.resolve(customer, engagement)
    runs = (
        CollectionRun.objects.for_customer(customer)
        .select_related("collector", "engagement", "result")
        .order_by("-started_at")[:JOBS_SHOWN]
    )
    return render_screen(
        request,
        "jobs",
        "engineers/jobs.html",
        {"runs": runs, "limit": JOBS_SHOWN},
        customer,
        engagements,
        engagement,
    )


@never_cache
@engineer_required
def switcher(request):
    """The switcher's customer list, filtered as the engineer types (an htmx partial)."""
    screen = request.GET.get("screen", "")
    if screen not in shell.SCREENS:
        return HttpResponseBadRequest("Unknown screen.")
    query = request.GET.get("q", "").strip()[:200]
    return render(
        request,
        "engineers/_switcher_customers.html",
        {"switcher_customers": shell.switcher_customers(screen, query), "query": query},
    )
