import getpass

from django.core.management.base import BaseCommand, CommandError

from cartio_controller.collectors import rotation


class Command(BaseCommand):
    help = (
        "Rewrap every customer data key and engineer authenticator key with a new master key. "
        "Set CARTIO_MASTER_KEY_FILE to the new key and CARTIO_PREVIOUS_MASTER_KEY_FILE to the old "
        "one first (docs/deploy/master-key.md). Safe to run again if it stops partway."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--actor",
            default="",
            help="Who to name in each customer's audit log. Defaults to the system user.",
        )

    def handle(self, *args, **options):
        actor = options["actor"] or f"{getpass.getuser()} (rotate_master_key)"
        try:
            result = rotation.rotate(actor)
        except rotation.RotationError as exc:
            raise CommandError(
                f"{exc} Keys rotated before this stay rotated; run the command again to go on."
            ) from exc
        self.stdout.write(
            f"Rewrapped {result.data_keys} customer data keys and {result.engineer_keys} "
            "engineer authenticator keys with the new master key."
        )
        left = rotation.remaining()
        if left:
            raise CommandError(f"{left} keys still aren't on the new master key. Run it again.")
        self.stdout.write(
            "Every key is on the new master key. Unset CARTIO_PREVIOUS_MASTER_KEY_FILE and restart "
            "the controller. Keep the old key's backup while older database backups exist."
        )
