from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from cartio_controller.collectors.assessments import AssessmentNotAllowed, run_assessment
from cartio_controller.collectors.models import Customer, Engagement, OnSiteDataError


class Command(BaseCommand):
    help = "Run the assessment checks on an engagement's collected data and store the results."

    def add_arguments(self, parser):
        parser.add_argument("--customer", required=True, help="Customer name.")
        parser.add_argument("--engagement", required=True, help="Engagement name.")
        parser.add_argument("--user", required=True, help="Engineer username, for the audit log.")

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
        try:
            run = run_assessment(engagement, started_by=options["user"])
        except (AssessmentNotAllowed, OnSiteDataError) as error:
            raise CommandError(str(error)) from error

        counts = {}
        for status in run.results.values_list("status", flat=True):
            counts[status] = counts.get(status, 0) + 1
        summary = ", ".join(f"{count} {status}" for status, count in sorted(counts.items()))
        self.stdout.write(f"Assessment run {run.pk} for {customer.name} / {engagement.name}.")
        self.stdout.write(f"Results: {summary or 'no devices'}.")
