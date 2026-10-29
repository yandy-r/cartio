"""Engineer sign-in with a second factor, and the UI shell: customers, collectors and jobs.

Signing in takes two steps. The password step only remembers who is signing in; the session
becomes signed in (and the sign-in is logged) after a valid authenticator code. An engineer
without an authenticator yet adds one at that point, with the one-time enrollment code an admin
issued (`cartio-controller enroll_engineer`), before the first sign-in completes.
"""

import time
import uuid
from functools import wraps

from django.conf import settings
from django.contrib import auth, messages
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.signals import user_login_failed
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.db.models import Count
from django.http import Http404, HttpResponse, HttpResponseBadRequest, StreamingHttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.cache import patch_vary_headers
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods, require_POST

from cartio_controller.collectors import assessments, audit
from cartio_controller.collectors import jobs as job_channel
from cartio_controller.collectors.models import (
    ENROLLMENT_TOKEN_LIFETIME,
    CollectionRun,
    Customer,
    Job,
    Site,
)

from . import compare as compare_screen
from . import findings as findings_screen
from . import inventory as inventory_screen
from . import onboarding, operations, overview, shell, totp
from . import report as report_screen
from . import search as global_search
from . import sitemap as sitemap_screen
from .health import ONLINE_WITHIN, collector_health
from .health import collectors as _collectors
from .models import EngineerMFA

PENDING = "cartio_pending_sign_in"
# The second step must follow the password soon after, with a few tries at most.
PENDING_SECONDS = 300
MAX_CODE_TRIES = 5

TOO_MANY_FAILURES = "Too many failed sign-ins. Wait {minutes} minutes and try again."


def _token_hours() -> str:
    """How long a new enrollment token works, for the screens that show one."""
    return f"{ENROLLMENT_TOKEN_LIFETIME.total_seconds() / 3600:g}"


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

    if request.POST.get("step") != str(state["step"]):
        # A page from before Back, or from another tab: its fields belong to another step.
        return redirect("engineers:onboard")
    onboarding.save_input(state, request.POST)
    request.session[onboarding.SESSION_KEY] = state
    request.session.modified = True
    step = state["step"]
    if action == "add_site" and step == 3:
        onboarding.add_site(state)
    elif action.startswith("remove_site:") and step == 3:
        index = _small_number(action.partition(":")[2])
        if index is not None:
            onboarding.remove_site(state, index)
    elif action == "back":
        onboarding.go_to(state, step - 1)
    elif action.startswith("goto:") and _small_number(action[5:]) is not None:
        target = _small_number(action[5:])
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


