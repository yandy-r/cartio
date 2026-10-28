"""Engineer sign-in with a second factor, and the UI shell: customers, collectors and jobs.

Signing in takes two steps. The password step only remembers who is signing in; the session
becomes signed in (and the sign-in is logged) after a valid authenticator code. An engineer
without an authenticator yet adds one at that point, with the one-time enrollment code an admin
issued (`cartio-controller enroll_engineer`), before the first sign-in completes.
"""

import time
from datetime import timedelta
from functools import wraps

from django.contrib import auth
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.signals import user_login_failed
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_POST

from cartio_controller.collectors.models import (
    CollectionRun,
    Collector,
    Customer,
    Engagement,
    Site,
)

from . import totp
from .models import EngineerMFA

PENDING = "cartio_pending_sign_in"
# The second step must follow the password soon after, with a few tries at most.
PENDING_SECONDS = 300
MAX_CODE_TRIES = 5

# Collectors check in every minute by default. Three missed check-ins and it shows as offline.
ONLINE_WITHIN = timedelta(minutes=3)


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
    if user is None or not _is_engineer(user):
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
    if mfa.record_failure() - request.session[PENDING]["failed_before"] >= MAX_CODE_TRIES:
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


def collector_health(collector, now=None) -> str:
    if collector.enrolled_at is None:
        return "not enrolled"
    if collector.last_check_in is None:
        return "never checked in"
    now = now or timezone.now()
    return "online" if now - collector.last_check_in <= ONLINE_WITHIN else "offline"


def _collectors(customer):
    collectors = list(Collector.objects.for_customer(customer).order_by("name"))
    now = timezone.now()
    for collector in collectors:
        collector.health = collector_health(collector, now)
    return collectors


@never_cache
@engineer_required
def customers(request):
    show_archived = request.GET.get("archived") == "1"
    online = Q(
        collectors__enrolled_at__isnull=False,
        collectors__last_check_in__gte=timezone.now() - ONLINE_WITHIN,
    )
    rows = Customer.objects.annotate(
        collector_count=Count("collectors", distinct=True),
        online_count=Count("collectors", filter=online, distinct=True),
    )
    if not show_archived:
        rows = rows.filter(archived_at__isnull=True)
    return render(
        request,
        "engineers/customers.html",
        {"customers": rows, "show_archived": show_archived},
    )


def _can_see_jobs(user) -> bool:
    return user.has_perm("collectors.view_collectionrun")


@never_cache
@engineer_required
def customer_detail(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    return render(
        request,
        "engineers/customer_detail.html",
        {
            "customer": customer,
            "engagements": Engagement.objects.for_customer(customer).filter(
                archived_at__isnull=True
            ),
            "sites": Site.objects.for_customer(customer).filter(archived_at__isnull=True),
            "collectors": _collectors(customer),
            "can_see_jobs": _can_see_jobs(request.user),
            "online_within_minutes": int(ONLINE_WITHIN.total_seconds() // 60),
        },
    )


JOBS_SHOWN = 100


@never_cache
@engineer_required
def jobs(request, pk):
    # The same permission the runs API asks for.
    if not _can_see_jobs(request.user):
        raise PermissionDenied
    customer = get_object_or_404(Customer, pk=pk)
    runs = (
        CollectionRun.objects.for_customer(customer)
        .select_related("collector", "engagement", "result")
        .order_by("-started_at")[:JOBS_SHOWN]
    )
    return render(
        request,
        "engineers/jobs.html",
        {"customer": customer, "runs": runs, "limit": JOBS_SHOWN},
    )
