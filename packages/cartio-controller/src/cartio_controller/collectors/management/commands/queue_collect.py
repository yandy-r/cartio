"""Queue a collect job on a collector (YAN-1753), until engagement setup (YAN-1735) launches one.

The targets file is JSON: a list of `{"host": ..., "platform": "ios" | "ios-xe" | "fortios",
"credential": <credential name>, "port": 22}`. Credentials are named, never given here: their
secrets stay in the controller's vault and reach the collector sealed inside the job.

An On site customer's collector keeps its own targets and credentials (YAN-1670), so its job
takes no targets file.
"""

import json
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from cartio_controller.collectors.jobs import JobError, queue_job
from cartio_controller.collectors.models import (
    Collector,
    Credential,
    Customer,
    Engagement,
    Job,
)
from cartio_controller.collectors.runs import RunNotAllowed


class Command(BaseCommand):
    help = "Queue a collect job: SSH to each target, mask, and store outputs and configs."

    def add_arguments(self, parser):
        parser.add_argument("--customer", required=True, help="Customer name.")
        parser.add_argument("--engagement", required=True, help="Engagement name.")
        parser.add_argument("--collector", required=True, help="Collector name.")
        parser.add_argument(
            "--targets", type=Path, help="Targets JSON file. Hosted customers only."
        )
        parser.add_argument("--user", required=True, help="Engineer username, for the audit log.")
        parser.add_argument(
            "--no-host-key-check",
            action="store_true",
            help="Don't require the devices' SSH host keys to be known to the collector.",
        )

    def handle(self, *args, **options):
        if not get_user_model().objects.filter(username=options["user"]).exists():
            raise CommandError(f"No user {options['user']}.")
        customer = Customer.objects.filter(name__iexact=options["customer"]).first()
        if customer is None:
            raise CommandError(f"No customer {options['customer']}.")
        engagement = (
            Engagement.objects.for_customer(customer)
            .filter(name__iexact=options["engagement"])
            .first()
        )
        if engagement is None:
            raise CommandError(f"{customer.name} has no engagement {options['engagement']}.")
        collector = (
            Collector.objects.for_customer(customer)
            .filter(name__iexact=options["collector"])
            .first()
        )
        if collector is None:
            raise CommandError(f"{customer.name} has no collector {options['collector']}.")
        params = {"verify_host_keys": not options["no_host_key_check"]}
        if customer.is_on_site:
            if options["targets"]:
                raise CommandError(
                    f"{customer.name} is On site: its targets are kept on its collector."
                )
            self._queue(collector, engagement, options["user"], params, "its on-site targets")
            return
        if not options["targets"]:
            raise CommandError("A Hosted customer's collect job needs --targets.")
        try:
            targets = json.loads(options["targets"].read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise CommandError(f"Can't read the targets file: {error}") from error
        if not isinstance(targets, list) or not all(isinstance(t, dict) for t in targets):
            raise CommandError("The targets file must be a JSON list of objects.")
        credentials = Credential.objects.for_customer(customer)
        for target in targets:
            name = target.get("credential")
            credential = credentials.filter(name=name).first() if isinstance(name, str) else None
            if credential is None:
                raise CommandError(f"{customer.name} has no credential {name!r}.")
            target["credential"] = str(credential.pk)
        params["targets"] = targets
        self._queue(collector, engagement, options["user"], params, f"{len(targets)} devices")

    def _queue(self, collector, engagement, user, params, what):
        try:
            job = queue_job(collector, Job.Type.COLLECT, user, engagement=engagement, params=params)
        except (JobError, RunNotAllowed) as error:
            raise CommandError(str(error)) from error
        self.stdout.write(f"Queued collect job {job.pk} on {collector.name} for {what}.")
