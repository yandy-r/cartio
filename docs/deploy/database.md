# Controller database roles

The controller uses two PostgreSQL roles (YAN-1724):

| Role | Used by | Can do |
| -- | -- | -- |
| Owner, for example `cartio_owner` | `cartio-controller migrate` only | Owns every table. Creates and alters them, and installs the customer isolation and append-only rules (YAN-1721). |
| App, for example `cartio_app` | The running controller and its other commands, such as `purge_login_events` | Reads and writes rows. Doesn't own anything, so it can't `TRUNCATE`, `ALTER` or drop a table, or disable its triggers. |

The app role also can't change or delete rows in the append-only tables (the customer audit
log, collection runs and their results). It can delete from the sign-in log, which
`purge_login_events` needs for retention, but it can't change an entry there either. The
migration history is read-only to it.

## Set up the roles

Run this once as a PostgreSQL superuser, with your own passwords:

```sql
CREATE ROLE cartio_owner LOGIN PASSWORD '...';
CREATE ROLE cartio_app LOGIN PASSWORD '...' NOSUPERUSER NOCREATEDB NOCREATEROLE;
CREATE DATABASE cartio OWNER cartio_owner;
\c cartio
-- PostgreSQL 15 and later already do this. On older versions, stop anyone else from
-- creating objects in the schema the controller uses.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
ALTER SCHEMA public OWNER TO cartio_owner;
```

Don't make the app role a member of the owner role, or a superuser, and don't grant table
privileges to `PUBLIC`: any of these would give it rights back. `migrate` checks for them and
fails until they're gone.

## Migrate as the owner, run as the app role

Set `CARTIO_DB_APP_ROLE` everywhere the controller runs, and point `DATABASE_URL` at the role
that step needs:

```sh
export CARTIO_DB_APP_ROLE=cartio_app

# Every upgrade: as the owner. Afterwards, migrate grants the app role its privileges again,
# so new tables are covered.
DATABASE_URL=postgres://cartio_owner:...@db/cartio cartio-controller migrate

# The controller, cron jobs and every other command: as the app role.
DATABASE_URL=postgres://cartio_app:...@db/cartio cartio-controller ...
```

`migrate` resets the app role's table privileges each time, so privileges granted by hand
don't survive it. Keep the owner's password out of the running controller's environment.

## An existing database

A controller that has run as the table owner keeps working as before while
`CARTIO_DB_APP_ROLE` is unset. To switch it over:

1. Create whichever role is missing, as above.
2. Make sure the owner role owns the tables. If the controller's current role is to become the
   app role, hand its objects over, as a superuser: `REASSIGN OWNED BY cartio_app TO
   cartio_owner;` in the `cartio` database.
3. Run `migrate` once as the owner with `CARTIO_DB_APP_ROLE` set. It has nothing to apply, but
   it grants and checks.
4. Change the controller's `DATABASE_URL` to the app role.

## Tests

CI runs the test suite twice against PostgreSQL: as the owner, which creates and migrates the
test database, and then as the app role with `--reuse-db`. Tests that flush the database need
`TRUNCATE`, so they only run in the owner's pass.
