#!/bin/sh
# Enroll once with CARTIO_ENROLL_TOKEN, then check in every minute.
# It shares the controller's network, because the collector only speaks plain http to localhost.
set -eu

config=/collector/collector.json
if [ ! -s "$config" ]; then
    if [ -z "${CARTIO_ENROLL_TOKEN:-}" ]; then
        echo "Set CARTIO_ENROLL_TOKEN to the token create_collector printed." >&2
        exit 1
    fi
    cartio-collector --config "$config" enroll --controller http://localhost:8000 \
        --token "$CARTIO_ENROLL_TOKEN"
fi

exec cartio-collector --config "$config" checkin --interval 60
