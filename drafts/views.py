from django.contrib import messages
from django.shortcuts import redirect, render

from approvals.services import approval_history
from brands.services import brand_choices
from campaigns.services import campaign_filter_choices
from content import services as content_services
from core.permissions import module_required
from publications.services import can_publish
from core.utils import page_number, valid_oid_or_blank
from drafts import services
from publications.forms import PublicationFilterForm


def _writer_filter(value):
    # Results are always scoped by content_scope, so any integer id is safe to filter on.
    try:
        writer_id = int(value)
    except (TypeError, ValueError):
        return None
    return writer_id if writer_id > 0 else None


@module_required("drafts")
def draft_list(request):
    principal = request.principal
    status = request.GET.get("status", "")
    if status not in services.REVIEWED_STATUSES:
        status = ""
    content_type = request.GET.get("type", "")
    if content_type not in content_services.CONTENT_TYPE_LABELS:
        content_type = ""
    dates = PublicationFilterForm(request.GET)
    dates.is_valid()
    filters = {
        "q": request.GET.get("q", ""),
        "brand_id": valid_oid_or_blank(request.GET.get("brand")),
        "campaign_id": valid_oid_or_blank(request.GET.get("campaign")),
        "content_type": content_type,
        "status": status,
        "writer_id": _writer_filter(request.GET.get("writer")),
        "date_from": dates.cleaned_data.get("date_from"),
        "date_to": dates.cleaned_data.get("date_to"),
    }
    page = services.list_reviewed(principal, page=page_number(request), **filters)
    return render(request, "drafts/list.html", {
        "page": page,
        "filters": filters,
        "counts": services.status_counts(principal),
        "status_tabs": services.STATUS_TABS,
        "brand_choices": brand_choices(principal, include_inactive=True),
        "campaign_choices": campaign_filter_choices(principal),
        "type_choices": content_services.CONTENT_TYPES,
        "writer_choices": services.writer_choices(principal),
        "has_filters": any(filters[k] for k in ("q", "brand_id", "campaign_id", "content_type", "writer_id", "date_from", "date_to")),
    })


@module_required("drafts")
def draft_detail(request, content_id):
    principal = request.principal
    item = services.get_reviewed(principal, content_id)
    if item["status"] not in services.REVIEWED_STATUSES:
        # Resubmitted (pending again), published, or never reviewed: send the user to the right place.
        messages.info(request, f"“{item['title']}” is now {item['status'].replace('_', ' ')} and no longer in Content Drafts.")
        if item["status"] == "pending" and principal.can_access("approvals"):
            return redirect("approvals:review", content_id=item["id"])
        if principal.can_access("content"):
            return redirect("content:detail", content_id=item["id"])
        return redirect("drafts:list")
    history = approval_history(item)
    return render(request, "drafts/detail.html", {
        "item": item,
        "version": content_services.current_version(item),
        "history": history,
        "latest": history[0] if history else None,
        # Edit & resubmit goes through Content Writing, so it needs both permissions.
        "can_edit": principal.has_perm("drafts.edit") and principal.can_access("content") and content_services.can_edit(principal, item),
        "can_publish": item["status"] == "approved" and can_publish(principal),
        "show_internal_notes": principal.role != "client",
    })
