from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from cartio_controller.collectors import audit
from cartio_controller.collectors.models import AuditEvent, Collector, Customer


def _audit_customer_created(customer: Customer) -> None:
    audit.record(
        customer,
        AuditEvent.Action.CUSTOMER_CREATED,
        object_type="customer",
        object_id=customer.pk,
        object_name=customer.name,
        data_mode=customer.data_mode,
        via="create_collector",
    )


class Command(BaseCommand):
    help = "Create a collector for a customer and print its one-time enrollment token."

    def add_arguments(self, parser):
        parser.add_argument("--customer", required=True, help="Customer name. Created if new.")
        parser.add_argument("--name", required=True, help="Collector name, unique per customer.")

    def handle(self, *args, **options):
        with transaction.atomic():
            # Customer names are unique ignoring case, so look them up the same way.
            customer = Customer.objects.filter(name__iexact=options["customer"]).first()
            created = customer is None
            if created:
                customer = Customer.objects.create(name=options["customer"])
            if customer.is_archived:
                raise CommandError(f"Customer {customer.name} is archived. Restore it first.")
            collector = Collector(customer=customer, name=options["name"])
            token = collector.issue_enrollment_token()
            collector.save()
            if created:
                _audit_customer_created(customer)
            audit.record(
                customer,
                AuditEvent.Action.COLLECTOR_CREATED,
                object_type="collector",
                object_id=collector.pk,
                object_name=collector.name,
                via="create_collector",
            )

        self.stdout.write(f"Collector {collector.id} created for {customer.name}.")
        self.stdout.write("Enrollment token (shown once, valid for one enrollment):")
        self.stdout.write(token)
