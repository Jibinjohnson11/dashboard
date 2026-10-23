"""Content writing workflow.

State machine (``contents.status``)::

    draft ──submit──▶ pending ──approve──▶ approved ──publish──▶ published
      ▲                 │  │                  │
      │                 │  └─reject──▶ rejected (final)
      │                 └─request changes──▶ needs_edit ──submit──▶ pending
      └──────────── revise (new version) ◀────┘

* Every submission locks the submitted version; any later edit creates version n+1,
  so a reviewer's decision always refers to exactly one immutable version.
* Every transition is a single ``find_one_and_update`` whose filter includes the
  expected current state (status, version and an edit sequence number). If someone
  else changed the record first, the filter no longer matches and the action fails
  with a WorkflowConflict instead of silently overwriting their change.
"""
import logging

from django.contrib.auth import get_user_model
from pymongo import DESCENDING
from pymongo.errors import DuplicateKeyError, PyMongoError

from brands.repositories import brands
from campaigns.repositories import campaigns
from campaigns.services import writable_campaigns
from content.repositories import content_versions, contents
from core import audit
from core.exceptions import NotFound, ServiceError, WorkflowConflict
from core.permissions import ACTIVE, ADMIN, TEAM_MEMBER, Principal, combine, content_scope, require
from core.sanitize import sanitize_html, word_count
from core.utils import parse_oid, search_regex, utcnow

logger = logging.getLogger("seo.content")

CONTENT_TYPES = [
    ("blog", "Blog"),
    ("article", "Article"),
    ("social_bookmarking", "Social Bookmarking"),
    ("guest_post", "Guest Post"),
    ("directory_submission", "Directory Submission"),
    ("other", "Other"),
]
CONTENT_TYPE_LABELS = dict(CONTENT_TYPES)
STATUS_CHOICES = [
    ("draft", "Draft"),
    ("pending", "Pending Approval"),
    ("approved", "Approved"),
    ("needs_edit", "Needs Edit"),
    ("rejected", "Rejected"),
    ("published", "Published"),
]
EDITABLE_STATUSES = ("draft", "needs_edit")
MAX_BODY_LENGTH = 200_000


# --------------------------------------------------------------------------- queries

def hydrate(items):
    from teams.services import display_names

    brand_map = brands.by_ids(c["brand_id"] for c in items)
    campaign_map = campaigns.by_ids(c["campaign_id"] for c in items)
    names = display_names({c.get("writer_user_id") for c in items})
    for item in items:
        item["brand"] = brand_map.get(item["brand_id"])
        item["campaign"] = campaign_map.get(item["campaign_id"])
        item["writer_name"] = names.get(item.get("writer_user_id"), "—")
        item["content_type_label"] = CONTENT_TYPE_LABELS.get(item.get("content_type"), "Other")
    return items


def list_content(principal, *, q="", brand_id="", campaign_id="", status="", content_type="", page=1, base_query=None, sort=None):
    query = dict(base_query or {})
    if status:
        query["status"] = status
    if brand_id:
        query["brand_id"] = parse_oid(brand_id)
    if campaign_id:
        query["campaign_id"] = parse_oid(campaign_id)
    if content_type:
        query["content_type"] = content_type
    regex = search_regex(q)
    if regex:
        query["$or"] = [{"title": regex}, {"keyword": regex}]
    result = contents.find_page(combine(content_scope(principal), query), page=page, sort=sort or [("updated_at", DESCENDING)])
    hydrate(result.items)
    return result


def get_content(principal, content_id):
    """Fetch a content item the principal may see; anything else is a 404 (no IDOR leakage)."""
    item = contents.find_one(combine(content_scope(principal), {"_id": parse_oid(content_id, not_found=True)}))
    if item is None:
        raise NotFound("Content not found.")
    hydrate([item])
    return item


def current_version(item):
    return content_versions.get_version(item["_id"], item["current_version"])


def get_version(principal, content_id, version_number):
    item = get_content(principal, content_id)
    version = content_versions.get_version(item["_id"], int(version_number))
    if version is None:
        raise NotFound("Version not found.")
    return item, version


def version_history(item):
    return content_versions.for_content(item["_id"])


# --------------------------------------------------------------------------- permissions

def can_edit(principal, item):
    if item["status"] not in EDITABLE_STATUSES or not principal.has_perm("content.edit"):
        return False
    return principal.is_admin or (principal.role == TEAM_MEMBER and item.get("writer_user_id") == principal.user_id)


