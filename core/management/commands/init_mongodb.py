from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.mongo import ensure_indexes, get_db, ping


class Command(BaseCommand):
    help = "Create MongoDB collections and indexes (safe to run repeatedly)."

    def handle(self, *args, **options):
        if settings.MONGODB_USE_MOCK:
            self.stdout.write(self.style.WARNING("MONGODB_USE_MOCK is enabled: indexes are created in memory only."))
        try:
            ping()
        except Exception as exc:
            raise CommandError(f"Cannot reach MongoDB at the configured MONGODB_URI: {exc.__class__.__name__}: {exc}")
        count = ensure_indexes()
        db = get_db()
        self.stdout.write(self.style.SUCCESS(f"MongoDB '{db.name}' ready: {count} indexes ensured across {len(db.list_collection_names())} collections."))
