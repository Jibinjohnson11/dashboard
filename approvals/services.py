"""Review decisions on submitted content (internal reviewers, admins and client approvers)."""
import logging

from pymongo import DESCENDING
from pymongo.errors import DuplicateKeyError

from approvals.repositories import approvals
from content import services as content_services
from content.repositories import content_versions, contents
from core import audit
from core.exceptions import ServiceError, WorkflowConflict
from core.permissions import REVIEW_ROLES, combine, content_scope, require
from core.utils import utcnow

logger = logging.getLogger("seo.approvals")

APPROVE = "approve"
REQUEST_CHANGES = "request_changes"
REJECT = "reject"
ACTIONS = {
    APPROVE: "approved",
    REQUEST_CHANGES: "needs_edit",
    REJECT: "rejected",
}
ACTION_LABELS = {APPROVE: "Approved", REQUEST_CHANGES: "Changes requested", REJECT: "Rejected"}
FILTER_CHOICES = [
    ("pending", "Pending Approval"),
    ("needs_edit", "Needs Edit"),
    ("approved", "Approved"),
    ("rejected", "Rejected"),
    ("published", "Published"),
    ("all", "All reviewed"),
]
MIN_FEEDBACK = 5


def list_approvals(principal, *, status="pending", q="", brand_id="", campaign_id="", page=1):
    require(principal.can_access("approvals"))
    base = {} if status != "all" else {"status": {"$in": ["pending", "approved", "needs_edit", "rejected", "published"]}}
    return content_services.list_content(
        principal,
        q=q,
        brand_id=brand_id,
        campaign_id=campaign_id,
        status="" if status == "all" else status,
        page=page,
        base_query=base,
        sort=[("submitted_at", DESCENDING)],
    )


def allowed_actions(principal):
    """Review actions this user's permissions allow (approve / reject / request_changes)."""
    if principal.role not in REVIEW_ROLES or not principal.can_access("approvals"):
        return []
    return [action for action in ACTIONS if principal.has_perm(f"approvals.{action}")]


def can_review(principal, item):
    """Active reviewers, admins and assigned clients, on pending content only, with at least one
    review permission. Reviewers and clients may never review their own work; admins may."""
    if principal.role not in REVIEW_ROLES or not principal.is_active or not allowed_actions(principal):
        return False
    if item["status"] != "pending":
        return False
    return not self_review_blocked(principal, item)


def self_review_blocked(principal, item):
    """True when the self-review rule stops this user. Admins are exempt."""
    if principal.is_admin:
        return False
    if item.get("writer_user_id") == principal.user_id:
        return True
    version = content_services.current_version(item)
    return bool(version and version.get("created_by_user_id") == principal.user_id)


def decide(principal, content_id, *, action, feedback, version_number):
    require(principal.can_access("approvals"))
    if action not in ACTIONS:
        raise ServiceError("Unknown review action.")
    require(principal.has_perm(f"approvals.{action}"), "You do not have permission to take this review action.")
    feedback = (feedback or "").strip()
    if action in (REQUEST_CHANGES, REJECT) and len(feedback) < MIN_FEEDBACK:
        raise ServiceError(
            "Please explain the changes you need." if action == REQUEST_CHANGES else "Please give a reason for rejecting this content."
        )
    # Visibility check doubles as the object-level authorisation (clients: assigned brands only).
    item = content_services.get_content(principal, content_id)
    if self_review_blocked(principal, item):
        raise ServiceError("You cannot review content you wrote.")
    require(principal.role in REVIEW_ROLES)
    if item["status"] != "pending":
        raise WorkflowConflict("This content has already been reviewed.")
    if version_number != item["current_version"] or version_number != item.get("submitted_version"):
        raise WorkflowConflict("A newer revision of this content exists. Please review the latest version.")
    version = content_versions.get_version(item["_id"], version_number)
    if version is None or not version.get("locked"):
        raise WorkflowConflict("This revision is not ready for review.")

    now = utcnow()
    new_status = ACTIONS[action]
    fields = {"status": new_status, "last_reviewed_at": now, "last_reviewed_by_user_id": principal.user_id, "updated_at": now}
    if action == APPROVE:
        fields.update(approved_version=version_number, approved_version_id=version["_id"], approved_at=now)
    # Atomic compare-and-set: succeeds for exactly one reviewer even under concurrent clicks.
    updated = contents.transition(
        combine(content_scope(principal), {"_id": item["_id"], "status": "pending", "current_version": version_number}),
        {"$set": fields, "$inc": {"edit_seq": 1}},
    )
    if updated is None:
        raise WorkflowConflict("This content was just reviewed by someone else.")
    try:
        approvals.insert({
            "content_id": item["_id"],
            "content_version_id": version["_id"],
            "version_number": version_number,
            "reviewer_user_id": principal.user_id,
            "reviewer_role": principal.role,
            "action": action,
            "feedback": feedback,
            "reviewed_at": now,
        })
    except DuplicateKeyError:
        logger.error("approvals.duplicate_decision", extra={"content_id": str(item["_id"]), "version": version_number})
    audit.record(principal.user_id, f"content.{action}", "content", item["_id"], {"version": version_number, "status": new_status})
    return updated


def approval_history(item):
    from teams.services import display_names

    history = approvals.for_content(item["_id"])
    names = display_names({h["reviewer_user_id"] for h in history})
    for entry in history:
        entry["reviewer_name"] = names.get(entry["reviewer_user_id"], "Unknown")
        entry["action_label"] = ACTION_LABELS.get(entry["action"], entry["action"])
    return history


def pending_count(principal):
    if not principal.can_access("approvals"):
        return 0
    query = {"status": "pending"}
    if not principal.is_admin:
        query["writer_user_id"] = {"$ne": principal.user_id}
    return contents.count(combine(content_scope(principal), query))
