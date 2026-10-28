#!/bin/sh
# Create the engineer account and load the lab, once. Runs as the app role.
set -eu

user="${CARTIO_ENGINEER:-engineer}"

if cartio-controller shell --verbosity 0 -c "
import sys
from django.contrib.auth import get_user_model
sys.exit(0 if get_user_model().objects.filter(username='$user').exists() else 1)
"; then
    echo "Engineer $user already exists."
else
    cartio-controller createsuperuser --noinput --username "$user" --email "$user@example.com"
    echo "Created engineer $user. Run enroll_engineer $user before signing in."
fi

cartio-controller load_lab --user "$user"