def _small_number(text: str) -> int | None:
    # isdecimal, unlike isdigit, accepts only what int() parses ("²" passes isdigit).
    return int(text) if text.isdecimal() and len(text) <= 3 else None


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
        # Most likely another engineer created a customer with this name meanwhile.
        onboarding.go_to(state, 1)
        request.session.modified = True
        errors = onboarding.errors_for(state, 1) or {
            "name": "The customer couldn't be created. Check the name and try again."
        }
        return _onboarding_page(request, state, errors, status=409)
    # The wizard is done: the next visit, or "Onboard another customer", starts a fresh one.
    request.session.pop(onboarding.SESSION_KEY, None)
    for token in tokens:
        token["command"] = _enroll_command(request, token["token"])
    engagements = shell.engagements_of(customer)
    return render(
        request,
        "engineers/onboarding_done.html",
        {
            "created": customer,
            "summary": onboarding.summary(state),
            "tokens": tokens,
            "token_hours": _token_hours(),
            "overview_url": shell.screen_url(
                "collectors", customer, engagements[0] if engagements else None
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
    context["can_see_audit"] = _can_see_audit(request.user)
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
        },
        customer,
        engagements,
        engagement,
    )


@never_cache
@engineer_required
def sitemap(request, customer, engagement=None):
    """The Site map (DESIGN.md section 5): one site's L2 or L3 map. Its state is in the URL."""
    customer, engagements, engagement = shell.resolve(customer, engagement)
    extra = {}
    if engagement is not None and not customer.inside_access_only:
        extra["view"] = sitemap_screen.SiteMap(customer, engagement, request.GET)
    return render_screen(
        request,
        "sitemap",
        "engineers/sitemap.html",
        extra,
        customer,
        engagements,
        engagement,
        network_data=True,
    )


@require_POST
@csrf_protect
@never_cache
@engineer_required
def sitemap_export(request, customer, engagement):
    """The map as SVG (a PNG is drawn from it in the browser) or its devices as CSV, logged."""
    customer, _, engagement = shell.resolve(customer, engagement)
    # Inside access only: the controller holds no network data to export (ADR-0005).
    if customer.inside_access_only:
        raise Http404
    fmt = request.POST.get("format", "")
    if fmt not in sitemap_screen.EXPORT_FORMATS:
        return HttpResponseBadRequest("Export as png, svg or csv.")
    view = sitemap_screen.SiteMap(customer, engagement, request.POST)
    if view.empty:
        return HttpResponseBadRequest("This site has no devices to export.")
    with transaction.atomic():
        content, name, content_type = sitemap_screen.export(view, request.user.get_username(), fmt)
    response = HttpResponse(content, content_type=content_type)
    response["Content-Disposition"] = f'attachment; filename="{name}"'
    return response


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


FINDING_ACTIONS = {"add", "edit", "include", "exclude", "note", "remove", "restore", "run"}


@require_http_methods(["GET", "POST"])
@csrf_protect
@never_cache
@engineer_required
def findings(request, customer, engagement=None):
    """Findings (DESIGN.md section 7): the checks' results, and the engineer's edits to them."""
    customer, engagements, engagement = shell.resolve(customer, engagement)
    extra = {"severities": list(findings_screen.SEVERITY_LABELS.items())}
    status = 200
    data = None
    if engagement is not None and not customer.inside_access_only:
        found = findings_screen.Findings(customer, engagement)
        here = shell.screen_url("findings", customer, engagement)
        editable = findings_screen.editable(customer, engagement)
        params = request.GET
        if request.method == "POST":
            response, params, error = _finding_post(request, customer, engagement, found, here)
            if response is not None:
                return response
            # The form again, as the engineer filled it, with what to fix.
            found = findings_screen.Findings(customer, engagement)
            extra["error"] = error
            data = request.POST
            # htmx swaps only 2xx responses, so a boosted form gets its errors with a 200.
            status = 200 if request.headers.get("HX-Request") else 400
        screen = findings_screen.Screen(found, params)
        extra.update(
            {
                "found": found,
                "view": screen,
                "here": here,
                "editable": editable,
                # Checks re-run on the stored model, completed engagements included (DESIGN.md).
                "can_run": not customer.is_on_site,
                "form": _finding_form(screen, data),
            }
        )
    elif request.method == "POST":
        raise Http404
    response = render_screen(
        request,
        "findings",
        "engineers/findings.html",
        extra,
        customer,
        engagements,
        engagement,
        network_data=True,
    )
    response.status_code = status
    return response


@never_cache
@engineer_required
def compare(request, customer, engagement=None):
    """Compare (DESIGN.md section 8): two engagements of this customer, and config diffs."""
    customer, engagements, engagement = shell.resolve(customer, engagement)
    extra = {}
    if engagement is not None and not customer.inside_access_only:
        # The pickers name the current engagement; it goes in the path like everywhere else.
        current = request.GET.get("current", "")
        if current:
            picked = next((e for e in engagements if str(e.pk) == current), None)
            if picked is None:
                raise Http404
            query = request.GET.copy()
            del query["current"]
            url = shell.screen_url("compare", customer, picked)
            return redirect(f"{url}?{query.urlencode()}" if query else url)
        extra["view"] = compare_screen.Screen(customer, engagements, engagement, request.GET)
    return render_screen(
        request,
        "compare",
        "engineers/compare.html",
        extra,
        customer,
        engagements,
        engagement,
        network_data=True,
    )


@require_POST
@csrf_protect
@never_cache
@engineer_required
def compare_export(request, customer, engagement):
    """One device's config diff as a .diff file, audit-logged. Masked values only."""
    customer, engagements, engagement = shell.resolve(customer, engagement)
    # On site configs stay in the customer's store (ADR-0002); the controller has none to export.
    if customer.is_on_site:
        raise Http404
    view = compare_screen.Screen(customer, engagements, engagement, request.POST)
    diff = view.comparison.diff_for(request.POST.get("device", "")) if view.comparison else None
    if diff is None:
        return HttpResponseBadRequest("Pick a device whose config changed.")
    with transaction.atomic():
        content, name = compare_screen.export(view.comparison, diff, request.user.get_username())
    response = HttpResponse(content, content_type="text/x-diff; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{name}"'
    return response


@never_cache
@engineer_required
def report(request, customer, engagement=None):
    """The assessment report (DESIGN.md section 9): a preview of its pages, and its export."""
    customer, engagements, engagement = shell.resolve(customer, engagement)
    extra = {"optional": report_screen.OPTIONAL, "severities": findings_screen.SEVERITY_LABELS}
    if engagement is not None and not customer.inside_access_only:
        built = report_screen.Report(customer, engagement, request.GET, _engineer_name(request))
        extra.update(
            {
                "report": built,
                "pages": report_screen.pages(built.blocks()),
                "history": report_screen.history(customer, engagement),
                "findings_url": shell.screen_url("findings", customer, engagement),
            }
        )
    return render_screen(
        request,
        "report",
        "engineers/report.html",
        extra,
        customer,
        engagements,
        engagement,
        network_data=True,
    )


def _engineer_name(request) -> str:
    return request.user.get_full_name() or request.user.get_username()


@require_POST
@csrf_protect
@never_cache
@engineer_required
def report_export(request, customer, engagement):
    """The report as PDF or DOCX, or its findings as CSV, audit-logged."""
    customer, _, engagement = shell.resolve(customer, engagement)
    # On site reports are built and saved on site (ADR-0005), which the collector can't do yet.
    if customer.is_on_site:
        raise Http404
    fmt = request.POST.get("format", "")
    if fmt not in report_screen.FORMATS:
        return HttpResponseBadRequest("Export as pdf, docx or csv.")
    built = report_screen.Report(customer, engagement, request.POST, _engineer_name(request))
    with transaction.atomic():
        content, name = report_screen.export(built, request.user.get_username(), fmt)
    response = HttpResponse(content, content_type=report_screen.FORMATS[fmt])
    response["Content-Disposition"] = f'attachment; filename="{name}"'
    return response


def _finding_post(request, customer, engagement, found, here):
    """Apply one change. Returns a redirect, or the params and error to show the form with."""
    action = request.POST.get("action", "")
    if action not in FINDING_ACTIONS:
        return HttpResponseBadRequest("Unknown action."), None, ""
    actor = request.user.get_username()
    if action == "run":
        try:
            assessments.run_assessment(engagement, started_by=actor)
        except (assessments.AssessmentNotAllowed, assessments.OnSiteDataError) as error:
            messages.error(request, str(error))
        else:
            messages.success(request, "Checks ran · logged in the audit log")
        return redirect(here), None, ""
    if not findings_screen.editable(customer, engagement):
        raise PermissionDenied
    key = request.POST.get("finding", "")
    item = None
    if action != "add":
        item = found.get(key)
        if item is None:
            raise Http404
    try:
        if action == "add":
            row = findings_screen.add(found, request.POST, actor)
            key, done = str(row.pk), "Finding added"
        elif action == "edit":
            changed = findings_screen.edit(found, item, request.POST, actor)
            done = "Finding updated" if changed else ""
        elif action in ("include", "exclude"):
            findings_screen.set_included(found, item, action == "include", actor)
            done = "Included in the report" if action == "include" else "Left out of the report"
        elif action == "note":
            changed = findings_screen.set_note(found, item, request.POST.get("note", ""), actor)
            done = "Note saved" if changed else ""
        elif action == "remove":
            findings_screen.remove(found, item, request.POST.get("reason", ""), actor)
            done = "Finding deleted" if item.manual else "Finding dismissed"
            key = ""
        else:
            findings_screen.restore(found, item, actor)
            done = "Finding restored"
    except findings_screen.EditError as error:
        params = {"finding": key}
        params["mode" if action in ("add", "edit") else "remove"] = (
            action if action in ("add", "edit") else "1"
        )
        return None, params, str(error)
    if done:
        messages.success(request, f"{done} · logged in the audit log")
    return redirect(here + (f"?finding={key}" if key else "")), None, ""


def _finding_form(screen, data) -> dict:
    """The add or edit form's values: as posted, else the finding's, else blank."""
    item = screen.selected if screen.mode == "edit" else None
    if data is not None:
        return {
            "title": data.get("title", ""),
            "severity": data.get("severity", ""),
            "severity_reason": data.get("severity_reason", ""),
            "check_id": data.get("check_id", ""),
            "devices": set(data.getlist("devices")),
            "evidence": data.get("evidence", ""),
            "recommendation": data.get("recommendation", ""),
            "suggested_config": data.get("suggested_config", ""),
            "include_in_report": data.get("include_in_report") == "on",
            "reason": data.get("reason", ""),
        }
    if item is None:
        return {"severity": "medium", "devices": set(), "include_in_report": True}
    return {
        "title": item.title,
        "severity": item.severity,
        "severity_reason": item.severity_reason,
        "check_id": item.check_id,
        "devices": {str(a.device.pk) for a in item.devices},
        "evidence": item.evidence,
        "recommendation": item.recommendation,
        "suggested_config": item.suggested_config,
        "include_in_report": item.include_in_report,
    }


JOBS_SHOWN = 100


def _can_see_audit(user) -> bool:
    # The same permission the audit log API asks for.
    return user.has_perm("collectors.view_auditevent")


def _uuid(text: str):
    try:
        return uuid.UUID(text)
    except (TypeError, ValueError):
        return None


def _enroll_command(request, token: str) -> str:
    controller = request.build_absolute_uri("/").rstrip("/")
    return f"uv run cartio-collector enroll --controller {controller} --token {token}"


@require_http_methods(["GET", "POST"])
@csrf_protect
@never_cache
@engineer_required
def collectors(request, customer, engagement=None):
    """Collectors, jobs and audit (DESIGN.md section 4): the Collectors tab, add and revoke."""
    customer, engagements, engagement = shell.resolve(customer, engagement)
    here = shell.screen_url("collectors", customer, engagement)
    can_add = request.user.has_perm("collectors.add_collector") and not customer.is_archived
    can_revoke = request.user.has_perm("collectors.change_collector")
    extra = {"can_add": can_add, "can_revoke": can_revoke, "here": here, "status": 200}

    if request.method == "POST":
        action = request.POST.get("action", "")
        if action == "revoke":
            if not can_revoke:
                raise PermissionDenied
            collector_id = _uuid(request.POST.get("collector", ""))
            revoked = collector_id and operations.revoke(
                customer, collector_id, request.user.get_username()
            )
            if revoked:
                messages.success(request, f"{revoked.name} revoked · logged in the audit log")
            return redirect(here)
        if action != "add":
            return HttpResponseBadRequest("Add or revoke a collector.")
        if not can_add:
            raise PermissionDenied
        name = request.POST.get("name", "").strip()[: operations.MAX_NAME + 1]
        site_id = request.POST.get("site", "").strip()
        errors = operations.add_errors(customer, name, site_id)
        if not errors:
            site = operations.site_from(customer, site_id) if site_id else None
            try:
                collector, token = operations.add(customer, name, site, request.user.get_username())
            except IntegrityError:
                # Most likely another engineer added a collector with this name meanwhile.
                errors = {"name": "This customer already has a collector with this name."}
            else:
                collector.health = collector_health(collector)
                # The token is only ever in this response: only its hash is stored.
                extra.update(
                    {"added": collector, "command": _enroll_command(request, token), "status": 201}
                )
        if errors:
            extra.update(
                {"adding": True, "errors": errors, "form": {"name": name, "site": site_id}}
            )
            extra["status"] = 400
    else:
        extra["adding"] = can_add and "add" in request.GET
        extra["form"] = {"name": "", "site": ""}
        if can_revoke:
            confirm = _uuid(request.GET.get("revoke", ""))
            extra["confirm"] = confirm and operations.find_collector(customer, confirm)

    rows = _collectors(customer, revoked=True)
    open_jobs = dict(
        Job.objects.for_customer(customer)
        .filter(status__in=Job.OPEN)
        .values_list("collector")
        .annotate(count=Count("pk"))
    )
    for row in rows:
        row.queue = open_jobs.get(row.pk, 0)
    extra.update(
        {
            "collectors": rows,
            "offline": [c for c in rows if c.health == "offline"],
            "sites": operations.sites(customer),
            "online_within_minutes": int(ONLINE_WITHIN.total_seconds() // 60),
            "token_hours": _token_hours(),
        }
    )
    response = render_screen(
        request,
        "collectors",
        "engineers/collectors.html",
        extra,
        customer,
        engagements,
        engagement,
    )
    response.status_code = extra["status"]
    return response


@never_cache
@engineer_required
def collector_status(request, customer, collector):
    """A new collector's state while the engineer waits for it to enroll (an htmx poll)."""
    customer, _, _ = shell.resolve(customer)
    found = operations.find_collector(customer, collector)
    if found is None:
        raise Http404
    found.health = collector_health(found)
    return render(request, "engineers/_collector_waiting.html", {"collector": found})


@require_http_methods(["GET", "POST"])
@csrf_protect
@never_cache
@engineer_required
def jobs(request, customer, engagement=None):
    # The same permission the runs API asks for.
    if not _can_see_jobs(request.user):
        raise PermissionDenied
    customer, engagements, engagement = shell.resolve(customer, engagement)
    can_cancel = request.user.has_perm("collectors.change_job")
    if request.method == "POST":
        if request.POST.get("action") != "cancel":
            return HttpResponseBadRequest("Cancel a job.")
        if not can_cancel:
            raise PermissionDenied
        job_id = _uuid(request.POST.get("job", ""))
        job = job_id and Job.objects.for_customer(customer).filter(pk=job_id).first()
        if not job:
            raise Http404
        try:
            job_channel.cancel_job(job, request.user.get_username())
        except job_channel.JobClosed as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "Job cancelled · logged in the audit log")
        return redirect(shell.screen_url("jobs", customer, engagement))
    runs = (
        CollectionRun.objects.for_customer(customer)
        .select_related("collector", "engagement", "result", "job")
        .order_by("-started_at")[:JOBS_SHOWN]
    )
    return render_screen(
        request,
        "jobs",
        "engineers/jobs.html",
        {"runs": runs, "limit": JOBS_SHOWN, "can_cancel": can_cancel},
        customer,
        engagements,
        engagement,
    )


@never_cache
@engineer_required
def audit_log(request, customer, engagement=None):
    if not _can_see_audit(request.user):
        raise PermissionDenied
    customer, engagements, engagement = shell.resolve(customer, engagement)
    kind = request.GET.get("kind", "")
    if kind not in operations.AUDIT_KINDS:
        kind = ""
    here = shell.screen_url("audit", customer, engagement)
    filters = [{"key": "", "label": "All", "url": here}] + [
        {"key": key, "label": label, "url": f"{here}?kind={key}"}
        for key, (label, _) in operations.AUDIT_KINDS.items()
    ]
    return render_screen(
        request,
        "audit",
        "engineers/audit.html",
        {
            "events": operations.audit_events(customer, kind),
            "kind": kind,
            "filters": filters,
            "limit": operations.AUDIT_SHOWN,
        },
        customer,
        engagements,
        engagement,
    )


@require_POST
@csrf_protect
@never_cache
@engineer_required
def audit_export(request, customer):
    """The customer's whole audit log as CSV. The export is itself logged first."""
    if not _can_see_audit(request.user):
        raise PermissionDenied
    customer, _, _ = shell.resolve(customer)
    chunks = audit.export_events(customer, request.user.get_username(), "csv")
    response = StreamingHttpResponse(
        audit.streamed(request, chunks), content_type="text/csv; charset=utf-8"
    )
    response["Content-Disposition"] = (
        f'attachment; filename="cartio-audit-customer-{customer.pk}.csv"'
    )
    return response


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


def _small_pk(text: str) -> int | None:
    return int(text) if text.isdecimal() and len(text) <= 18 else None


@never_cache
@engineer_required
def search(request):
    """Global search results (DESIGN.md section 11), an htmx partial for the search dialog.

    The page's customer and engagement come along, for the "only this customer" scope and so
    links to screens keep the engineer's context. An unknown or archived one is ignored.
    """
    current = engagement = None
    customer_pk = _small_pk(request.GET.get("customer", ""))
    if customer_pk is not None:
        try:
            current, engagements, engagement = shell.resolve(customer_pk)
        except Http404:
            current = None
        if current is not None and current.is_archived:
            current = engagement = None
        wanted = _small_pk(request.GET.get("engagement", ""))
        if current is not None and wanted is not None:
            engagement = next((e for e in engagements if e.pk == wanted), engagement)
    results = global_search.Search(
        request.user,
        request.GET.get("q", ""),
        current,
        engagement,
        only_current=request.GET.get("scope") == "current",
    )
    return render(request, "engineers/_search_results.html", {"search": results})
