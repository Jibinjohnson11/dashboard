"""Content Drafts: a read-only, filtered view of content that has received a review decision.

No data is copied or moved. It queries the existing ``contents`` collection for the
reviewed statuses, scoped by the same ``content_scope`` as every other module. When
content is resubmitted its status returns to ``pending`` and it drops out of this view
automatically; its decisions stay in ``content_approvals``.
"""
from datetime import timedelta

from pymongo import DESCENDING

from content import services as content_services
from content.repositories import contents
from core.permissions import combine, content_scope
from core.utils import date_to_datetime, parse_oid

REVIEWED_STATUSES = ("approved", "rejected", "needs_edit")
STATUS_TABS = [("", "All Reviewed"), ("approved", "Approved"), ("rejected", "Rejected"), ("needs_edit", "Needs Edit")]


def _base_query(*, status="", writer_id=None, date_from=None, date_to=None):
    query = {"status": status if status in REVIEWED_STATUSES else {"$in": list(REVIEWED_STATUSES)}}
    if writer_id is not None:
        query["writer_user_id"] = writer_id
    if date_from or date_to:
        query["last_reviewed_at"] = {}
        if date_from:
            query["last_reviewed_at"]["$gte"] = date_to_datetime(date_from)
        if date_to:
            query["last_reviewed_at"]["$lt"] = date_to_datetime(date_to) + timedelta(days=1)
    return query


def list_reviewed(principal, *, q="", brand_id="", campaign_id="", content_type="", status="", writer_id=None,
                  date_from=None, date_to=None, page=1):
    from teams.services import display_names

    result = content_services.list_content(
        principal,
        q=q,
        brand_id=brand_id,
        campaign_id=campaign_id,
        content_type=content_type,
        page=page,
        base_query=_base_query(status=status, writer_id=writer_id, date_from=date_from, date_to=date_to),
        sort=[("last_reviewed_at", DESCENDING), ("updated_at", DESCENDING)],
    )
    names = display_names({c.get("last_reviewed_by_user_id") for c in result.items})
    for item in result.items:
        item["reviewed_by_name"] = names.get(item.get("last_reviewed_by_user_id"), "—")
    return result


def status_counts(principal):
    counts = contents.status_counts(combine(content_scope(principal), {"status": {"$in": list(REVIEWED_STATUSES)}}))
    result = {status: counts.get(status, 0) for status in REVIEWED_STATUSES}
    result["total"] = sum(result.values())
    return result


def writer_choices(principal):
    """Writers who have reviewed content visible to this user."""
    from teams.services import display_names

    rows = contents.aggregate([
        {"$match": combine(content_scope(principal), {"status": {"$in": list(REVIEWED_STATUSES)}})},
        {"$group": {"_id": "$writer_user_id"}},
    ])
    names = display_names({row["_id"] for row in rows if row["_id"] is not None})
    return sorted(names.items(), key=lambda kv: kv[1].lower())


def get_reviewed(principal, content_id):
    """Scoped fetch. Raises NotFound for invisible content; returns the item whatever its status
    so the view can redirect content that has since been resubmitted or published."""
    return content_services.get_content(principal, parse_oid(content_id, not_found=True))
