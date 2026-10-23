"""Recording where approved content was published (this app never posts to external sites)."""
import csv
import logging
from datetime import datetime, time, timedelta, timezone

from pymongo import DESCENDING
from pymongo.errors import DuplicateKeyError, PyMongoError

from brands.repositories import brands
from campaigns.repositories import campaigns
from content.repositories import content_versions, contents
from content.services import CONTENT_TYPE_LABELS, get_content
from core import audit
from core.exceptions import NotFound, ServiceError, WorkflowConflict
from core.permissions import ACTIVE, ADMIN, PUBLISH_ROLES, TEAM_MEMBER, combine, content_scope, publication_scope, require
from core.utils import date_to_datetime, parse_oid, search_regex, utcnow
from publications.repositories import publications

logger = logging.getLogger("seo.publications")


def can_publish(principal):
    return principal.is_active and principal.role in PUBLISH_ROLES and principal.has_perm("published.create")


def can_edit_publication(principal, publication):
    if not principal.has_perm("published.edit"):
        return False
    if principal.is_admin:
        return True
    return principal.role == TEAM_MEMBER and principal.user_id in (
        publication.get("recorded_by_user_id"),
        publication.get("published_by_user_id"),
    )


def _hydrate(items):
    from teams.services import display_names

    brand_map = brands.by_ids(p["brand_id"] for p in items)
    campaign_map = campaigns.by_ids(p["campaign_id"] for p in items)
    names = display_names({p.get("published_by_user_id") for p in items} | {p.get("recorded_by_user_id") for p in items})
    for item in items:
        item["brand"] = brand_map.get(item["brand_id"])
        item["campaign"] = campaign_map.get(item["campaign_id"])
        item["published_by_name"] = names.get(item.get("published_by_user_id"), "—")
        item["recorded_by_name"] = names.get(item.get("recorded_by_user_id"), "—")
        item["content_type_label"] = CONTENT_TYPE_LABELS.get(item.get("content_type"), "Other")
    return items


def _query(principal, *, q="", brand_id="", campaign_id="", date_from=None, date_to=None):
    query = {}
    if brand_id:
        query["brand_id"] = parse_oid(brand_id)
    if campaign_id:
        query["campaign_id"] = parse_oid(campaign_id)
    if date_from or date_to:
        query["published_at"] = {}
        if date_from:
            query["published_at"]["$gte"] = date_to_datetime(date_from)
        if date_to:
            query["published_at"]["$lt"] = date_to_datetime(date_to) + timedelta(days=1)
    regex = search_regex(q)
    if regex:
        query["$or"] = [{"title": regex}, {"keyword": regex}, {"published_url": regex}]
    return combine(publication_scope(principal), query)


def list_publications(principal, *, page=1, **filters):
    result = publications.find_page(_query(principal, **filters), page=page, sort=[("published_at", DESCENDING)])
    _hydrate(result.items)
    return result


def get_publication(principal, publication_id):
    item = publications.find_one(combine(publication_scope(principal), {"_id": parse_oid(publication_id, not_found=True)}))
    if item is None:
        raise NotFound("Publication not found.")
    _hydrate([item])
    return item


def publishable_content(principal):
    """Approved content (in the principal's scope) that has no publication record yet."""
    if not can_publish(principal):
        return []
    items = contents.find(combine(content_scope(principal), {"status": "approved"}), sort=[("approved_at", DESCENDING)], limit=500)
    brand_map = brands.by_ids(c["brand_id"] for c in items)
    for item in items:
        item["brand"] = brand_map.get(item["brand_id"])
    return items


def publisher_choices():
    from teams.repositories import team_profiles

    profiles = team_profiles.find({"role": {"$in": [ADMIN, TEAM_MEMBER]}, "status": ACTIVE}, sort=[("display_name", 1)])
    return [(p["django_user_id"], p.get("display_name") or p.get("email")) for p in profiles]


def _validate_publisher(user_id):
    if user_id not in {uid for uid, _ in publisher_choices()}:
        raise ServiceError("Choose an active team member as the publisher.")
    return user_id


def _published_datetime(value):
    if value is None:
        return utcnow()
    if value > utcnow().date():
        raise ServiceError("The published date cannot be in the future.")
    # Keep the calendar date the user picked; record noon UTC so it doesn't shift across time zones.
    return datetime.combine(value, time(12, 0), tzinfo=timezone.utc)


def _check_url_unused(url, exclude_id=None):
    query = {"published_url": url}
    if exclude_id is not None:
        query["_id"] = {"$ne": exclude_id}
    if publications.col.find_one(query, {"_id": 1}):
        raise ServiceError("This URL is already recorded for another publication.")


