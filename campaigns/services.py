from pymongo import DESCENDING
from pymongo.errors import DuplicateKeyError

from brands.repositories import brands
from campaigns.repositories import campaign_assignments, campaigns
from core import audit
from core.exceptions import NotFound, ServiceError
from core.permissions import ACTIVE, INVITED, TEAM_MEMBER, brand_scope, campaign_read_scope, campaign_scope, combine, content_scope, publication_scope, require
from core.utils import date_to_datetime, parse_oid, search_regex, utcnow

STATUS_CHOICES = [("draft", "Draft"), ("active", "Active"), ("paused", "Paused"), ("completed", "Completed")]
FILTER_STATUS_CHOICES = STATUS_CHOICES + [("archived", "Archived")]
# Campaigns that still accept new content.
WRITABLE_STATUSES = ("draft", "active")


def _hydrate(items):
    from teams.services import display_names

    brand_map = brands.by_ids(c["brand_id"] for c in items)
    names = display_names({uid for c in items for uid in c.get("assigned_user_ids", [])} | {c.get("created_by_user_id") for c in items})
    for campaign in items:
        campaign["brand"] = brand_map.get(campaign["brand_id"])
        campaign["landing_page_url"] = landing_url(campaign)
        campaign["title"] = display_title(campaign)
        campaign["created_by_name"] = names.get(campaign.get("created_by_user_id"), "")
        campaign["members"] = [names.get(uid, "Unknown") for uid in campaign.get("assigned_user_ids", [])]
    return items


def list_campaigns(principal, *, q="", brand_id="", status="", page=1):
    query = {"status": status} if status else {"status": {"$ne": "archived"}}
    if brand_id:
        query["brand_id"] = parse_oid(brand_id)
    regex = search_regex(q)
    if regex:
        query["$or"] = [{"name": regex}, {"primary_keyword": regex}, {"secondary_keywords": regex},
                        {"landing_page_url": regex}, {"target_url": regex}, {"target_location": regex}]
    result = campaigns.find_page(combine(campaign_scope(principal), query), page=page, sort=[("created_at", DESCENDING)])
    _hydrate(result.items)
    from publications.repositories import publications

    counts = {row["_id"]: row["n"] for row in publications.aggregate([
        {"$match": combine(publication_scope(principal), {"campaign_id": {"$in": [c["_id"] for c in result.items]}})},
        {"$group": {"_id": "$campaign_id", "n": {"$sum": 1}}},
    ])}
    for campaign in result.items:
        campaign["backlinks"] = counts.get(campaign["_id"], 0)
    return result


def get_campaign(principal, campaign_id):
    campaign = campaigns.find_one(combine(campaign_scope(principal), {"_id": parse_oid(campaign_id, not_found=True)}))
    if campaign is None:
        raise NotFound("Campaign not found.")
    _hydrate([campaign])
    return campaign


def _validate_brand(principal, brand_id):
    brand = brands.find_one(combine(brand_scope(principal), {"_id": parse_oid(brand_id)}))
    if brand is None:
        raise ServiceError("The selected brand does not exist.")
    if brand["status"] == "archived":
        raise ServiceError("Campaigns cannot be added to an archived brand.")
    return brand


def _validate_members(user_ids):
    """Only active (or invited) team members may be assigned to campaigns."""
    from teams.repositories import team_profiles

    user_ids = set(user_ids)
    if not user_ids:
        return []
    valid = {
        p["django_user_id"]
        for p in team_profiles.find({
            "django_user_id": {"$in": list(user_ids)},
            "role": TEAM_MEMBER,
            "status": {"$in": [ACTIVE, INVITED]},
        })
    }
    if valid != user_ids:
        raise ServiceError("Only active team members can be assigned to a campaign.")
    return sorted(user_ids)


def landing_url(campaign):
    """Landing page URL, falling back to the legacy ``target_url`` field on older campaigns."""
    return campaign.get("landing_page_url") or campaign.get("target_url") or ""


def display_title(campaign):
    """Cards and headings show the primary keyword; older campaigns without one show their name."""
    return campaign.get("primary_keyword") or campaign.get("name") or "Campaign"


def _derived_name(brand_id, data):
    """Internal campaign name (users don't type one). Uses the primary keyword, else the landing
    page path/host, else the location. Made unique per brand; never rewritten afterwards."""
    from urllib.parse import urlparse

    label = data.get("primary_keyword") or ""
    url = data.get("landing_page_url") or ""
    parsed = urlparse(url) if url and not label else None
    if parsed and parsed.netloc:
        path = parsed.path.strip("/")
        label = path.replace("/", " / ").replace("-", " ") if path else parsed.netloc.removeprefix("www.")
    base = (label or data.get("target_location") or "Campaign")[:110]
    name, n = base, 2
    while campaigns.col.find_one({"brand_id": brand_id, "name_lower": name.lower()}, {"_id": 1}):
        name, n = f"{base} ({n})", n + 1
    return name


