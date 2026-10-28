"""Django settings for the Cartio controller.

Everything that differs per environment comes from environment variables:

- CARTIO_SECRET_KEY: required unless CARTIO_DEBUG is set.
- CARTIO_DEBUG: "1" for local development.
- CARTIO_ALLOWED_HOSTS: comma-separated host names.
- DATABASE_URL: PostgreSQL in any shared environment. Defaults to a local SQLite file.
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

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# The engineer API. Engineers sign in to the controller (staff accounts until the login and UI
# shell issue lands) and need the matching model permissions to make changes. Collectors use
# their own endpoints and never reach this API.
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.SessionAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAdminUser",
        "rest_framework.permissions.DjangoModelPermissions",
    ],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 100,
}

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "static"
