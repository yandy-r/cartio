from django.core.management.base import BaseCommand
from django.db import transaction

from cartio_controller.collectors.models import Collector, Customer


class Command(BaseCommand):
    help = "Create a collector for a customer and print its one-time enrollment token."

    def add_arguments(self, parser):
        parser.add_argument("--customer", required=True, help="Customer name. Created if new.")
        parser.add_argument("--name", required=True, help="Collector name, unique per customer.")

    def handle(self, *args, **options):
        with transaction.atomic():
            customer, _ = Customer.objects.get_or_create(name=options["customer"])
            collector = Collector(customer=customer, name=options["name"])
            token = collector.issue_enrollment_token()
            collector.save()

        self.stdout.write(f"Collector {collector.id} created for {customer.name}.")
        self.stdout.write("Enrollment token (shown once, valid for one enrollment):")
        self.stdout.write(token)
