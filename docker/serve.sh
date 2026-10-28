#!/bin/sh
# Collect the static files for the web server, then run the controller under gunicorn.
set -eu

cartio-controller collectstatic --noinput --clear --verbosity 0
exec gunicorn cartio_controller.wsgi --bind 0.0.0.0:8000 \
    --workers "${CARTIO_WORKERS:-3}" --access-logfile -
