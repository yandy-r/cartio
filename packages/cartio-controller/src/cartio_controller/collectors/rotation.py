"""Rotating the master key (YAN-1722).

Only the small things the master key wraps change: each customer's data key and each engineer's
authenticator key. Credentials stay encrypted with their customer's data key, so none of them is
rewritten or re-entered.

Each key is rewrapped in its own transaction, under a row lock, and checked to open with the new
key before it's saved. A key is never left half done: it's either still wrapped by the old key or
already wrapped by the new one, and the controller opens both while the rotation runs. So a
rotation that stops partway is finished by running it again.
"""

from dataclasses import dataclass

from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.views.decorators.debug import sensitive_variables

from cartio_controller.engineers.models import EngineerMFA

from . import audit, vault
from .models import AuditEvent, CustomerDataKey


class RotationError(Exception):
    """The rotation can't start, or stopped. Whatever was rotated before it stays rotated."""


@dataclass
class RotationResult:
    data_keys: int = 0
    engineer_keys: int = 0


def keys_to_rotate() -> tuple[vault.MasterKey, vault.MasterKey]:
    """The (old, new) master keys, after checking every stored key is wrapped by one of them."""
    new = vault.master_key()
    old = vault.previous_master_key()
    if old is None:
        raise RotationError(
            "Set CARTIO_PREVIOUS_MASTER_KEY_FILE to the old master key, and CARTIO_MASTER_KEY_FILE "
            "to the new one."
        )
    if old.key_id == new.key_id:
        raise RotationError("The previous and the current master key are the same key.")
    known = {old.key_id, new.key_id}
    unknown = set(
        CustomerDataKey.all_customers.exclude(master_key_id__in=known).values_list(
            "master_key_id", flat=True
        )
    ) | set(
        EngineerMFA.objects.exclude(master_key_id__in=known).values_list("master_key_id", flat=True)
    )
    if unknown:
        # Rewrapping the rest would hide that these keys are lost. Stop before changing anything.
        raise RotationError(
            "Some keys are wrapped by neither master key: " + ", ".join(sorted(unknown)) + ". "
            "Find that key file first."
        )
    return old, new


@sensitive_variables("data_key")
def _rewrap(old: vault.MasterKey, new: vault.MasterKey, wrapped: bytes, context: bytes) -> bytes:
    try:
        data_key = old.unwrap(wrapped, context)
    except vault.VaultError as exc:
        raise RotationError("A key doesn't open with the previous master key.") from exc
    rewrapped = new.wrap(data_key, context)
    if new.unwrap(rewrapped, context) != data_key:
        raise RotationError("A rewrapped key didn't open with the new master key.")
    return rewrapped


def _rotate_data_key(old, new, pk, actor: str) -> bool:
    with transaction.atomic():
        record = (
            CustomerDataKey.all_customers.select_for_update().select_related("customer").get(pk=pk)
        )
        if record.master_key_id != old.key_id:
            return False  # Another run got here first.
        context = CustomerDataKey._context(record.customer_id)
        record.wrapped_key = _rewrap(old, new, record.wrapped_key, context)
        record.master_key_id = new.key_id
        record.save(update_fields=["wrapped_key", "master_key_id"])
        audit.record(
            record.customer_id,
            AuditEvent.Action.MASTER_KEY_ROTATED,
            actor=actor,
            object_type="customer",
            object_id=record.customer_id,
            object_name=record.customer.name,
            old_master_key_id=old.key_id,
            new_master_key_id=new.key_id,
        )
    return True


def _rotate_engineer_key(old, new, pk) -> bool:
    with transaction.atomic():
        mfa = EngineerMFA.objects.select_for_update().get(pk=pk)
        if mfa.master_key_id != old.key_id:
            return False
        mfa.secret_encrypted = _rewrap(old, new, mfa.secret_encrypted, mfa._context())
        mfa.master_key_id = new.key_id
        mfa.save(update_fields=["secret_encrypted", "master_key_id"])
    return True


def rotate(actor: str) -> RotationResult:
    """Rewrap everything the previous master key wraps with the current one.

    Safe to run again after it stops: keys already on the new master key are skipped.
    """
    try:
        old, new = keys_to_rotate()
    except ImproperlyConfigured as exc:
        raise RotationError(str(exc)) from exc
    result = RotationResult()
    pending = CustomerDataKey.all_customers.filter(master_key_id=old.key_id)
    for pk in list(pending.order_by("pk").values_list("pk", flat=True)):
        if _rotate_data_key(old, new, pk, actor):
            result.data_keys += 1
    engineers = EngineerMFA.objects.filter(master_key_id=old.key_id)
    for pk in list(engineers.order_by("pk").values_list("pk", flat=True)):
        if _rotate_engineer_key(old, new, pk):
            result.engineer_keys += 1
    return result


def remaining() -> int:
    """How many stored keys aren't wrapped by the current master key yet."""
    current = vault.master_key().key_id
    return (
        CustomerDataKey.all_customers.exclude(master_key_id=current).count()
        + EngineerMFA.objects.exclude(master_key_id=current).count()
    )
