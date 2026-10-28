"""PostgreSQL itself refuses cross-customer links and audit rewrites (YAN-1721).

These use raw SQL on purpose: the model layer already refuses the same writes, and these rules
are for code that skips it. SQLite is local development only, so they skip there.
"""

import uuid

import pytest
from cartio_controller.collectors import db_guards
from cartio_controller.collectors.models import (
    AuditEvent,
    CheckIn,
    CollectionRun,
    CollectionRunResult,
    Collector,
    Customer,
    CustomerOwned,
    Engagement,
)
from django.apps import apps
from django.db import IntegrityError, ProgrammingError, connection, transaction

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def postgres_only():
    if connection.vendor != "postgresql":
        pytest.skip("The database-level guards are PostgreSQL only.")


@pytest.fixture
def acme():
    return Customer.objects.create(name="Acme")


@pytest.fixture
def globex():
    return Customer.objects.create(name="Globex")


@pytest.fixture
def collector(acme):
    return Collector.objects.for_customer(acme).create(name="vm01")


MOVED = "can't move to another customer"
APPEND_ONLY = "can't be changed or deleted"


def _tie(table, column):
    return f'"{db_guards.tie_name(table, column)}"'


def _refused(sql, params, match, error=IntegrityError):
    """The statement fails on the guard named by `match`, not on some other constraint."""
    with (
        pytest.raises(error, match=match),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(sql, params)


def _owns_tables():
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_has_role(tableowner, 'USAGE') FROM pg_tables WHERE tablename = %s",
            ["collectors_collector"],
        )
        return cursor.fetchone()[0]


def _checkin(collector):
    return CheckIn.objects.for_customer(collector.customer).create(
        collector=collector, hostname="vm01", version="1", platform="linux"
    )


def test_a_check_in_cant_be_inserted_for_another_customer(collector, globex):
    _refused(
        "INSERT INTO collectors_checkin (customer_id, collector_id, at, hostname, version,"
        " platform) VALUES (%s, %s, now(), 'vm01', '1', 'linux')",
        [globex.pk, collector.pk],
        _tie("collectors_checkin", "collector_id"),
    )


def test_a_check_in_cant_move_to_another_customer(collector, globex):
    check_in = _checkin(collector)
    _refused(
        "UPDATE collectors_checkin SET customer_id = %s WHERE id = %s",
        [globex.pk, check_in.pk],
        MOVED,
    )


def test_a_check_in_cant_point_at_another_customers_collector(collector, globex):
    check_in = _checkin(collector)
    other = Collector.objects.for_customer(globex).create(name="vm02")
    _refused(
        "UPDATE collectors_checkin SET collector_id = %s WHERE id = %s",
        [other.pk, check_in.pk],
        _tie("collectors_checkin", "collector_id"),
    )


def test_a_collector_with_check_ins_cant_move_to_another_customer(collector, globex):
    _checkin(collector)
    move = "UPDATE collectors_collector SET customer_id = %s WHERE id = %s"
    _refused(move, [globex.pk, collector.pk], MOVED)
    if not _owns_tables():
        return  # Only the owner can disable a trigger (test_db_roles).
    # The check-ins' tie holds on its own too, without the collector's trigger.
    with transaction.atomic():
        with connection.cursor() as cursor:
            # Django's own foreign keys are deferred; ALTER TABLE needs them checked first.
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
            cursor.execute(
                "ALTER TABLE collectors_collector DISABLE TRIGGER " + db_guards.IMMUTABLE_TRIGGER
            )
        _refused(move, [globex.pk, collector.pk], _tie("collectors_checkin", "collector_id"))
        transaction.set_rollback(True)


def test_no_record_moves_to_another_customer_even_without_children(acme, globex):
    engagement = Engagement.objects.for_customer(acme).create(name="Audit")
    _refused(
        "UPDATE collectors_engagement SET customer_id = %s WHERE id = %s",
        [globex.pk, engagement.pk],
        MOVED,
    )


def test_a_run_cant_use_another_customers_engagement(collector, globex):
    engagement = Engagement.objects.for_customer(globex).create(name="Audit")
    _refused(
        "INSERT INTO collectors_collectionrun (id, customer_id, job_type, engagement_id,"
        " collector_id, started_by, started_at) VALUES (%s, %s, 'discovery', %s, %s, '', now())",
        [uuid.uuid4(), collector.customer_id, engagement.pk, collector.pk],
        _tie("collectors_collectionrun", "engagement_id"),
    )


def test_other_columns_can_still_change(collector):
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE collectors_collector SET hostname = 'vm01.acme' WHERE id = %s", [collector.pk]
        )
    collector.refresh_from_db()
    assert collector.hostname == "vm01.acme"


@pytest.fixture
def audit_rows(collector):
    run = CollectionRun.objects.for_customer(collector.customer).create(
        job_type="discovery", collector=collector
    )
    return {
        "collectors_auditevent": AuditEvent.objects.for_customer(collector.customer)
        .create(action="run.started", collector=collector, object_type="run", object_id="1")
        .pk,
        "collectors_collectionrun": run.pk,
        "collectors_collectionrunresult": CollectionRunResult.objects.for_customer(
            collector.customer
        )
        .create(run=run, outcome="succeeded")
        .pk,
    }


@pytest.mark.parametrize(
    "table, column",
    [
        ("collectors_auditevent", "id"),
        ("collectors_collectionrun", "id"),
        ("collectors_collectionrunresult", "run_id"),
    ],
)
def test_audit_records_cant_be_changed_or_deleted(audit_rows, table, column):
    # Table and column names come from the parametrize list above, never from input.
    pk = audit_rows[table]
    # The app role isn't even granted these (test_db_roles); the owner hits the trigger.
    refusal = (APPEND_ONLY,) if _owns_tables() else ("permission denied", ProgrammingError)
    _refused(f"UPDATE {table} SET customer_id = customer_id WHERE {column} = %s", [pk], *refusal)  # noqa: S608
    _refused(f"DELETE FROM {table} WHERE {column} = %s", [pk], *refusal)  # noqa: S608


# A new table must get the same rules in its migration. These read the live schema.


def _owned_models():
    return [
        m for m in apps.get_app_config("collectors").get_models() if issubclass(m, CustomerOwned)
    ]


def _triggers(name):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT tgrelid::regclass::text FROM pg_trigger WHERE tgname = %s AND NOT tgisinternal",
            [name],
        )
        return {row[0] for row in cursor.fetchall()}


def _ties():
    """The tie constraints, as (child table, definition)."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT conrelid::regclass::text, pg_get_constraintdef(oid) FROM pg_constraint"
            " WHERE contype = 'f' AND conname LIKE %s",
            ["%\\_same\\_customer"],
        )
        return set(cursor.fetchall())


def test_every_customer_owned_table_keeps_its_customer():
    assert _triggers(db_guards.IMMUTABLE_TRIGGER) == {m._meta.db_table for m in _owned_models()}


def test_every_parent_link_is_tied_to_the_same_customer():
    expected = {
        (
            model._meta.db_table,
            f"FOREIGN KEY ({field.column}, customer_id)"
            f" REFERENCES {field.related_model._meta.db_table}(id, customer_id)",
        )
        for model in _owned_models()
        for field in model._meta.concrete_fields
        if (field.many_to_one or field.one_to_one)
        and field.name != "customer"
        and issubclass(field.related_model, CustomerOwned)
    }
    assert expected
    assert _ties() == expected


def test_every_append_only_table_refuses_changes():
    assert _triggers(db_guards.APPEND_ONLY_TRIGGER) == {
        m._meta.db_table for m in _owned_models() if m.append_only
    }