def publish(principal, *, content_id, published_url, published_by_user_id, published_date, notes=""):
    require(can_publish(principal), "Only admins and team members can record publications.")
    item = get_content(principal, content_id)
    if item["status"] == "published":
        raise WorkflowConflict("This content has already been recorded as published.")
    if item["status"] != "approved" or not item.get("approved_version_id"):
        raise ServiceError("Only approved content can be published.")
    if item.get("approved_version") != item["current_version"]:
        raise WorkflowConflict("The approved version is out of date. A newer revision must be approved first.")
    version = content_versions.get(item["approved_version_id"])
    if version is None or version["version_number"] != item["approved_version"]:
        raise ServiceError("The approved version could not be found.")
    _validate_publisher(published_by_user_id)
    _check_url_unused(published_url)
    published_at = _published_datetime(published_date)

    # Step 1: atomically move approved -> published (only one request can win).
    updated = contents.transition(
        combine(content_scope(principal), {"_id": item["_id"], "status": "approved", "approved_version_id": version["_id"]}),
        {"$set": {"status": "published", "published_at": published_at, "updated_at": utcnow()}, "$inc": {"edit_seq": 1}},
    )
    if updated is None:
        raise WorkflowConflict("This content was just published or changed by someone else.")
    # Step 2: write the publication record; roll step 1 back if it fails.
    try:
        publication = publications.insert({
            "content_id": item["_id"],
            "approved_version_id": version["_id"],
            "approved_version": version["version_number"],
            "brand_id": item["brand_id"],
            "campaign_id": item["campaign_id"],
            "title": item["title"],
            "keyword": item["keyword"],
            "content_type": item["content_type"],
            "writer_user_id": item.get("writer_user_id"),
            "published_url": published_url,
            "published_by_user_id": published_by_user_id,
            "recorded_by_user_id": principal.user_id,
            "published_at": published_at,
            "notes": notes,
        })
    except (DuplicateKeyError, PyMongoError) as exc:
        contents.col.update_one(
            {"_id": item["_id"], "status": "published"},
            {"$set": {"status": "approved", "published_at": None}},
        )
        logger.exception("publications.insert_failed")
        if isinstance(exc, DuplicateKeyError):
            raise WorkflowConflict("This content has already been recorded as published.")
        raise ServiceError("The publication could not be saved. Please try again.")
    audit.record(principal.user_id, "publication.created", "publication", publication["_id"], {
        "content_id": str(item["_id"]),
        "version": version["version_number"],
        "url": published_url,
    })
    return publication


def update_publication(principal, publication_id, *, published_url, published_by_user_id, published_date, notes=""):
    publication = get_publication(principal, publication_id)
    require(can_edit_publication(principal, publication), "You cannot edit this publication.")
    _validate_publisher(published_by_user_id)
    _check_url_unused(published_url, exclude_id=publication["_id"])
    fields = {
        "published_url": published_url,
        "published_by_user_id": published_by_user_id,
        "published_at": _published_datetime(published_date),
        "notes": notes,
    }
    changes = {
        key: [str(publication.get(key)), str(value)]
        for key, value in fields.items()
        if publication.get(key) != value
    }
    publications.update(publication["_id"], fields)
    contents.col.update_one({"_id": publication["content_id"]}, {"$set": {"published_at": fields["published_at"]}})
    audit.record(principal.user_id, "publication.updated", "publication", publication["_id"], {"changes": changes})


def publication_history(publication):
    from teams.services import display_names

    entries = audit.audit_logs.find(
        {"entity_type": "publication", "entity_id": publication["_id"]}, sort=[("created_at", DESCENDING)], limit=50
    )
    names = display_names({e["actor_user_id"] for e in entries})
    for entry in entries:
        entry["actor_name"] = names.get(entry["actor_user_id"], "Unknown")
    return entries


def _csv_safe(value):
    """Neutralise spreadsheet formula injection in exported cells."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def write_csv(principal, response, **filters):
    writer = csv.writer(response)
    writer.writerow(["Title", "Brand", "Campaign", "Keyword", "Content Type", "Published URL", "Published By", "Published Date", "Version", "Notes"])
    items = publications.find(_query(principal, **filters), sort=[("published_at", DESCENDING)], limit=10000)
    for item in _hydrate(items):
        writer.writerow([_csv_safe(v) for v in (
            item["title"],
            (item.get("brand") or {}).get("name", ""),
            (item.get("campaign") or {}).get("name", ""),
            item.get("keyword"),
            item["content_type_label"],
            item["published_url"],
            item["published_by_name"],
            item["published_at"].date().isoformat() if item.get("published_at") else "",
            item.get("approved_version"),
            item.get("notes"),
        )])
    audit.record(principal.user_id, "publication.exported", "publication", None, {"rows": len(items)})
