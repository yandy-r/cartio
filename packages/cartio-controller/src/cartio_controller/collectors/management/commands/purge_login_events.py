from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from cartio_controller.collectors import audit


class Command(BaseCommand):
    help = (
        "Delete sign-in log entries past the retention period (CARTIO_LOGIN_LOG_DAYS, 365 days "
        "by default). Run it daily, for example from cron. The customer audit log is never purged."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--older-than-days",
            type=int,
            default=settings.LOGIN_LOG_DAYS,
            help="Keep this many days of the sign-in log.",
        )

    def handle(self, *args, **options):
        days = options["older_than_days"]
        try:
            deleted = audit.purge_login_events(days)
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f"Deleted {deleted} sign-in log entries older than {days} days.")
