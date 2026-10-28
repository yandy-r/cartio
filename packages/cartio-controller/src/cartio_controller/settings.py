"""Django settings for the Cartio controller.

Everything that differs per environment comes from environment variables:

- CARTIO_SECRET_KEY: required unless CARTIO_DEBUG is set.
- CARTIO_DEBUG: "1" for local development.
- CARTIO_ALLOWED_HOSTS: comma-separated host names.
- DATABASE_URL: PostgreSQL in any shared environment. Defaults to a local SQLite file. The
  controller connects as the app role; run `migrate` with the owner role's URL instead.
- CARTIO_DB_APP_ROLE: the PostgreSQL role the controller runs as. `migrate`, run as the owner,
  grants it row access and nothing more (collectors/db_grants.py, docs/deploy/database.md).
- CARTIO_MASTER_KEY_FILE: a file holding the base64 32-byte master key that wraps each customer's
  credential key (ADR-0003). Keep it out of the database and its backups, and back it up on its
  own: losing it means re-entering every credential. CARTIO_MASTER_KEY can hold the key instead.
  Generate one with `openssl rand -base64 32`. Engineers' authenticator keys are encrypted with
  it too.
- CARTIO_PREVIOUS_MASTER_KEY_FILE: set only during a master key rotation, to the old key (or
  CARTIO_PREVIOUS_MASTER_KEY). Run `rotate_master_key`, then unset it (docs/deploy/master-key.md).
- CARTIO_SESSION_HOURS: how long an engineer stays signed in, 8 hours by default. Sessions also
  end when the browser closes.
- CARTIO_SIGN_IN_MAX_FAILURES: failed sign-ins (passwords and codes) an account may have in the
  last CARTIO_SIGN_IN_LOCKOUT_MINUTES (15 by default) before it's refused, 10 by default.
- CARTIO_SIGN_IN_MAX_FAILURES_PER_ADDRESS: the same for one client address, 50 by default. Behind
  a reverse proxy every sign-in comes from the proxy's address, so this limit then covers all
  engineers together. 0 turns either limit off.
- CARTIO_LOGIN_LOG_DAYS: how long `purge_login_events` keeps the sign-in log, 365 days by default.
"""

import os
from pathlib import Path

import dj_database_url

BASE_DIR = Path(os.environ.get("CARTIO_DATA_DIR", Path.cwd()))

DEBUG = os.environ.get("CARTIO_DEBUG") == "1"

SECRET_KEY = os.environ.get("CARTIO_SECRET_KEY", "")
if not SECRET_KEY:
    if not DEBUG:
        raise RuntimeError("Set CARTIO_SECRET_KEY, or CARTIO_DEBUG=1 for local development.")
    SECRET_KEY = "insecure-local-development-key"  # noqa: S105

ALLOWED_HOSTS = [h for h in os.environ.get("CARTIO_ALLOWED_HOSTS", "").split(",") if h]
if DEBUG:
    ALLOWED_HOSTS += ["localhost", "127.0.0.1", "[::1]", "testserver"]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "cartio_controller.collectors",
    "cartio_controller.engineers",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "cartio_controller.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "cartio_controller.wsgi.application"
ASGI_APPLICATION = "cartio_controller.asgi.application"

DATABASES = {
    "default": dj_database_url.config(
        default=f"sqlite:///{BASE_DIR / 'cartio.sqlite3'}",
        conn_max_age=60,
    )
}

DB_APP_ROLE = os.environ.get("CARTIO_DB_APP_ROLE", "")

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# Engineers sign in with a password and an authenticator code (cartio_controller.engineers),
# including for the admin. Only staff accounts can sign in.
LOGIN_URL = "engineers:sign-in"
LOGIN_REDIRECT_URL = "engineers:customers"

SESSION_COOKIE_AGE = int(float(os.environ.get("CARTIO_SESSION_HOURS", "8")) * 3600)
SESSION_EXPIRE_AT_BROWSER_CLOSE = True

# Failed sign-ins are counted from the sign-in log (collectors.LoginEvent), so the limits hold
# across every controller process. A refused attempt isn't logged, which keeps the log bounded.
SIGN_IN_MAX_FAILURES = int(os.environ.get("CARTIO_SIGN_IN_MAX_FAILURES", "10"))
SIGN_IN_MAX_FAILURES_PER_ADDRESS = int(
    os.environ.get("CARTIO_SIGN_IN_MAX_FAILURES_PER_ADDRESS", "50")
)
SIGN_IN_LOCKOUT_MINUTES = int(os.environ.get("CARTIO_SIGN_IN_LOCKOUT_MINUTES", "15"))
LOGIN_LOG_DAYS = int(os.environ.get("CARTIO_LOGIN_LOG_DAYS", "365"))
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG

# The engineer API. Engineers sign in to the controller and need the matching model permissions
# to make changes. Collectors use their own endpoints and never reach this API.
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.SessionAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAdminUser",
        "cartio_controller.collectors.permissions.ArchivableModelPermissions",
    ],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 100,
}

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "static"
