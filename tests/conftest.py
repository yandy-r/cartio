import pytest
from django.conf import settings


def _as_app_role() -> bool:
    role = settings.DB_APP_ROLE
    return bool(role) and settings.DATABASES["default"].get("USER") == role


def pytest_collection_modifyitems(config, items):
    # Transactional tests flush the database with TRUNCATE, which the app role can't run
    # (YAN-1724). The CI pass as the owner role runs them.
    if not _as_app_role():
        return
    skip = pytest.mark.skip(reason="Flushes with TRUNCATE; runs in the owner role's pass.")
    for item in items:
        marker = item.get_closest_marker("django_db")
        if marker and marker.kwargs.get("transaction"):
            item.add_marker(skip)
