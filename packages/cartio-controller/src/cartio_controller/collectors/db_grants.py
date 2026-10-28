"""The controller's runtime database role and what it may do (YAN-1724).

An owner role runs migrations and owns every table. The controller itself connects as an app
role named by CARTIO_DB_APP_ROLE, which doesn't own the tables, so it can't TRUNCATE them,
ALTER them or disable their triggers (db_guards). It reads and writes rows only:

- Append-only tables (the audit log and collection runs) get SELECT and INSERT only.
- The sign-in log gets SELECT, INSERT and DELETE: `purge_login_events` drops old entries, and
  nothing ever rewrites one.
- Django's migration history is read-only, since only the owner migrates.

`migrate` run as the owner re-applies these grants afterwards, so a new table is covered as soon
as it exists, and fails if the role still gets more some other way (a superuser, a member of
the owner, or a grant to PUBLIC). PostgreSQL only; with no CARTIO_DB_APP_ROLE nothing changes.
"""

import logging

from django.apps import apps
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import connections, transaction

logger = logging.getLogger(__name__)

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
        cursor.execute("SELECT current_user, current_schema()")
        user, schema = cursor.fetchone()
    if user == role:
        logger.warning("Connected as %s, the app role, so its privileges weren't updated.", role)
        return False
    quoted = connection.ops.quote_name(role)
    in_schema = connection.ops.quote_name(schema)
    statements = [
        f"GRANT USAGE ON SCHEMA {in_schema} TO {quoted}",
        # Start from nothing, so a privilege granted by hand (TRUNCATE, TRIGGER) doesn't linger.
        f"REVOKE ALL ON ALL TABLES IN SCHEMA {in_schema} FROM {quoted}",
        f"GRANT {ROW_PRIVILEGES} ON ALL TABLES IN SCHEMA {in_schema} TO {quoted}",
        f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA {in_schema} FROM {quoted}",
        f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA {in_schema} TO {quoted}",
    ]
    statements += [
        f"REVOKE {privileges} ON {connection.ops.quote_name(table)} FROM {quoted}"
        for table, privileges in _never_rewritten().items()
    ]
    with transaction.atomic(using=using), connection.cursor() as cursor:
        for statement in statements:
            cursor.execute(statement)
        _check_least_privilege(cursor, role, schema)
    return True


def _check_least_privilege(cursor, role: str, schema: str) -> None:
    """Fail the migration if the app role still has more than row access some other way.

    REVOKE only removes the role's own grants, not those it holds as a superuser, through a
    role it belongs to, or through PUBLIC.
    """
    cursor.execute(
        "SELECT rolsuper OR rolcreaterole OR rolbypassrls, pg_has_role(%s, current_user, 'MEMBER')"
        " FROM pg_roles WHERE rolname = %s",
        [role, role],
    )
    powerful, member = cursor.fetchone()
    if powerful or member:
        raise ImproperlyConfigured(
            f"CARTIO_DB_APP_ROLE {role} must not be a superuser, create roles, bypass row "
            "security or belong to the role that runs migrations (docs/deploy/database.md)."
        )
    cursor.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname = %s AND ("
        " pg_has_role(%s, tableowner, 'USAGE') OR has_table_privilege(%s,"
        " quote_ident(schemaname) || '.' || quote_ident(tablename),"
        " 'TRUNCATE, TRIGGER, REFERENCES'))",
        [schema, role, role],
    )
    loose = [row[0] for row in cursor.fetchall()]
    for table, privileges in _never_rewritten().items():
        cursor.execute(
            "SELECT has_table_privilege(%s, quote_ident(%s) || '.' || quote_ident(%s), %s)",
            [role, schema, table, privileges],
        )
        if cursor.fetchone()[0]:
            loose.append(table)
    if loose:
        raise ImproperlyConfigured(
            f"CARTIO_DB_APP_ROLE {role} still owns or can rewrite {', '.join(sorted(loose))}, "
            "probably through PUBLIC or another role. Revoke that grant and migrate again."
        )


def grant_after_migrate(sender, using="default", **kwargs):
    grant_app_role(using)
