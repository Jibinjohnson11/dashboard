from datetime import timedelta

from pymongo import DESCENDING

from approvals.repositories import approvals
from brands.repositories import brands
from campaigns.repositories import campaigns
from content.repositories import contents
from content.services import STATUS_CHOICES, hydrate
from core.permissions import TEAM_MEMBER, brand_scope, campaign_read_scope, campaign_scope, combine, content_scope, publication_scope
from core.utils import search_regex, utcnow
from publications.repositories import publications

CHART_DAYS = 30


def summary(principal):
    from teams.services import team_member_count

    counts = contents.status_counts(content_scope(principal))
    return {
        "brands": brands.count(combine(brand_scope(principal), {"status": {"$ne": "archived"}})),
        "active_campaigns": campaigns.count(combine(campaign_read_scope(principal), {"status": "active"})),
        "team_members": team_member_count(principal),
        "draft": counts.get("draft", 0) + counts.get("needs_edit", 0),
        "pending": counts.get("pending", 0),
        "approved": counts.get("approved", 0),
        "rejected": counts.get("rejected", 0),
        "published": publications.count(publication_scope(principal)),
        "status_counts": counts,
    }


def charts(principal, status_counts):
    today = utcnow().date()
    since = utcnow().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=CHART_DAYS - 1)
    daily = publications.daily_counts(publication_scope(principal), since)
    days = [today - timedelta(days=offset) for offset in range(CHART_DAYS - 1, -1, -1)]

    by_campaign = contents.status_counts_by("campaign_id", combine(content_scope(principal), {"status": {"$in": ["approved", "published"]}}))
    top = sorted(by_campaign.items(), key=lambda kv: -(kv[1].get("approved", 0) + kv[1].get("published", 0)))[:8]
    names = {c["_id"]: c["name"] for c in campaigns.find({"_id": {"$in": [cid for cid, _ in top]}})}

    return {
        "status": {
            "labels": [label for _, label in STATUS_CHOICES],
            "keys": [key for key, _ in STATUS_CHOICES],
            "values": [status_counts.get(key, 0) for key, _ in STATUS_CHOICES],
            "rows": [{"label": label, "value": status_counts.get(key, 0)} for key, label in STATUS_CHOICES],
        },
        "daily": {
            "labels": [d.strftime("%b %d") for d in days],
            "values": [daily.get(d.isoformat(), 0) for d in days],
        },
        "campaigns": {
            "labels": [names.get(cid, "Campaign") for cid, _ in top],
            "approved": [c.get("approved", 0) for _, c in top],
            "published": [c.get("published", 0) for _, c in top],
        },
    }


def recent_activity(principal, limit=5):
    from teams.services import display_names

    scope = content_scope(principal)
    submitted = hydrate(contents.find(combine(scope, {"submitted_at": {"$ne": None}, "status": {"$ne": "draft"}}), sort=[("submitted_at", DESCENDING)], limit=limit))
    visible_ids = [c["_id"] for c in contents.find(scope, projection={"_id": 1}, sort=[("updated_at", DESCENDING)], limit=500)]
    reviews = approvals.find({"content_id": {"$in": visible_ids}}, sort=[("reviewed_at", DESCENDING)], limit=limit)
    review_content = {c["_id"]: c for c in contents.find({"_id": {"$in": [r["content_id"] for r in reviews]}})}
    names = display_names({r["reviewer_user_id"] for r in reviews})
    for review in reviews:
        review["content"] = review_content.get(review["content_id"])
        review["reviewer_name"] = names.get(review["reviewer_user_id"], "Unknown")

    published = publications.find(publication_scope(principal), sort=[("published_at", DESCENDING)], limit=limit)
    brand_query = combine(brand_scope(principal), {"status": {"$ne": "archived"}})
    return {
        "brands": brands.find(brand_query, sort=[("created_at", DESCENDING)], limit=limit),
        "campaigns": campaigns.find(campaign_scope(principal), sort=[("created_at", DESCENDING)], limit=limit)
        if principal.can_access("campaigns") else [],
        "submitted": submitted,
        "reviews": reviews,
        "published": published,
    }


def notification_summary(principal):
    """Small badge in the top bar: what needs this user's attention."""
    from approvals.services import pending_count

    items = []
    if principal.can_access("approvals"):
        n = pending_count(principal)
        if n:
            items.append({"label": f"{n} item{'s' if n != 1 else ''} waiting for review", "url_name": "approvals:list"})
    if principal.role == TEAM_MEMBER or principal.is_admin:
        n = contents.count({"writer_user_id": principal.user_id, "status": "needs_edit"})
        if n:
            items.append({"label": f"{n} item{'s' if n != 1 else ''} returned for changes", "url_name": "content:list", "query": "?status=needs_edit"})
        n = contents.count(combine(content_scope(principal), {"status": "approved"}))
        if n:
            items.append({"label": f"{n} approved item{'s' if n != 1 else ''} ready to publish", "url_name": "publications:create"})
    if principal.is_admin:
        from teams.repositories import team_profiles

        n = team_profiles.count({"status": "pending"})
        if n:
            items.append({"label": f"{n} account{'s' if n != 1 else ''} awaiting activation", "url_name": "teams:list", "query": "?status=pending"})
    return {"items": items, "count": len(items)}


def global_search(principal, q, limit=10):
    regex = search_regex(q)
    if not regex:
        return {"brands": [], "campaigns": [], "content": [], "publications": []}
    result = {
        "brands": brands.find(combine(brand_scope(principal), {"name": regex}), limit=limit),
        "campaigns": [],
        "content": [],
        "publications": publications.find(combine(publication_scope(principal), {"$or": [{"title": regex}, {"published_url": regex}]}), limit=limit),
    }
    if principal.can_access("campaigns"):
        result["campaigns"] = campaigns.find(
            combine(campaign_scope(principal), {"$or": [{"name": regex}, {"primary_keyword": regex}]}), limit=limit
        )
    if principal.can_access("content") or principal.can_access("approvals"):
        result["content"] = hydrate(contents.find(combine(content_scope(principal), {"$or": [{"title": regex}, {"keyword": regex}]}), limit=limit))
    return result
