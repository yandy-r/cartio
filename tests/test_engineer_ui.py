"""Engineer sign-in with an authenticator code, and the UI shell (YAN-1655)."""

from datetime import timedelta

import pytest
from cartio_controller.collectors.models import (
    CollectionRun,
    CollectionRunResult,
    Collector,
    Customer,
    LoginEvent,
)
from cartio_controller.engineers import totp, views
from cartio_controller.engineers.models import EngineerMFA
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.management import CommandError, call_command
from django.utils import timezone

pytestmark = pytest.mark.django_db

PASSWORD = "correct horse battery"


def _engineer(username="engineer", **extra):
    extra.setdefault("is_staff", True)
    return get_user_model().objects.create_user(username, password=PASSWORD, **extra)


def _code(mfa, offset=0):
    return totp.code_at(mfa.secret, totp.step_at() + offset)


def _password(client, username="engineer", password=PASSWORD, next_url=""):
    return client.post("/sign-in", {"username": username, "password": password, "next": next_url})


def _enroll(client, user, next_url=""):
    """An admin issues an enrollment code; the engineer signs in with it and adds the app."""
    _, token = EngineerMFA.issue(user)
    assert _password(client, user.get_username(), next_url=next_url).url == "/sign-in/authenticator"
    assert client.post("/sign-in/authenticator", {"enrollment_code": token}).status_code == 302
    return EngineerMFA.objects.get(user=user)


def _signed_in(client, user):
    """Sign in all the way: password, then a code (adding the authenticator first if needed)."""
    mfa = EngineerMFA.objects.filter(user=user, confirmed_at__isnull=False).first()
    if mfa is None:
        mfa = _enroll(client, user)
        return client.post("/sign-in/authenticator", {"code": _code(mfa)})
    _password(client, user.get_username())
    return client.post("/sign-in/code", {"code": _code(mfa)})


def _kinds():
    return list(LoginEvent.objects.order_by("at", "pk").values_list("kind", flat=True))


def test_totp_matches_rfc_6238():
    # RFC 6238 appendix B, SHA-1, T = 59 s: 94287082, of which a 6-digit code is the tail.
    secret = b"12345678901234567890"
    assert totp.code_at(secret, totp.step_at(59)) == "287082"
    assert totp.matching_step(secret, "287 082", when=59) == 1
    assert totp.matching_step(secret, "287083", when=59) is None
    assert totp.matching_step(secret, "28708", when=59) is None


def test_first_sign_in_adds_an_authenticator_and_lands_on_customers(client):
    user = _engineer()
    Customer.objects.create(name="Acme")
    _, token = EngineerMFA.issue(user)

    response = _password(client)
    assert response.url == "/sign-in/authenticator"
    # The password alone signs nobody in, and doesn't reveal the authenticator key.
    assert client.get("/").status_code == 302
    assert client.get("/api/v1/customers").status_code == 403
    mfa = EngineerMFA.objects.get(user=user)
    assert totp.to_base32(mfa.secret)[:8] not in client.get(response.url).content.decode()
    assert client.post(response.url, {"enrollment_code": "0" * 32}).status_code == 200
    assert _kinds() == [LoginEvent.Kind.FAILED]

    client.post(response.url, {"enrollment_code": token})
    page = client.get("/sign-in/authenticator").content.decode()
    assert totp.to_base32(mfa.secret)[:4] in page
    # Stored encrypted, never as the raw key.
    assert mfa.secret not in bytes(mfa.secret_encrypted)

    response = client.post("/sign-in/authenticator", {"code": _code(mfa)})
    assert response.url == "/"
    assert "Acme" in client.get("/").content.decode()
    mfa.refresh_from_db()
    assert mfa.confirmed_at is not None
    assert not mfa.check_enrollment_token(token)
    assert _kinds() == [LoginEvent.Kind.FAILED, LoginEvent.Kind.LOGGED_IN]


def test_a_password_alone_cant_add_an_authenticator(client):
    user = _engineer()
    response = _password(client)
    page = client.get(response.url)
    assert "enroll_engineer" in page.content.decode()
    assert not EngineerMFA.objects.filter(user=user).exists()
    # An expired enrollment code doesn't open enrollment either.
    _, token = EngineerMFA.issue(user)
    EngineerMFA.objects.filter(user=user).update(enrollment_expires_at=timezone.now())
    _password(client)
    page = client.post("/sign-in/authenticator", {"enrollment_code": token})
    assert "enroll_engineer" in page.content.decode()
    assert client.get("/").status_code == 302


def test_enroll_engineer_command_replaces_only_with_reset(capsys):
    user = _engineer()
    call_command("enroll_engineer", "engineer")
    assert "Enrollment code for engineer" in capsys.readouterr().out
    EngineerMFA.objects.filter(user=user).update(confirmed_at=timezone.now())
    with pytest.raises(CommandError, match="--reset"):
        call_command("enroll_engineer", "engineer")
    call_command("enroll_engineer", "engineer", "--reset")
    assert EngineerMFA.objects.get(user=user).confirmed_at is None


