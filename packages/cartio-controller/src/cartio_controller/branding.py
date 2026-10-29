"""The name and organisation the UI and reports carry, from CARTIO_BRAND_NAME and CARTIO_BRAND_ORG.

Read from settings on every call, so tests can override them.
"""

from django.conf import settings


def name() -> str:
    return settings.BRAND_NAME


def org() -> str:
    return settings.BRAND_ORG


def report() -> str:
    """Who a report is from: the organisation, or the app name when none is set."""
    return org() or name()


def context(request) -> dict:
    return {"brand": {"name": name(), "org": org(), "report": report()}}