def _clean(data, existing_start=None):
    if data.get("status") not in dict(STATUS_CHOICES):
        raise ServiceError("Invalid campaign status.")
    start, end = data.get("start_date") or existing_start, data.get("end_date")
    if start and end and end < start:
        raise ServiceError("End date cannot be before the start date.")
    url = data.get("landing_page_url") or data.get("target_url") or ""
    doc = {
        "primary_keyword": (data.get("primary_keyword") or "").strip(),
        "secondary_keywords": data.get("secondary_keywords") or [],
        "landing_page_url": url,
        "target_url": url,  # legacy field kept in sync for backward compatibility
        "target_location": data.get("target_location") or "",
        "end_date": date_to_datetime(end),
        "status": data["status"],
    }
    # Only overwrite fields the caller actually supplied (the form no longer edits these).
    if "start_date" in data:
        doc["start_date"] = date_to_datetime(start)
    if "description" in data:
        doc["description"] = data.get("description") or ""
    return doc


def _sync_assignments(campaign_id, user_ids):
    campaign_assignments.replace_for_campaign(campaign_id, user_ids)
    campaigns.col.update_one({"_id": campaign_id}, {"$set": {"assigned_user_ids": list(user_ids)}})


def create_campaign(principal, data):
    require(principal.is_admin and principal.has_perm("campaigns.create"))
    brand = _validate_brand(principal, data["brand_id"])
    members = _validate_members(data.get("assigned_user_ids", []))
    doc = _clean(data, existing_start=utcnow().date())  # new campaigns start today unless told otherwise
    # Name and primary keyword are no longer form inputs. An explicit name is still accepted
    # from internal callers (seed data); otherwise one is derived. Existing names are never rewritten.
    name = (data.get("name") or "").strip() or _derived_name(brand["_id"], dict(data, landing_page_url=doc["landing_page_url"]))
    doc.setdefault("start_date", date_to_datetime(utcnow().date()))
    doc.setdefault("description", "")
    doc.update(
        name=name,
        name_lower=name.lower(),
        brand_id=brand["_id"],
        assigned_user_ids=members,
        created_by_user_id=principal.user_id,
    )
    try:
        campaign = campaigns.insert(doc)
    except DuplicateKeyError:
        raise ServiceError("This brand already has a campaign with that name.")
    _sync_assignments(campaign["_id"], members)
    audit.record(principal.user_id, "campaign.created", "campaign", campaign["_id"], {"brand_id": str(brand["_id"])})
    return campaign


def update_campaign(principal, campaign_id, data):
    require(principal.is_admin and principal.has_perm("campaigns.edit"))
    campaign = get_campaign(principal, campaign_id)
    if campaign["status"] == "archived":
        raise ServiceError("Archived campaigns cannot be edited.")
    members = _validate_members(data.get("assigned_user_ids", []))
    existing_start = campaign.get("start_date")
    doc = _clean(data, existing_start=existing_start.date() if existing_start else None)
    try:
        campaigns.update(campaign["_id"], doc)  # brand_id is immutable: the campaign stays with its brand
    except DuplicateKeyError:
        raise ServiceError("This brand already has a campaign with that name.")
    _sync_assignments(campaign["_id"], members)
    audit.record(principal.user_id, "campaign.updated", "campaign", campaign["_id"], {
        "status": [campaign["status"], doc["status"]],
        "members_added": len(set(members) - set(campaign.get("assigned_user_ids", []))),
        "members_removed": len(set(campaign.get("assigned_user_ids", [])) - set(members)),
    })


def archive_campaign(principal, campaign_id):
    require(principal.is_admin and principal.has_perm("campaigns.delete"))
    campaign = get_campaign(principal, campaign_id)
    if campaign["status"] == "archived":
        raise ServiceError("This campaign is already archived.")
    campaigns.update(campaign["_id"], {"status": "archived", "status_before_archive": campaign["status"], "archived_at": utcnow()})
    audit.record(principal.user_id, "campaign.archived", "campaign", campaign["_id"])


def restore_campaign(principal, campaign_id):
    require(principal.is_admin and principal.has_perm("campaigns.delete"))
    campaign = get_campaign(principal, campaign_id)
    if campaign["status"] != "archived":
        raise ServiceError("This campaign is not archived.")
    campaigns.update(campaign["_id"], {"status": campaign.get("status_before_archive") or "paused", "archived_at": None})
    audit.record(principal.user_id, "campaign.restored", "campaign", campaign["_id"])


def campaign_content(principal, campaign):
    from content.repositories import contents
    from teams.services import display_names

    items = contents.find(
        combine(content_scope(principal), {"campaign_id": campaign["_id"]}), sort=[("updated_at", DESCENDING)], limit=200
    )
    names = display_names({c.get("writer_user_id") for c in items})
    groups = {"draft": [], "pending": [], "approved": [], "published": []}
    for item in items:
        item["writer_name"] = names.get(item.get("writer_user_id"), "—")
        key = "draft" if item["status"] in ("draft", "needs_edit") else item["status"]
        if key in groups:
            groups[key].append(item)
    return groups


def writable_campaigns(principal):
    """Campaigns the principal may create content in, with their brand."""
    if not principal.can_access("content"):
        return []
    query = combine(campaign_scope(principal), {"status": {"$in": list(WRITABLE_STATUSES)}})
    items = campaigns.find(query, sort=[("name_lower", 1)])
    active_brands = brands.by_ids(c["brand_id"] for c in items)
    return [c for c in items if c["brand_id"] in active_brands and active_brands[c["brand_id"]]["status"] == "active"]


def campaign_filter_choices(principal):
    """Campaign options for filter dropdowns."""
    return [(c["id"], c["name"]) for c in campaigns.find(campaign_read_scope(principal), sort=[("name_lower", 1)])]
