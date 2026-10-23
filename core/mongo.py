"""MongoDB connection management and index initialisation.

All business data lives in MongoDB. The client is created lazily, once per
process. When ``MONGODB_USE_MOCK`` is enabled (always the case under
``manage.py test``) an in-memory mongomock client is used instead, so tests can
never touch a real database.
"""
import logging
import threading

from django.conf import settings
from pymongo import ASCENDING, DESCENDING, MongoClient

logger = logging.getLogger("seo.mongo")

_client = None
_lock = threading.Lock()

COLLECTIONS = (
    "brands",
    "campaigns",
    "team_profiles",
    "brand_access",
    "campaign_assignments",
    "contents",
    "content_versions",
    "content_approvals",
    "publications",
    "audit_logs",
    "role_permissions",
)


def _create_client():
    if settings.MONGODB_USE_MOCK:
        import mongomock

        logger.warning("mongo.mock_enabled", extra={"detail": "Using in-memory MongoDB; data is not persisted."})
        return mongomock.MongoClient(tz_aware=True)
    return MongoClient(
        settings.MONGODB_URI,
        tz_aware=True,
        serverSelectionTimeoutMS=settings.MONGODB_TIMEOUT_MS,
        connectTimeoutMS=settings.MONGODB_TIMEOUT_MS,
        appname="seo-management-dashboard",
    )


def get_client():
    global _client
    if _client is None:
        with _lock:
            if _client is None:
                _client = _create_client()
                if settings.MONGODB_USE_MOCK:
                    ensure_indexes(_client[settings.MONGODB_DB_NAME])
    return _client


def get_db():
    return get_client()[settings.MONGODB_DB_NAME]


def reset_client():
    """Drop the cached client. Used by tests to get a fresh in-memory database."""
    global _client
    with _lock:
        if _client is not None and not settings.MONGODB_USE_MOCK:
            _client.close()
        _client = None


def ping():
    get_client().admin.command("ping")


# (collection, keys, options)
INDEXES = [
    ("brands", [("name_lower", ASCENDING)], {"unique": True, "name": "uniq_brand_name"}),
    ("brands", [("status", ASCENDING), ("created_at", DESCENDING)], {"name": "status_created"}),
    ("campaigns", [("brand_id", ASCENDING), ("name_lower", ASCENDING)], {"unique": True, "name": "uniq_campaign_per_brand"}),
    ("campaigns", [("status", ASCENDING), ("created_at", DESCENDING)], {"name": "status_created"}),
    ("campaigns", [("assigned_user_ids", ASCENDING)], {"name": "assigned_users"}),
    ("team_profiles", [("django_user_id", ASCENDING)], {"unique": True, "name": "uniq_django_user"}),
    ("team_profiles", [("role", ASCENDING), ("status", ASCENDING)], {"name": "role_status"}),
    ("brand_access", [("django_user_id", ASCENDING), ("brand_id", ASCENDING)], {"unique": True, "name": "uniq_user_brand"}),
    ("brand_access", [("brand_id", ASCENDING)], {"name": "brand"}),
    ("campaign_assignments", [("campaign_id", ASCENDING), ("django_user_id", ASCENDING)], {"unique": True, "name": "uniq_campaign_user"}),
    ("campaign_assignments", [("django_user_id", ASCENDING)], {"name": "user"}),
    ("contents", [("brand_id", ASCENDING), ("status", ASCENDING)], {"name": "brand_status"}),
    ("contents", [("campaign_id", ASCENDING), ("status", ASCENDING)], {"name": "campaign_status"}),
    ("contents", [("writer_user_id", ASCENDING), ("status", ASCENDING)], {"name": "writer_status"}),
    ("contents", [("updated_at", DESCENDING)], {"name": "updated"}),
    ("contents", [("submitted_at", DESCENDING)], {"name": "submitted"}),
    ("content_versions", [("content_id", ASCENDING), ("version_number", ASCENDING)], {"unique": True, "name": "uniq_content_version"}),
    # One review decision per submitted version: blocks duplicate approval actions.
    ("content_approvals", [("content_version_id", ASCENDING)], {"unique": True, "name": "uniq_decision_per_version"}),
    ("content_approvals", [("content_id", ASCENDING), ("reviewed_at", DESCENDING)], {"name": "content_reviewed"}),
    ("content_approvals", [("reviewer_user_id", ASCENDING)], {"name": "reviewer"}),
    # A content item can be recorded as published once.
    ("publications", [("content_id", ASCENDING)], {"unique": True, "name": "uniq_publication_per_content"}),
    ("publications", [("brand_id", ASCENDING), ("published_at", DESCENDING)], {"name": "brand_published"}),
    ("publications", [("campaign_id", ASCENDING), ("published_at", DESCENDING)], {"name": "campaign_published"}),
    ("publications", [("published_at", DESCENDING)], {"name": "published"}),
    ("role_permissions", [("role", ASCENDING)], {"unique": True, "name": "uniq_role"}),
    ("audit_logs", [("created_at", DESCENDING)], {"name": "created"}),
    ("audit_logs", [("entity_type", ASCENDING), ("entity_id", ASCENDING)], {"name": "entity"}),
    ("audit_logs", [("actor_user_id", ASCENDING), ("created_at", DESCENDING)], {"name": "actor"}),
]


def ensure_indexes(db=None):
    db = db if db is not None else get_db()
    for name in COLLECTIONS:
        if name not in db.list_collection_names():
            db.create_collection(name)
    for collection, keys, options in INDEXES:
        db[collection].create_index(keys, **options)
    return len(INDEXES)
