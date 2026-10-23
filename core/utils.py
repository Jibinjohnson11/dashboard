import math
import re
from datetime import date, datetime, time, timezone

from bson import ObjectId
from bson.errors import InvalidId
from django.conf import settings
from django.core.exceptions import ValidationError

from core.exceptions import NotFound


def utcnow():
    return datetime.now(timezone.utc)


def parse_oid(value, *, not_found=False):
    """Convert user input into an ObjectId, raising ValidationError (or NotFound) when invalid."""
    if isinstance(value, ObjectId):
        return value
    try:
        return ObjectId(str(value).strip())
    except (InvalidId, TypeError):
        if not_found:
            raise NotFound("Record not found.")
        raise ValidationError("Invalid identifier.")


def valid_oid_or_blank(value):
    """For optional filter parameters: return the value if it is a valid ObjectId, else ''."""
    value = (value or "").strip()
    return value if ObjectId.is_valid(value) else ""


def parse_oid_list(values):
    return [parse_oid(v) for v in values if str(v).strip()]


def date_to_datetime(value):
    """Store calendar dates as midnight UTC datetimes (BSON has no date-only type)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return datetime.combine(value, time.min, tzinfo=timezone.utc)


def datetime_to_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


def search_regex(query, max_length=100):
    """Case-insensitive, fully escaped regex for user search input (no regex injection)."""
    query = (query or "").strip()[:max_length]
    if not query:
        return None
    return {"$regex": re.escape(query), "$options": "i"}


def with_id(doc):
    """Expose ``_id`` as ``id`` (templates cannot read attributes starting with an underscore)."""
    if doc is not None and "_id" in doc:
        doc["id"] = str(doc["_id"])
    return doc


def page_number(request, param="page"):
    try:
        return max(1, int(request.GET.get(param, 1)))
    except (TypeError, ValueError):
        return 1


class Page:
    def __init__(self, items, number, per_page, total):
        self.items = items
        self.per_page = per_page
        self.total = total
        self.num_pages = max(1, math.ceil(total / per_page)) if per_page else 1
        self.number = min(max(1, number), self.num_pages)

    def __iter__(self):
        return iter(self.items)

    def __len__(self):
        return len(self.items)

    @property
    def has_previous(self):
        return self.number > 1

    @property
    def has_next(self):
        return self.number < self.num_pages

    @property
    def previous_page_number(self):
        return self.number - 1

    @property
    def next_page_number(self):
        return self.number + 1

    @property
    def start_index(self):
        return 0 if self.total == 0 else (self.number - 1) * self.per_page + 1

    @property
    def end_index(self):
        return min(self.number * self.per_page, self.total)

    @property
    def page_range(self):
        start = max(1, self.number - 2)
        end = min(self.num_pages, self.number + 2)
        return range(start, end + 1)


def default_page_size():
    return getattr(settings, "PAGE_SIZE", 15)


def client_ip(request):
    if getattr(settings, "TRUST_X_FORWARDED_FOR", False):
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "unknown")
