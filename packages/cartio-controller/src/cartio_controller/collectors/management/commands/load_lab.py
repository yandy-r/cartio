"""Load the sanitized lab into a customer, so the engineer UI has data to show locally.

Collection doesn't reach the controller yet (the job channel waits on YAN-1640), so this is how a
local or compose controller gets devices, configs and check results. It reads the sanitized
fixtures in `tests/fixtures/sanitized` and stores them as uploads, the way an import would.

Two engagements, so Compare and the report's Changes page have something to compare:

- "Q2 review" (completed): core-sw1 and edge-fw1, with edge-fw1 on its weak config.
- "Q4 review" (active): all three devices, with edge-fw1 hardened.

Development only: it refuses to run unless CARTIO_DEBUG=1.
"""

import datetime
from pathlib import Path

from cartio_core.masking import find_unmasked
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from cartio_controller.collectors import audit
from cartio_controller.collectors.assessments import run_assessment
from cartio_controller.collectors.inventory import store_config, store_outputs
from cartio_controller.collectors.models import (
    AuditEvent,
    Customer,
    Engagement,
    Site,
    Source,
)

DEFAULT_FIXTURES = Path("tests") / "fixtures" / "sanitized"
# The masking key version the sanitized fixtures were masked with.
LAB_KEY_VERSION = "k1"

# Device name: (platform, site).
DEVICES = {
    "core-sw1": ("ios-xe", "HQ"),
    "edge-fw1": ("fortios", "HQ"),
    "floor-sw1": ("ios", "Floor 2"),
}

# Engagement name: (status, days ago it started, days ago it ended, {device: config file}).
ENGAGEMENTS = {
    "Q2 review": (
        Engagement.Status.COMPLETED,
        200,
        190,
        {"core-sw1": "config.cfg", "edge-fw1": "config-weak.cfg"},
    ),
    "Q4 review": (
        Engagement.Status.ACTIVE,
        7,
        None,
        {"core-sw1": "config.cfg", "edge-fw1": "config.cfg", "floor-sw1": "config.cfg"},
    ),
}


def _outputs(folder: Path) -> dict[str, str]:
    """A device's outputs by command. Files are named after the command."""
    return {
        path.stem.replace("_", " "): path.read_text(encoding="utf-8").replace("\r\n", "\n")
        for path in sorted(folder.glob("*.txt"))
    }


class Command(BaseCommand):
    help = "Load the sanitized lab devices, configs and check results (development only)."

    def add_arguments(self, parser):
        parser.add_argument("--customer", default="Lab", help="Customer name. Must be new.")
        parser.add_argument("--user", required=True, help="Engineer username, for the audit log.")
        parser.add_argument(
            "--fixtures",
            type=Path,
            default=DEFAULT_FIXTURES,
            help="The sanitized fixtures folder (default: tests/fixtures/sanitized).",
        )

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("load_lab is for local development. Set CARTIO_DEBUG=1.")
        user = options["user"]
        if not get_user_model().objects.filter(username=user).exists():
            raise CommandError(f"No user {user}.")
        fixtures = options["fixtures"]
        if not fixtures.is_dir():
            raise CommandError(f"No fixtures folder at {fixtures}.")
        name = options["customer"]
        if Customer.objects.filter(name__iexact=name).exists():
            self.stdout.write(f"{name} is already loaded. Nothing to do.")
            return

        with transaction.atomic():
            customer = Customer.objects.create(name=name)
            self._created(customer, "customer", customer, user)
            sites = {}
            for site_name in sorted({site for _, site in DEVICES.values()}):
                site = Site.objects.for_customer(customer).create(name=site_name)
                self._created(customer, "site", site, user)
                sites[site_name] = site
            # The checks too, so a failed load leaves nothing behind and can simply be re-run.
            for item in ENGAGEMENTS.items():
                engagement = self._engagement(customer, sites, fixtures, user, *item)
                run = run_assessment(engagement, started_by=user)
                self.stdout.write(
                    f"{engagement.name}: {run.results.count()} check results "
                    f"on {engagement.devices.count()} devices."
                )
        self.stdout.write(f"Loaded the lab as {customer.name}.")

    def _engagement(self, customer, sites, fixtures, user, name, item):
        status, started, ended, configs = item
        today = timezone.localdate()
        engagement = Engagement.objects.for_customer(customer).create(
            name=name,
            status=status,
            start_date=today - datetime.timedelta(days=started),
            end_date=None if ended is None else today - datetime.timedelta(days=ended),
            assessment="Lab assessment loaded from the sanitized fixtures.",
        )
        self._created(customer, "engagement", engagement, user)
        source = Source.objects.for_customer(customer).create(
            engagement=engagement, kind=Source.Kind.UPLOAD, name="lab outputs", added_by=user
        )
        for device_name, config_file in configs.items():
            platform, site_name = DEVICES[device_name]
            folder = fixtures / platform / device_name
            device = store_outputs(source, platform, _outputs(folder), actor=user)
            device.site = sites[site_name]
            device.save()
            config = (folder / config_file).read_text(encoding="utf-8").replace("\r\n", "\n")
            if find_unmasked(config, platform):
                raise CommandError(f"{folder / config_file} has an unmasked secret.")
            store_config(source, device, platform, config, key_version=LAB_KEY_VERSION, actor=user)
        return engagement

    def _created(self, customer, object_type, record, user):
        audit.record(
            customer,
            AuditEvent.Action.RECORD_CREATED,
            object_type=object_type,
            object_id=record.pk,
            object_name=record.name,
            actor=user,
            via="load_lab",
        )