def can_revise(principal, item):
    if item["status"] != "approved" or not principal.has_perm("content.edit"):
        return False
    return principal.is_admin or (principal.role == TEAM_MEMBER and item.get("writer_user_id") == principal.user_id)


def _writable_campaign(principal, campaign_id):
    campaign_id = parse_oid(campaign_id)
    for campaign in writable_campaigns(principal):
        if campaign["_id"] == campaign_id:
            return campaign
    raise ServiceError("You cannot add content to the selected campaign.")


def writer_choices(principal):
    """Admins may assign any active team member (or themselves) as the writer."""
    from teams.repositories import team_profiles

    if not principal.is_admin:
        return [(principal.user_id, principal.display_name)]
    profiles = team_profiles.find({"role": {"$in": [TEAM_MEMBER, ADMIN]}, "status": ACTIVE}, sort=[("display_name", 1)])
    return [(p["django_user_id"], p.get("display_name") or p.get("email")) for p in profiles]


def _validate_writer(principal, writer_id, campaign):
    if not principal.is_admin:
        return principal.user_id
    if writer_id is None:
        return principal.user_id
    if writer_id not in {uid for uid, _ in writer_choices(principal)}:
        raise ServiceError("The selected writer is not an active team member.")
    from teams.repositories import team_profiles

    profile = team_profiles.for_user(writer_id)
    if profile["role"] == TEAM_MEMBER:
        writer = Principal(get_user_model().objects.get(pk=writer_id), profile)
        if campaign["_id"] not in writer.campaign_ids:
            raise ServiceError("The selected writer is not assigned to this campaign.")
    return writer_id


def _clean_body(body_html):
    body = sanitize_html(body_html)
    if len(body) > MAX_BODY_LENGTH:
        raise ServiceError("The content body is too long.")
    return body


# --------------------------------------------------------------------------- writes

def can_create(principal):
    return principal.can_access("content") and principal.has_perm("content.create")


def create_content(principal, data):
    require(can_create(principal), "You do not have permission to create content.")
    campaign = _writable_campaign(principal, data["campaign_id"])
    if str(campaign["brand_id"]) != str(data["brand_id"]):
        raise ServiceError("The selected campaign does not belong to the selected brand.")
    if data.get("content_type") not in CONTENT_TYPE_LABELS:
        raise ServiceError("Please choose a valid content type.")
    writer_id = _validate_writer(principal, data.get("writer_user_id"), campaign)
    body = _clean_body(data.get("body_html", ""))
    now = utcnow()

    item = contents.insert({
        "brand_id": campaign["brand_id"],
        "campaign_id": campaign["_id"],
        "title": data["title"],
        "keyword": data["keyword"],
        "content_type": data["content_type"],
        "current_version": 1,
        "status": "draft",
        "edit_seq": 0,
        "writer_user_id": writer_id,
        "created_by_user_id": principal.user_id,
        "word_count": word_count(body),
        "submitted_at": None,
        "approved_version": None,
        "approved_version_id": None,
    })
    try:
        content_versions.insert({
            "content_id": item["_id"],
            "version_number": 1,
            "body_html": body,
            "internal_notes": data.get("internal_notes", ""),
            "word_count": word_count(body),
            "created_by_user_id": principal.user_id,
            "locked": False,
            "updated_at": now,
        })
    except PyMongoError:
        contents.col.delete_one({"_id": item["_id"]})  # compensate: never leave content without a version
        logger.exception("content.create_version_failed")
        raise ServiceError("The content could not be saved. Please try again.")
    audit.record(principal.user_id, "content.created", "content", item["_id"], {"campaign_id": str(campaign["_id"])})
    return contents.get(item["_id"])


