#!/bin/sh
# Create the master key on first start, then run the command.
set -eu

key="${CARTIO_MASTER_KEY_FILE:-}"
if [ -n "$key" ] && [ ! -s "$key" ]; then
    umask 077
    python -c "import base64, secrets; print(base64.b64encode(secrets.token_bytes(32)).decode())" > "$key"
    echo "Created a master key at $key."
fi

exec "$@"
