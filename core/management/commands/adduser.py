import secrets

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from core.roles import ROLES, assign_role


class Command(BaseCommand):
    help = ("Create a user with a role (viewer, editor, admin) and a one-time password. "
            "At first login the user must set up two-factor authentication.")

    def add_arguments(self, parser):
        parser.add_argument("username")
        parser.add_argument("--email", default="")
        parser.add_argument("--first-name", default="")
        parser.add_argument("--last-name", default="")
        parser.add_argument("--role", choices=ROLES, default="viewer")

    def handle(self, username, email, first_name, last_name, role, **options):
        User = get_user_model()
        if User.objects.filter(username=username).exists():
            raise CommandError(f"User {username!r} already exists.")
        password = secrets.token_urlsafe(12)
        user = User.objects.create_user(username, email, password,
                                        first_name=first_name, last_name=last_name)
        assign_role(user, role)
        self.stdout.write(self.style.SUCCESS(f"Created {username} ({role})."))
        self.stdout.write(f"One-time password: {password}")
        self.stdout.write("Send it privately; ask the user to change it after the first login.")
