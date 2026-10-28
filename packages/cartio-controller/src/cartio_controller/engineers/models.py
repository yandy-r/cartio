import hashlib
import hmac
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import models, transaction
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables

from cartio_controller.collectors import vault

from . import totp


def _hash(value: str) -> str:
    # Enrollment codes are 16 random bytes, so a plain SHA-256 is enough to store them.
    return hashlib.sha256(value.encode()).hexdigest()


class EngineerMFA(models.Model):
    """An engineer's authenticator (TOTP) key, the second factor of every sign-in.

    Engineers aren't customer-owned: one engineer works across every customer. The key is stored
    encrypted with the controller's master key, so a database dump alone can't mint codes.

    An admin starts enrollment with `cartio-controller enroll_engineer`, which hands out a
    one-time enrollment code. A password alone never adds an authenticator.
    """

    ENROLLMENT_VALID = timedelta(hours=24)

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="mfa"
    )
    secret_encrypted = models.BinaryField(editable=False)
    master_key_id = models.CharField(max_length=16, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    # Empty until the engineer proves their app works by entering a code from it.
    confirmed_at = models.DateTimeField(null=True, blank=True)
    # The newest time step accepted, so a code can't be used twice.
    last_used_step = models.BigIntegerField(default=0, editable=False)
    # Only the hash of the one-time enrollment code. Cleared once the authenticator is confirmed.
    enrollment_token_hash = models.CharField(max_length=64, blank=True, editable=False)
    enrollment_expires_at = models.DateTimeField(null=True, blank=True, editable=False)
    # Wrong codes since the last good one. Counted in the database, so parallel tries all count.
    failed_codes = models.PositiveIntegerField(default=0, editable=False)

    class Meta:
        verbose_name = "engineer MFA"
        verbose_name_plural = "engineer MFA"

    def __str__(self) -> str:
        return f"MFA for {self.user}"

    def __repr__(self) -> str:
        return f"<EngineerMFA user={self.user_id} confirmed={self.confirmed_at is not None}>"

    @classmethod
    def issue(cls, user) -> tuple["EngineerMFA", str]:
        """Start (or restart) enrollment: a new key, and a one-time code to unlock it."""
        token = secrets.token_hex(16)
        with transaction.atomic():
            cls.objects.filter(user=user).delete()
            mfa = cls(
                user=user,
                enrollment_token_hash=_hash(token),
                enrollment_expires_at=timezone.now() + cls.ENROLLMENT_VALID,
            )
            mfa.set_secret(totp.new_secret())
            mfa.save()
        return mfa, token

    @property
    def enrollment_open(self) -> bool:
        return (
            self.confirmed_at is None
            and bool(self.enrollment_token_hash)
            and self.enrollment_expires_at is not None
            and timezone.now() < self.enrollment_expires_at
        )

    @sensitive_variables("token")
    def check_enrollment_token(self, token: str) -> bool:
        return self.enrollment_open and hmac.compare_digest(
            self.enrollment_token_hash, _hash(token.strip())
        )

    def record_failure(self) -> int:
        """Count a wrong code and return the new total."""
        EngineerMFA.objects.filter(pk=self.pk).update(failed_codes=models.F("failed_codes") + 1)
        self.refresh_from_db(fields=["failed_codes"])
        return self.failed_codes

    def _context(self) -> bytes:
        return f"cartio engineer totp:{self.user_id}".encode()

    @sensitive_variables("secret")
    def set_secret(self, secret: bytes) -> None:
        key = vault.master_key()
        self.secret_encrypted = key.wrap(secret, self._context())
        self.master_key_id = key.key_id

    @property
    def secret(self) -> bytes:
        key = vault.master_key_for(self.master_key_id)
        return key.unwrap(self.secret_encrypted, self._context())

    @sensitive_variables("code")
    def use_code(self, code: str) -> bool:
        """Check a code and mark it used. A code works once, even under concurrent sign-ins."""
        step = totp.matching_step(self.secret, code)
        if step is None or step <= self.last_used_step:
            return False
        # Conditional, so two requests racing with the same code can't both win.
        won = (
            EngineerMFA.objects.filter(pk=self.pk, last_used_step__lt=step).update(
                last_used_step=step, failed_codes=0
            )
            == 1
        )
        if won:
            self.last_used_step = step
        return won
