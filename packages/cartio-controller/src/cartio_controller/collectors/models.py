import hashlib
import hmac
import secrets
import uuid

from django.db import models


def _hash(value: str) -> str:
    # Tokens and secrets are 32 random bytes, so a plain SHA-256 is enough to store them.
    return hashlib.sha256(value.encode()).hexdigest()


class Customer(models.Model):
    """An organization we assess. No data, collector or credential crosses customers."""

    name = models.CharField(max_length=200, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Collector(models.Model):
    """An agent running in or near one customer's network."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="collectors")
    name = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)

    # Set when the collector is created, cleared when it enrolls.
    enrollment_token_hash = models.CharField(max_length=64, blank=True, db_index=True)
    enrolled_at = models.DateTimeField(null=True, blank=True)
    secret_hash = models.CharField(max_length=64, blank=True)

    hostname = models.CharField(max_length=255, blank=True)
    version = models.CharField(max_length=50, blank=True)
    platform = models.CharField(max_length=200, blank=True)
    last_check_in = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["customer__name", "name"]
        constraints = [
            models.UniqueConstraint(fields=["customer", "name"], name="unique_collector_name"),
        ]

    def __str__(self) -> str:
        return f"{self.customer} / {self.name}"

    def issue_enrollment_token(self) -> str:
        """Return a new one-time enrollment token. Only its hash is stored."""
        # Hex, so the token never starts with "-" and is safe to pass on a command line.
        token = secrets.token_hex(32)
        self.enrollment_token_hash = _hash(token)
        return token

    def issue_secret(self) -> str:
        """Return a new collector secret. Only its hash is stored."""
        secret = secrets.token_urlsafe(32)
        self.secret_hash = _hash(secret)
        return secret

    def check_secret(self, secret: str) -> bool:
        return bool(self.secret_hash) and hmac.compare_digest(self.secret_hash, _hash(secret))

    @classmethod
    def for_enrollment_token(cls, token: str) -> "Collector | None":
        return cls.objects.filter(enrollment_token_hash=_hash(token)).first()


class CheckIn(models.Model):
    """One check-in from a collector. Kept as a record of when each collector was seen."""

    collector = models.ForeignKey(Collector, on_delete=models.CASCADE, related_name="check_ins")
    at = models.DateTimeField(auto_now_add=True)
    hostname = models.CharField(max_length=255)
    version = models.CharField(max_length=50)
    platform = models.CharField(max_length=200)

    class Meta:
        ordering = ["-at"]
        get_latest_by = "at"
