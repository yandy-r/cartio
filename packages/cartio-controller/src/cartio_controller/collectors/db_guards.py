"""Customer isolation and append-only rules enforced by PostgreSQL itself (YAN-1721).

The model layer (`CustomerOwned`) already refuses these writes. The database refuses them too,
so raw SQL or a future code path that skips the ORM can't link two customers' data or rewrite
the audit log:

- A child row's `customer_id` must match its parent's: a composite foreign key from
  `(parent_id, customer_id)` to the parent's unique `(id, customer_id)`.
- A row's `customer_id` never changes once written.
- Append-only tables refuse UPDATE and DELETE.

SQLite is for local development only, so these are PostgreSQL-only and the ORM guards stay.
Migrations call these helpers. A new customer-owned table gets the same rules in its own
migration; tests/test_db_guards.py fails until it does.
"""

# Both functions raise SQLSTATE 23001 (restrict_violation), which Django reports as an
# IntegrityError, like the foreign keys do.
_FUNCTIONS = """
CREATE OR REPLACE FUNCTION cartio_customer_id_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'A % row can''t move to another customer.', TG_TABLE_NAME
        USING ERRCODE = 'restrict_violation';
END;
$$;

CREATE OR REPLACE FUNCTION cartio_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% rows can''t be changed or deleted.', TG_TABLE_NAME
        USING ERRCODE = 'restrict_violation';
END;
$$;
"""

IMMUTABLE_TRIGGER = "cartio_customer_id_immutable"
APPEND_ONLY_TRIGGER = "cartio_append_only"


def tie_name(table: str, column: str) -> str:
    return f"{table}_{column}_same_customer"


def _postgres(schema_editor) -> bool:
    return schema_editor.connection.vendor == "postgresql"


def create_functions(schema_editor) -> None:
    if _postgres(schema_editor):
        # No params, so the driver leaves the `%` placeholders of RAISE alone.
        schema_editor.execute(_FUNCTIONS, params=None)


def drop_functions(schema_editor) -> None:
    if _postgres(schema_editor):
        schema_editor.execute(
            "DROP FUNCTION IF EXISTS cartio_customer_id_immutable();"
            "DROP FUNCTION IF EXISTS cartio_append_only();"
        )


def tie_to_parent(schema_editor, table: str, column: str, parent: str) -> None:
    """Make `table.column` point only at a `parent` row of the same customer.

    `parent` needs a unique `(id, customer_id)`. A null `column` skips the check (MATCH SIMPLE).
    """
    if _postgres(schema_editor):
        schema_editor.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT {tie_name(table, column)} "
            f"FOREIGN KEY ({column}, customer_id) REFERENCES {parent} (id, customer_id)"
        )


def untie(schema_editor, table: str, column: str) -> None:
    if _postgres(schema_editor):
        schema_editor.execute(
            f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {tie_name(table, column)}"
        )


def freeze_customer(schema_editor, table: str) -> None:
    """Refuse any UPDATE that changes `table.customer_id`."""
    if _postgres(schema_editor):
        schema_editor.execute(
            f"CREATE TRIGGER {IMMUTABLE_TRIGGER} BEFORE UPDATE OF customer_id ON {table} "
            "FOR EACH ROW WHEN (OLD.customer_id IS DISTINCT FROM NEW.customer_id) "
            "EXECUTE FUNCTION cartio_customer_id_immutable()"
        )


def unfreeze_customer(schema_editor, table: str) -> None:
    if _postgres(schema_editor):
        schema_editor.execute(f"DROP TRIGGER IF EXISTS {IMMUTABLE_TRIGGER} ON {table}")


def make_append_only(schema_editor, table: str) -> None:
    """Refuse UPDATE and DELETE on `table`. TRUNCATE stays, for test database flushes."""
    if _postgres(schema_editor):
        schema_editor.execute(
            f"CREATE TRIGGER {APPEND_ONLY_TRIGGER} BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION cartio_append_only()"
        )


def unmake_append_only(schema_editor, table: str) -> None:
    if _postgres(schema_editor):
        schema_editor.execute(f"DROP TRIGGER IF EXISTS {APPEND_ONLY_TRIGGER} ON {table}")
