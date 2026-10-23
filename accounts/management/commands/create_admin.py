import getpass

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

    def handle(self, *args, **options):
        try:
            ping()
        except Exception as exc:
            raise CommandError(f"MongoDB is not reachable. Check MONGODB_URI ({exc.__class__.__name__}).")

        User = get_user_model()
        email = normalize_email(options["email"] or input("Email: "))
        name = (options["name"] or input("Full name: ")).strip()
        if not email or not name:
            raise CommandError("Email and full name are required.")
        if User.objects.filter(email__iexact=email).exists():
            raise CommandError("A user with that email already exists.")

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
