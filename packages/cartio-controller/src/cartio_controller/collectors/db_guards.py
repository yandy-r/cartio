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

# Moving a customer On site (YAN-1712) deletes their network data from the controller, including
# rows of append-only tables, once the on-site store confirmed it holds the export. The app role
# can't delete those rows, and the trigger refuses everyone else, so the deletion goes through
# `cartio_purge_network_rows`: it runs as the owner (SECURITY DEFINER), only for these tables,
# only for a customer that is On site, and only with that customer's `customer.data_mode_changed`
# audit event, which can't be deleted afterwards. While it runs, the trigger lets the owner
# delete that customer's rows and nothing else.
PURGEABLE_TABLES = (
    "collectors_source",
    "collectors_rawoutput",
    "collectors_configsnapshot",
    "collectors_assessmentrun",
    "collectors_checkresult",
)
PURGE_FUNCTION = "cartio_purge_network_rows"

_APPEND_ONLY_WITH_PURGE = """
CREATE OR REPLACE FUNCTION cartio_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE'
        AND current_setting('cartio.purge_customer', true) = OLD.customer_id::text
        AND EXISTS (
            SELECT 1 FROM pg_tables WHERE schemaname = TG_TABLE_SCHEMA
            AND tablename = TG_TABLE_NAME AND tableowner = current_user
        ) THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION '% rows can''t be changed or deleted.', TG_TABLE_NAME
        USING ERRCODE = 'restrict_violation';
END;
$$;
"""

_PURGE = """
CREATE OR REPLACE FUNCTION cartio_purge_network_rows(
    p_table text, p_customer bigint, p_event bigint
) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, {schema}, pg_temp AS $$
DECLARE
    deleted bigint;
BEGIN
    IF NOT p_table = ANY (ARRAY[{tables}]) THEN
        RAISE EXCEPTION '% isn''t network data.', p_table USING ERRCODE = 'restrict_violation';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM collectors_customer WHERE id = p_customer AND data_mode = 'on_site'
    ) OR NOT EXISTS (
        SELECT 1 FROM collectors_auditevent WHERE id = p_event AND customer_id = p_customer
        AND action = 'customer.data_mode_changed'
    ) THEN
        RAISE EXCEPTION 'Only a customer that moved On site has its network data deleted.'
            USING ERRCODE = 'restrict_violation';
    END IF;
    PERFORM set_config('cartio.purge_customer', p_customer::text, true);
    EXECUTE format('DELETE FROM %I WHERE customer_id = $1', p_table) USING p_customer;
    GET DIAGNOSTICS deleted = ROW_COUNT;
    PERFORM set_config('cartio.purge_customer', '', true);
    RETURN deleted;
END;
$$;
"""

# An On site customer's network data is refused by the model layer too, but that check reads the
# mode without a lock: a write that read Hosted could commit after the switch and its purge. The
# trigger reads the mode under the customer row's key-share lock, which waits for a switch in
# progress (it holds the row FOR UPDATE) and then sees its result.
ON_SITE_TRIGGER = "cartio_on_site_refused"

_REFUSE_ON_SITE = """
CREATE OR REPLACE FUNCTION cartio_on_site_refused() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    mode text;
BEGIN
    -- Nested: PL/pgSQL resolves NEW.job_id even when the table check is false.
    IF TG_TABLE_NAME = 'collectors_jobchunk' THEN
        IF EXISTS (
            SELECT 1 FROM collectors_job WHERE id = NEW.job_id AND type IN ('health', 'export')
        ) THEN
            RETURN NEW;
        END IF;
    END IF;
    SELECT data_mode INTO mode FROM collectors_customer WHERE id = NEW.customer_id FOR KEY SHARE;
    IF mode = 'on_site' THEN
        RAISE EXCEPTION 'This customer is On site: % rows stay in its own store.', TG_TABLE_NAME
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
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


def refuse_on_site(schema_editor, tables) -> None:
    """Refuse new rows in `tables` for an On site customer, waiting out a switch in progress."""
    if _postgres(schema_editor):
        schema_editor.execute(_REFUSE_ON_SITE, params=None)
        for table in tables:
            schema_editor.execute(
                f"CREATE TRIGGER {ON_SITE_TRIGGER} BEFORE INSERT ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION cartio_on_site_refused()"
            )


def allow_on_site(schema_editor, tables) -> None:
    if _postgres(schema_editor):
        for table in tables:
            schema_editor.execute(f"DROP TRIGGER IF EXISTS {ON_SITE_TRIGGER} ON {table}")
        schema_editor.execute("DROP FUNCTION IF EXISTS cartio_on_site_refused()")


def create_purge_function(schema_editor) -> None:
    if _postgres(schema_editor):
        with schema_editor.connection.cursor() as cursor:
            cursor.execute("SELECT current_schema()")
            schema = schema_editor.connection.ops.quote_name(cursor.fetchone()[0])
        tables = ", ".join(f"'{table}'" for table in PURGEABLE_TABLES)
        schema_editor.execute(_APPEND_ONLY_WITH_PURGE, params=None)
        schema_editor.execute(_PURGE.format(schema=schema, tables=tables), params=None)


def drop_purge_function(schema_editor) -> None:
    if _postgres(schema_editor):
        schema_editor.execute(f"DROP FUNCTION IF EXISTS {PURGE_FUNCTION}(text, bigint, bigint)")
        # Back to refusing every change.
        schema_editor.execute(_FUNCTIONS, params=None)
