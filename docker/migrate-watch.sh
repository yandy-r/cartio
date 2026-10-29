#!/bin/sh
# Development only (compose.dev.yml): apply migrations as the owner role whenever a migration file
# under /app/packages changes, so the reloading controller never runs against an old schema.
# A failed migrate (a half-written file, say) is logged and tried again on the next change.
set -u

packages="${CARTIO_PACKAGES:-/app/packages}"
interval="${CARTIO_MIGRATE_WATCH_INTERVAL:-2}"

fingerprint() {
    find "$packages" -path '*/migrations/*.py' -type f | sort | xargs cat | cksum
}

last=""
while :; do
    now=$(fingerprint)
    if [ "$now" != "$last" ]; then
        cartio-controller migrate --noinput \
            || echo "migrate failed; trying again when the migrations change." >&2
        last="$now"
    fi
    sleep "$interval"
done
