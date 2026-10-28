"""Storing device credentials and delivering them to collectors (ADR-0003, Hosted mode).

Every change and every delivery writes an audit event in the same transaction, so there is no
credential change without a record of who made it.
"""

import uuid

from django.db import transaction
from django.views.decorators.debug import sensitive_variables

from . import audit
from .models import AuditEvent, Collector, Credential, Customer


class CredentialNotAvailable(Exception):
    """The collector may not have this credential: unknown, another customer's, or not enrolled."""


def _audit(credential: Credential, action: str, actor: str = "", collector=None, **detail):
    audit.record(
        credential.customer_id,
        action,
        actor=actor,
        collector=collector,
        object_type="credential",
        object_id=credential.pk,
        object_name=credential.name,
        kind=credential.kind,
        **detail,
    )


@sensitive_variables("secret")
def create_credential(customer: Customer, actor: str, secret: dict, **fields) -> Credential:
    with transaction.atomic():
        credential = Credential(customer=customer, **fields)
        credential.set_secret(secret)
        credential.save()
        _audit(credential, AuditEvent.Action.CREDENTIAL_CREATED, actor)
    return credential


@sensitive_variables("secret")
def update_credential(
    credential: Credential, actor: str, secret: dict | None = None, **fields
) -> Credential:
    """Change a credential's settings, and replace its secret when one is given."""
    with transaction.atomic():
        # Lock the row, so a concurrent change can't be overwritten by this one's stale copy.
        credential = (
            Credential.objects.for_customer(credential.customer_id)
            .select_for_update()
            .get(pk=credential.pk)
        )
        for name, value in fields.items():
            setattr(credential, name, value)
        if secret is not None:
            credential.set_secret(secret)
        credential.save()
        _audit(
            credential,
            AuditEvent.Action.CREDENTIAL_UPDATED,
            actor,
            fields=sorted(fields),
            secret_replaced=secret is not None,
        )
    return credential


def delete_credential(credential: Credential, actor: str) -> None:
    with transaction.atomic():
        _audit(credential, AuditEvent.Action.CREDENTIAL_DELETED, actor)
        credential.delete()


@sensitive_variables("delivered")
def deliver_credentials(collector: Collector, credential_ids, job: str = "") -> list[dict]:
    """The decrypted credentials a job sends to `collector`. Only its own customer's.

    The job channel (YAN-1651) calls this and sends the result inside the job, never storing it.
    A wrong master key raises `vault.VaultError` or `ImproperlyConfigured`; the job then fails.
    """
    if collector.enrolled_at is None:
        raise CredentialNotAvailable("The collector hasn't enrolled.")
    try:
        ids = [str(uuid.UUID(str(credential_id))) for credential_id in credential_ids]
    except ValueError as exc:
        raise CredentialNotAvailable("One or more credentials aren't available.") from exc
    with transaction.atomic():
        found = {str(c.pk): c for c in collector.scoped(Credential).filter(pk__in=ids)}
        if len(found) != len(set(ids)):
            # Don't say which ids exist: another customer's id looks the same as a made-up one.
            raise CredentialNotAvailable("One or more credentials aren't available.")
        delivered = []
        for credential_id in ids:
            credential = found[credential_id]
            delivered.append(
                {
                    "id": credential_id,
                    "kind": credential.kind,
                    "username": credential.username,
                    "auth_protocol": credential.auth_protocol,
                    "priv_protocol": credential.priv_protocol,
                    **credential.secret(),
                }
            )
            _audit(credential, AuditEvent.Action.CREDENTIAL_USED, collector=collector, job=job)
    return delivered
