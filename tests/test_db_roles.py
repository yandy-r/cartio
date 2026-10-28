"""The controller's runtime role can read and write rows, and nothing more (YAN-1724).

CI runs the suite twice on PostgreSQL: as the owner role, which migrates and grants, and then
as the app role named by CARTIO_DB_APP_ROLE. Each half checks its own side.
"""

import pytest
from cartio_controller.collectors import db_grants, db_guards
from cartio_controller.collectors.models import (
    AuditEvent,
    CollectionRun,
    CollectionRunResult,
    ConfigSnapshot,
    Customer,
    LoginEvent,
    RawOutput,
    Source,
)
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import DatabaseError, connection, transaction

pytestmark = pytest.mark.django_db

ROLE = settings.DB_APP_ROLE
APPEND_ONLY = {
    m._meta.db_table
    for m in (AuditEvent, CollectionRun, CollectionRunResult, Source, RawOutput, ConfigSnapshot)
}
LOGIN_LOG = LoginEvent._meta.db_table


def _current_user():
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_user")
        return cursor.fetchone()[0]


@pytest.fixture
def owner():
    if connection.vendor != "postgresql" or not ROLE or _current_user() == ROLE:
        pytest.skip("Needs PostgreSQL, CARTIO_DB_APP_ROLE and the owner role.")


@pytest.fixture
def app_role():
    if connection.vendor != "postgresql" or not ROLE or _current_user() != ROLE:
        pytest.skip("Needs PostgreSQL, connected as CARTIO_DB_APP_ROLE.")


def _privileges():
    """{table: (owned by the app role, its privileges)} for every table in the schema."""
    kinds = ["SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"]
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT tablename, tableowner = %s FROM pg_tables WHERE schemaname = current_schema()",
            [ROLE],
        )
        tables = cursor.fetchall()
        result = {}
        for table, owned in tables:
            held = set()
            for kind in kinds:
                cursor.execute("SELECT has_table_privilege(%s, %s, %s)", [ROLE, table, kind])
                if cursor.fetchone()[0]:
                    held.add(kind)
            result[table] = (owned, held)
    return result


def test_the_app_role_gets_row_access_only(owner):
    privileges = _privileges()
    assert APPEND_ONLY | {LOGIN_LOG} <= privileges.keys()
    for table, (owned, held) in privileges.items():
        assert not owned, table
        if table in APPEND_ONLY:
            expected = {"SELECT", "INSERT"}
        elif table == LOGIN_LOG:
            expected = {"SELECT", "INSERT", "DELETE"}
        elif table == "django_migrations":
            expected = {"SELECT"}
        else:
            expected = {"SELECT", "INSERT", "UPDATE", "DELETE"}
        assert held == expected, table


def test_migrate_fails_while_public_can_still_rewrite_the_audit_log(owner):
    table = AuditEvent._meta.db_table
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(f"GRANT DELETE ON {table} TO PUBLIC")
        with pytest.raises(ImproperlyConfigured, match=table):
            db_grants.grant_app_role()
        transaction.set_rollback(True)


@pytest.fixture
def audit_event():
    customer = Customer.objects.create(name="Acme")
    return AuditEvent.objects.for_customer(customer).create(
        action="record.created", object_type="customer", object_id=str(customer.pk)
    )


def _refused(sql):
    with (
        pytest.raises(DatabaseError, match="permission denied|must be owner"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(sql)


@pytest.mark.parametrize("table", sorted(APPEND_ONLY))
def test_the_app_role_cant_empty_or_rewrite_the_audit_tables(app_role, audit_event, table):
    # Table names come from the models above, never from input.
    _refused(f"TRUNCATE {table}")
    _refused(f"DELETE FROM {table}")  # noqa: S608
    _refused(f"UPDATE {table} SET customer_id = customer_id")  # noqa: S608
    _refused(f"ALTER TABLE {table} DISABLE TRIGGER {db_guards.APPEND_ONLY_TRIGGER}")
    assert AuditEvent.objects.for_customer(audit_event.customer).count() == 1


def test_the_app_role_cant_alter_customer_tables(app_role):
    table = Customer._meta.db_table
    _refused(f"TRUNCATE {table}")
    _refused(f"ALTER TABLE {table} ADD COLUMN note text")
    _refused(f"ALTER TABLE collectors_engagement DISABLE TRIGGER {db_guards.IMMUTABLE_TRIGGER}")


def test_the_app_role_can_purge_but_not_rewrite_the_sign_in_log(app_role):
    LoginEvent.objects.create(kind="failed", username="alice")
    _refused(f"UPDATE {LOGIN_LOG} SET username = 'bob'")  # noqa: S608
    with connection.cursor() as cursor:
        cursor.execute(f"DELETE FROM {LOGIN_LOG}")  # noqa: S608
    assert not LoginEvent.objects.exists()
