#!/bin/sh
# Collect the static files for the web server, then run the controller under uvicorn (ASGI), so
# collectors' job long-polls wait without holding a worker each (ADR-0009).
set -eu

cartio-controller collectstatic --noinput --clear --verbosity 0
exec uvicorn cartio_controller.asgi:application --host 0.0.0.0 --port 8000 \
    --workers "${CARTIO_WORKERS:-3}"
