import getpass
import os

from django.contrib.auth import get_user_model, password_validation
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.models import normalize_email
from core.mongo import ping
from core.permissions import ACTIVE, ADMIN
from teams.repositories import team_profiles


class Command(BaseCommand):
    help = "Create the first administrator (Django auth user + MongoDB admin profile)."

    def add_arguments(self, parser):
        parser.add_argument("--email", help="Admin email address")
        parser.add_argument("--name", help="Admin full name")
        parser.add_argument(
            "--noinput", action="store_true",
            help="Read DJANGO_ADMIN_EMAIL, DJANGO_ADMIN_NAME and DJANGO_ADMIN_PASSWORD from the environment "
                 "(for hosts without an interactive shell). Does nothing if the admin already exists.",
        )

    def handle(self, *args, **options):
        try:
            ping()
        except Exception as exc:
            raise CommandError(f"MongoDB is not reachable. Check MONGODB_URI ({exc.__class__.__name__}).")

        User = get_user_model()
        if options["noinput"]:
            email = normalize_email(options["email"] or os.environ.get("DJANGO_ADMIN_EMAIL", ""))
            name = (options["name"] or os.environ.get("DJANGO_ADMIN_NAME", "")).strip()
            password = os.environ.get("DJANGO_ADMIN_PASSWORD", "")
            if not (email and name and password):
                raise CommandError("Set DJANGO_ADMIN_EMAIL, DJANGO_ADMIN_NAME and DJANGO_ADMIN_PASSWORD.")
            if User.objects.filter(email__iexact=email).exists():
                self.stdout.write("Admin already exists; nothing to do.")  # safe to leave in a release step
                return
        else:
            email = normalize_email(options["email"] or input("Email: "))
            name = (options["name"] or input("Full name: ")).strip()
            if not email or not name:
                raise CommandError("Email and full name are required.")
            if User.objects.filter(email__iexact=email).exists():
                raise CommandError("A user with that email already exists.")

        if not options["noinput"]:
            password = getpass.getpass("Password: ")
            if password != getpass.getpass("Password (again): "):
                raise CommandError("Passwords do not match.")
        try:
            password_validation.validate_password(password, User(email=email, full_name=name))
        except ValidationError as exc:
            raise CommandError(" ".join(exc.messages))

        with transaction.atomic():
            user = User.objects.create_superuser(email=email, password=password, full_name=name)
        try:
            team_profiles.insert({"django_user_id": user.pk, "display_name": name, "email": email, "role": ADMIN, "status": ACTIVE})
        except Exception:
            user.delete()
            raise CommandError("Could not create the MongoDB profile; the admin user was not created.")
        self.stdout.write(self.style.SUCCESS(f"Admin {email} created. Sign in at /accounts/login/."))
