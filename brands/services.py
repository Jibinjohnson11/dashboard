from pymongo import DESCENDING
from pymongo.errors import DuplicateKeyError

from brands.repositories import brands
from campaigns.repositories import campaigns
from core import audit
from core.exceptions import NotFound, ServiceError
from core.permissions import brand_scope, campaign_scope, combine, content_scope, publication_scope, require
from core.utils import parse_oid, search_regex, utcnow

STATUS_CHOICES = [("active", "Active"), ("inactive", "Inactive")]
FILTER_STATUS_CHOICES = STATUS_CHOICES + [("archived", "Archived")]
EDITABLE_FIELDS = ("name", "website", "industry", "contact_name", "contact_email", "contact_phone", "description", "status")


def list_brands(principal, *, q="", status="", page=1):
    query = {}
    if status:
        query["status"] = status
    else:
        query["status"] = {"$ne": "archived"}
    regex = search_regex(q)
    if regex:
        query["$or"] = [{"name": regex}, {"industry": regex}, {"website": regex}]
    result = brands.find_page(combine(brand_scope(principal), query), page=page, sort=[("name_lower", 1)])
    counts = campaigns.active_counts_by_brand([b["_id"] for b in result.items])
    for brand in result.items:
        brand["active_campaigns"] = counts.get(brand["_id"], 0)
    return result


def get_brand(principal, brand_id):
    brand = brands.find_one(combine(brand_scope(principal), {"_id": parse_oid(brand_id, not_found=True)}))
    if brand is None:
        raise NotFound("Brand not found.")
    return brand


def _clean(data):
    doc = {field: data.get(field) or "" for field in EDITABLE_FIELDS}
    doc["name_lower"] = doc["name"].lower()
    if doc["status"] not in dict(STATUS_CHOICES):
        raise ServiceError("Invalid brand status.")
    return doc


def create_brand(principal, data):
    require(principal.is_admin and principal.has_perm("brands.create"))
    doc = _clean(data)
    doc.update(created_by_user_id=principal.user_id, archived_at=None)
    try:
        brand = brands.insert(doc)
    except DuplicateKeyError:
        raise ServiceError("A brand with this name already exists.")
    audit.record(principal.user_id, "brand.created", "brand", brand["_id"], {"name": brand["name"]})
    return brand


def update_brand(principal, brand_id, data):
    require(principal.is_admin and principal.has_perm("brands.edit"))
    brand = get_brand(principal, brand_id)
    if brand["status"] == "archived":
        raise ServiceError("Archived brands cannot be edited. Restore the brand first.")
    doc = _clean(data)
    try:
        brands.update(brand["_id"], doc)
    except DuplicateKeyError:
        raise ServiceError("A brand with this name already exists.")
    changed = [f for f in EDITABLE_FIELDS if brand.get(f) != doc.get(f)]
    audit.record(principal.user_id, "brand.updated", "brand", brand["_id"], {"fields": changed})
    return get_brand(principal, brand_id)


def archive_brand(principal, brand_id):
    """Soft delete: the brand and all its history stay in the database."""
    require(principal.is_admin and principal.has_perm("brands.delete"))
    brand = get_brand(principal, brand_id)
    if brand["status"] == "archived":
        raise ServiceError("This brand is already archived.")
    brands.update(brand["_id"], {"status": "archived", "status_before_archive": brand["status"], "archived_at": utcnow()})
    audit.record(principal.user_id, "brand.archived", "brand", brand["_id"])


def restore_brand(principal, brand_id):
    require(principal.is_admin and principal.has_perm("brands.delete"))
    brand = get_brand(principal, brand_id)
    if brand["status"] != "archived":
        raise ServiceError("This brand is not archived.")
    brands.update(brand["_id"], {"status": brand.get("status_before_archive") or "active", "archived_at": None})
    audit.record(principal.user_id, "brand.restored", "brand", brand["_id"])


def brand_detail(principal, brand):
    from content.repositories import contents
    from publications.repositories import publications
    from teams.repositories import brand_access, team_profiles
    from teams.services import display_names

    brand_campaigns = campaigns.find(
        combine(campaign_scope(principal), {"brand_id": brand["_id"]}), sort=[("created_at", DESCENDING)]
    )
    member_ids = set(brand_access.user_ids_for_brand(brand["_id"]))
    for campaign in campaigns.find({"brand_id": brand["_id"]}):
        member_ids.update(campaign.get("assigned_user_ids", []))
    names = display_names(member_ids)
    profiles = team_profiles.by_user_ids(member_ids)
    members = sorted(
        [{"user_id": uid, "name": names.get(uid, "Unknown"), "role": profiles.get(uid, {}).get("role")} for uid in member_ids],
        key=lambda m: m["name"].lower(),
    )
    if principal.role == "client":
        members = [m for m in members if m["role"] != "client" or m["user_id"] == principal.user_id]

    scope = combine(content_scope(principal), {"brand_id": brand["_id"]})
    counts = contents.status_counts(scope)
    return {
        "campaigns": brand_campaigns,
        "members": members,
        "content_total": sum(counts.values()),
        "pending_total": counts.get("pending", 0),
        "published_total": publications.count(combine(publication_scope(principal), {"brand_id": brand["_id"]})),
    }


def brand_choices(principal, *, include_inactive=False):
    query = {"status": "active"} if not include_inactive else {"status": {"$ne": "archived"}}
    return [(b["id"], b["name"]) for b in brands.find(combine(brand_scope(principal), query), sort=[("name_lower", 1)])]
