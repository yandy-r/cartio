from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from cartio_controller.engineers.models import EngineerMFA


class Command(BaseCommand):
    help = (
        "Give an engineer a one-time enrollment code to add their authenticator app. "
        "Needed before their first sign-in, and to replace a lost authenticator."
    )

    def add_arguments(self, parser):
        parser.add_argument("username")
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Replace an authenticator the engineer already has (lost or new phone).",
        )

    def handle(self, *args, **options):
        user = get_user_model()._default_manager.filter(username=options["username"]).first()
        if user is None:
            raise CommandError(f"No account named {options['username']}.")
        if not (user.is_active and user.is_staff):
            raise CommandError("Only active staff accounts can sign in to the controller.")
        current = EngineerMFA.objects.filter(user=user).first()
        if current is not None and current.confirmed_at is not None and not options["reset"]:
            raise CommandError(
                f"{user.get_username()} already has an authenticator. Pass --reset to replace it."
            )
        mfa, token = EngineerMFA.issue(user)
        self.stdout.write(f"Enrollment code for {user.get_username()}: {token}")
        self.stdout.write(
            f"It works once, until {mfa.enrollment_expires_at:%Y-%m-%d %H:%M} UTC. Give it to "
            "them directly. They sign in with their password, then enter it."
        )
