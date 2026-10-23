"""Append-only audit trail stored in the ``audit_logs`` collection."""
import logging

from core.repository import Repository
from core.utils import utcnow

logger = logging.getLogger("seo.audit")

# Never write these into audit metadata.
_SENSITIVE_KEYS = {"password", "password1", "password2", "token", "uid", "secret"}


class AuditLogRepository(Repository):
    collection_name = "audit_logs"
    timestamp_fields = ("created_at",)


audit_logs = AuditLogRepository()


def record(actor_user_id, action, entity_type, entity_id=None, metadata=None):
    metadata = {k: v for k, v in (metadata or {}).items() if k not in _SENSITIVE_KEYS}
    doc = {
        "actor_user_id": actor_user_id,
        "action": action,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "metadata": metadata,
        "created_at": utcnow(),
    }
    try:
        audit_logs.col.insert_one(doc)
    except Exception:  # The business action already succeeded; never fail it on audit write.
        logger.exception("audit.write_failed", extra={"action": action, "entity_type": entity_type})
    logger.debug("audit.%s", action, extra={"actor": actor_user_id, "entity_type": entity_type, "entity_id": str(entity_id)})
