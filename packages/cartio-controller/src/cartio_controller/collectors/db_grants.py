"""The controller's runtime database role and what it may do (YAN-1724).

An owner role runs migrations and owns every table. The controller itself connects as an app
role named by CARTIO_DB_APP_ROLE, which doesn't own the tables, so it can't TRUNCATE them,
ALTER them or disable their triggers (db_guards). It reads and writes rows only:

- Append-only tables (the audit log and collection runs) get SELECT and INSERT only.
- The sign-in log gets SELECT, INSERT and DELETE: `purge_login_events` drops old entries, and
  nothing ever rewrites one.
- Django's migration history is read-only, since only the owner migrates.

`migrate` run as the owner re-applies these grants afterwards, so a new table is covered as soon
as it exists. PostgreSQL only; with no CARTIO_DB_APP_ROLE nothing changes.
"""

from django.apps import apps
from django.conf import settings
from django.db import connections, transaction

ROW_PRIVILEGES = "SELECT, INSERT, UPDATE, DELETE"


def _never_rewritten() -> dict[str, str]:
    """Tables whose rows the app role may not change, with the privileges it loses on each."""
    from .models import CustomerOwned, LoginEvent

    revoked = {
        model._meta.db_table: "UPDATE, DELETE"
        for model in apps.get_models()
        if issubclass(model, CustomerOwned) and model.append_only
    }
    revoked[LoginEvent._meta.db_table] = "UPDATE"
    revoked["django_migrations"] = "INSERT, UPDATE, DELETE"
    return revoked


def grant_app_role(using: str = "default") -> bool:
    """Give the app role exactly its runtime privileges. True if any grants were applied.

    Skipped when no app role is set, off PostgreSQL, or when connected as the app role itself
    (it can't grant anything, and a test run reusing the owner's database migrates as it).
    """
    role = settings.DB_APP_ROLE
    connection = connections[using]
    if not role or connection.vendor != "postgresql":
        return False
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_user")
        if cursor.fetchone()[0] == role:
            return False
    quoted = connection.ops.quote_name(role)
    statements = [
        f"GRANT USAGE ON SCHEMA public TO {quoted}",
        # Start from nothing, so a privilege granted by hand (TRUNCATE, TRIGGER) doesn't linger.
        f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {quoted}",
        f"GRANT {ROW_PRIVILEGES} ON ALL TABLES IN SCHEMA public TO {quoted}",
        f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {quoted}",
        f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {quoted}",
    ]
    statements += [
        f"REVOKE {privileges} ON {connection.ops.quote_name(table)} FROM {quoted}"
        for table, privileges in _never_rewritten().items()
    ]
    with transaction.atomic(using=using), connection.cursor() as cursor:
        for statement in statements:
            cursor.execute(statement)
    return True


def grant_after_migrate(sender, using="default", **kwargs):
    grant_app_role(using)
