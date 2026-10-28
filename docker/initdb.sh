#!/bin/sh
# The controller's two roles and its database, as docs/deploy/database.md sets them up.
set -eu

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
    -v owner_password="$CARTIO_OWNER_PASSWORD" -v app_password="$CARTIO_APP_PASSWORD" <<'SQL'
CREATE ROLE cartio_owner LOGIN PASSWORD :'owner_password';
CREATE ROLE cartio_app LOGIN PASSWORD :'app_password' NOSUPERUSER NOCREATEDB NOCREATEROLE;
CREATE DATABASE cartio OWNER cartio_owner;
\c cartio
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
ALTER SCHEMA public OWNER TO cartio_owner;
SQL