def save_content(principal, content_id, data, *, expected_seq, submit=False):
    """Save edits to a draft / needs-edit item, optionally submitting it for approval."""
    item = get_content(principal, content_id)
    if not can_edit(principal, item):
        raise WorkflowConflict("This content is no longer editable. It may have been submitted or reviewed.")
    if data.get("content_type") not in CONTENT_TYPE_LABELS:
        raise ServiceError("Please choose a valid content type.")
    body = _clean_body(data.get("body_html", ""))
    version = current_version(item)
    words = word_count(body)
    needs_new_version = version is None or version.get("locked")
    new_version_number = item["current_version"] + 1 if needs_new_version else item["current_version"]

    fields = {
        "title": data["title"],
        "keyword": data["keyword"],
        "content_type": data["content_type"],
        "word_count": words,
        "current_version": new_version_number,
        "updated_at": utcnow(),
    }
    if principal.is_admin and data.get("writer_user_id"):
        fields["writer_user_id"] = _validate_writer(principal, data["writer_user_id"], item["campaign"])
    updated = contents.transition(
        {"_id": item["_id"], "status": {"$in": list(EDITABLE_STATUSES)}, "edit_seq": expected_seq},
        {"$set": fields, "$inc": {"edit_seq": 1}},
    )
    if updated is None:
        raise WorkflowConflict("Someone else changed this content while you were editing. Reload the page and try again.")

    if needs_new_version:
        try:
            content_versions.insert({
                "content_id": item["_id"],
                "version_number": new_version_number,
                "body_html": body,
                "internal_notes": data.get("internal_notes", ""),
                "word_count": words,
                "created_by_user_id": principal.user_id,
                "based_on_version": item["current_version"],
                "locked": False,
                "updated_at": utcnow(),
            })
        except (DuplicateKeyError, PyMongoError):
            contents.col.update_one(
                {"_id": item["_id"], "current_version": new_version_number},
                {"$set": {"current_version": item["current_version"]}},
            )
            logger.exception("content.new_version_failed")
            raise ServiceError("The new revision could not be saved. Please try again.")
    else:
        content_versions.col.update_one(
            {"_id": version["_id"], "locked": False},
            {"$set": {"body_html": body, "internal_notes": data.get("internal_notes", ""), "word_count": words, "updated_at": utcnow()}},
        )
    audit.record(principal.user_id, "content.saved", "content", item["_id"], {"version": new_version_number})
    if submit:
        submit_content(principal, item["_id"], expected_seq=updated["edit_seq"])
    return contents.get(item["_id"])


def submit_content(principal, content_id, *, expected_seq):
    item = get_content(principal, content_id)
    if not can_edit(principal, item):
        raise WorkflowConflict("This content cannot be submitted in its current state.")
    version = current_version(item)
    if version is None or version.get("locked"):
        raise WorkflowConflict("Save your changes as a new revision before resubmitting.")
    if word_count(version.get("body_html")) == 0:
        raise ServiceError("Add some content before submitting it for approval.")
    if not item["title"].strip():
        raise ServiceError("A title is required before submitting.")
    now = utcnow()
    updated = contents.transition(
        {
            "_id": item["_id"],
            "status": {"$in": list(EDITABLE_STATUSES)},
            "current_version": version["version_number"],
            "edit_seq": expected_seq,
        },
        {
            "$set": {"status": "pending", "submitted_at": now, "submitted_version": version["version_number"], "updated_at": now},
            "$inc": {"edit_seq": 1},
        },
    )
    if updated is None:
        raise WorkflowConflict("This content was already submitted or changed by someone else.")
    content_versions.col.update_one({"_id": version["_id"]}, {"$set": {"locked": True, "submitted_at": now}})
    audit.record(principal.user_id, "content.submitted", "content", item["_id"], {"version": version["version_number"]})
    return updated


def create_revision(principal, content_id, *, expected_seq):
    """Start a new draft version of approved content. It must be reviewed again before publishing."""
    item = get_content(principal, content_id)
    if not can_revise(principal, item):
        raise WorkflowConflict("Only approved, unpublished content can be revised.")
    base = current_version(item)
    new_number = item["current_version"] + 1
    updated = contents.transition(
        {"_id": item["_id"], "status": "approved", "current_version": item["current_version"], "edit_seq": expected_seq},
        {
            "$set": {
                "status": "draft",
                "current_version": new_number,
                "approved_version": None,
                "approved_version_id": None,
                "updated_at": utcnow(),
            },
            "$inc": {"edit_seq": 1},
        },
    )
    if updated is None:
        raise WorkflowConflict("This content changed while you were viewing it. Reload and try again.")
    content_versions.insert({
        "content_id": item["_id"],
        "version_number": new_number,
        "body_html": base["body_html"] if base else "",
        "internal_notes": base.get("internal_notes", "") if base else "",
        "word_count": base.get("word_count", 0) if base else 0,
        "created_by_user_id": principal.user_id,
        "based_on_version": item["current_version"],
        "locked": False,
        "updated_at": utcnow(),
    })
    audit.record(principal.user_id, "content.revision_started", "content", item["_id"], {"version": new_number})
    return updated


def content_counts(principal, extra=None):
    counts = contents.status_counts(combine(content_scope(principal), extra or {}))
    return {key: counts.get(key, 0) for key, _ in STATUS_CHOICES}