def test_later_sign_ins_need_a_fresh_code(client):
    user = _engineer()
    _signed_in(client, user)
    client.post("/sign-out")
    assert _kinds() == [LoginEvent.Kind.LOGGED_IN, LoginEvent.Kind.LOGGED_OUT]
    mfa = EngineerMFA.objects.get(user=user)

    assert _password(client).url == "/sign-in/code"
    # The authenticator can't be swapped once confirmed.
    assert client.get("/sign-in/authenticator").url == "/sign-in/code"
    # The code that signed in last time is used up.
    used = totp.code_at(mfa.secret, mfa.last_used_step)
    assert client.post("/sign-in/code", {"code": used}).status_code == 200
    assert client.get("/").status_code == 302

    response = client.post("/sign-in/code", {"code": _code(mfa, offset=1)})
    assert response.url == "/"
    assert client.get("/").status_code == 200


def test_wrong_codes_are_logged_and_end_the_attempt(client):
    user = _engineer()
    _signed_in(client, user)
    client.post("/sign-out")
    _password(client)

    for _ in range(views.MAX_CODE_TRIES - 1):
        assert client.post("/sign-in/code", {"code": "000000"}).status_code == 200
    assert client.post("/sign-in/code", {"code": "000000"}).url == "/sign-in"
    # Starting over takes the password again, even with a right code.
    mfa = EngineerMFA.objects.get(user=user)
    assert client.post("/sign-in/code", {"code": _code(mfa, 1)}).url == "/sign-in"
    assert client.get("/").status_code == 302
    failed = LoginEvent.objects.filter(kind=LoginEvent.Kind.FAILED)
    assert failed.count() == views.MAX_CODE_TRIES
    assert set(failed.values_list("username", flat=True)) == {"engineer"}


def test_odd_codes_are_refused_not_errors(client):
    user = _engineer()
    _signed_in(client, user)
    client.post("/sign-out")
    _password(client)
    for code in ("١٢٣٤٥٦", "²²²²²²", "abcdef", ""):
        assert client.post("/sign-in/code", {"code": code}).status_code == 200


def test_the_code_step_expires(client):
    user = _engineer()
    _signed_in(client, user)
    client.post("/sign-out")
    _password(client)
    session = client.session
    session[views.PENDING]["at"] -= views.PENDING_SECONDS + 1
    session.save()
    mfa = EngineerMFA.objects.get(user=user)
    assert client.post("/sign-in/code", {"code": _code(mfa, 1)}).url == "/sign-in"


def test_only_staff_accounts_sign_in(client):
    _engineer("customer-user", is_staff=False)
    right = _password(client, "customer-user").content.decode()
    wrong = _password(client, "customer-user", password="nope").content.decode()
    # The same answer either way, so it doesn't confirm the password.
    message = "Please enter a correct username and password"
    assert message in right and message in wrong
    assert client.get("/sign-in/authenticator").url == "/sign-in"
    assert _kinds() == [LoginEvent.Kind.FAILED, LoginEvent.Kind.FAILED]


def test_admin_sign_in_goes_through_the_code(client):
    response = client.get("/admin/login/?next=/admin/")
    assert response.status_code == 302
    assert response.url == "/sign-in?next=/admin/"

    user = _engineer(is_superuser=True)
    mfa = _enroll(client, user, next_url="/admin/")
    assert client.post("/sign-in/authenticator", {"code": _code(mfa)}).url == "/admin/"


def test_sign_in_never_redirects_off_site(client):
    mfa = _enroll(client, _engineer(), next_url="https://evil.example/")
    assert client.post("/sign-in/authenticator", {"code": _code(mfa)}).url == "/"


def test_sessions_expire():
    assert settings.SESSION_COOKIE_AGE == 8 * 3600
    assert settings.SESSION_EXPIRE_AT_BROWSER_CLOSE


def test_customer_page_shows_collector_health(client):
    acme = Customer.objects.create(name="Acme")
    now = timezone.now()
    collectors = Collector.objects.for_customer(acme)
    collectors.create(name="vm01", enrolled_at=now, last_check_in=now, version="0.1.0")
    collectors.create(
        name="vm02", enrolled_at=now, last_check_in=now - timedelta(hours=1), version="0.0.9"
    )
    collectors.create(name="vm03")
    _signed_in(client, _engineer())

    assert "1 of 3" in client.get("/").content.decode()
    page = client.get(f"/customers/{acme.pk}").content.decode()
    for text in ("vm01", "online", "0.1.0", "vm02", "offline", "0.0.9", "not enrolled"):
        assert text in page


def test_jobs_page_needs_the_runs_permission(client):
    acme = Customer.objects.create(name="Acme")
    collector = Collector.objects.for_customer(acme).create(name="vm01")
    run = CollectionRun.objects.for_customer(acme).create(
        collector=collector, job_type="discovery", started_by="engineer"
    )
    CollectionRunResult.objects.for_customer(acme).create(
        run=run, outcome=CollectionRunResult.Outcome.SUCCEEDED, device_count=12
    )
    user = _engineer()
    _signed_in(client, user)
    assert client.get(f"/customers/{acme.pk}/jobs").status_code == 403

    user.user_permissions.add(Permission.objects.get(codename="view_collectionrun"))
    page = client.get(f"/customers/{acme.pk}/jobs").content.decode()
    assert "discovery" in page and "Succeeded" in page and "12 devices" in page


def test_shell_pages_need_sign_in(client):
    acme = Customer.objects.create(name="Acme")
    for url in ("/", f"/customers/{acme.pk}", f"/customers/{acme.pk}/jobs"):
        response = client.get(url)
        assert response.status_code == 302
        assert response.url.startswith("/sign-in?next=")
