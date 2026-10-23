from django.contrib import messages
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from approvals.services import approval_history
from brands.services import brand_choices
from campaigns.services import campaign_filter_choices, writable_campaigns
from content import services
from publications.services import can_publish
from content.forms import ContentForm
from core.exceptions import ServiceError, WorkflowConflict
from core.permissions import module_required, require
from core.utils import page_number, valid_oid_or_blank


def _with_brands(campaign_list):
    from brands.repositories import brands

    brand_map = brands.by_ids(c["brand_id"] for c in campaign_list)
    for campaign in campaign_list:
        campaign["brand"] = brand_map.get(campaign["brand_id"])
    return campaign_list


def _form_campaigns(principal, item=None):
    items = writable_campaigns(principal)
    if item is not None and not any(c["_id"] == item["campaign_id"] for c in items) and item.get("campaign"):
        items.append(item["campaign"])  # keep the existing (locked) campaign selectable on edit
    return _with_brands(items)


@module_required("content")
def content_list(request):
    status = request.GET.get("status", "")
    if status not in dict(services.STATUS_CHOICES):
        status = ""
    content_type = request.GET.get("type", "")
    if content_type not in services.CONTENT_TYPE_LABELS:
        content_type = ""
    filters = {
        "q": request.GET.get("q", ""),
        "brand_id": valid_oid_or_blank(request.GET.get("brand")),
        "campaign_id": valid_oid_or_blank(request.GET.get("campaign")),
        "status": status,
        "content_type": content_type,
    }
    page = services.list_content(request.principal, page=page_number(request), **filters)
    return render(request, "content/list.html", {
        "page": page,
        "filters": filters,
        "status_choices": services.STATUS_CHOICES,
        "type_choices": services.CONTENT_TYPES,
        "brand_choices": brand_choices(request.principal, include_inactive=True),
        "campaign_choices": campaign_filter_choices(request.principal),
        "counts": services.content_counts(request.principal),
        "can_create": services.can_create(request.principal) and bool(writable_campaigns(request.principal)),
    })


@module_required("content")
@require_http_methods(["GET", "POST"])
def content_create(request):
    principal = request.principal
    require(services.can_create(principal), "You do not have permission to create content.")
    campaign_list = _form_campaigns(principal)
    initial = {"writer_user_id": principal.user_id}
    preset = valid_oid_or_blank(request.GET.get("campaign"))
    for campaign in campaign_list:
        if campaign["id"] == preset:
            initial.update(brand_id=str(campaign["brand_id"]), campaign_id=preset, keyword=campaign.get("primary_keyword", ""))
    form = ContentForm(
        request.POST or None,
        initial=initial,
        campaigns=campaign_list,
        writer_choices=services.writer_choices(principal),
        is_admin=principal.is_admin,
    )
    if request.method == "POST" and form.is_valid():
        submit = request.POST.get("action") == "submit"
        try:
            item = services.create_content(principal, form.cleaned_data)
        except ServiceError as exc:
            form.add_error(None, str(exc))
        else:
            if submit:
                try:
                    services.submit_content(principal, item["_id"], expected_seq=item["edit_seq"])
                    messages.success(request, "Content submitted for approval.")
                except ServiceError as exc:
                    messages.warning(request, f"Saved as a draft, but it could not be submitted: {exc}")
            else:
                messages.success(request, "Draft saved.")
            return redirect("content:detail", content_id=item["id"])
    return render(request, "content/form.html", {"form": form, "is_edit": False, "has_campaigns": bool(campaign_list)})


@module_required("content")
def content_detail(request, content_id):
    principal = request.principal
    item = services.get_content(principal, content_id)
    return render(request, "content/detail.html", {
        "item": item,
        "version": services.current_version(item),
        "versions": services.version_history(item),
        "history": approval_history(item),
        "can_edit": services.can_edit(principal, item),
        "can_revise": services.can_revise(principal, item),
        "can_publish": item["status"] == "approved" and can_publish(principal),
    })


@module_required("content")
def content_version(request, content_id, version_number):
    item, version = services.get_version(request.principal, content_id, version_number)
    return render(request, "content/version.html", {"item": item, "version": version})


@module_required("content")
@require_http_methods(["GET", "POST"])
def content_edit(request, content_id):
    principal = request.principal
    item = services.get_content(principal, content_id)
    if not services.can_edit(principal, item):
        messages.error(request, "This content cannot be edited in its current state.")
        return redirect("content:detail", content_id=content_id)
    version = services.current_version(item) or {}
    initial = {
        "brand_id": str(item["brand_id"]),
        "campaign_id": str(item["campaign_id"]),
        "title": item["title"],
        "keyword": item["keyword"],
        "content_type": item["content_type"],
        "writer_user_id": item.get("writer_user_id"),
        "body_html": version.get("body_html", ""),
        "internal_notes": version.get("internal_notes", ""),
        "edit_seq": item.get("edit_seq", 0),
    }
    form = ContentForm(
        request.POST or None,
        initial=initial,
        campaigns=_form_campaigns(principal, item),
        writer_choices=services.writer_choices(principal),
        is_admin=principal.is_admin,
        is_edit=True,
    )
    if request.method == "POST" and form.is_valid():
        submit = request.POST.get("action") == "submit"
        try:
            services.save_content(principal, content_id, form.cleaned_data, expected_seq=form.cleaned_data["edit_seq"], submit=submit)
        except WorkflowConflict as exc:
            messages.error(request, str(exc))
            return redirect("content:detail", content_id=content_id)
        except ServiceError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "Content submitted for approval." if submit else "Changes saved.")
            return redirect("content:detail", content_id=content_id)
    feedback = approval_history(item)[:1] if item["status"] == "needs_edit" else []
    return render(request, "content/form.html", {"form": form, "is_edit": True, "item": item, "version": version, "feedback": feedback, "has_campaigns": True})


@module_required("content")
@require_POST
def content_revise(request, content_id):
    try:
        expected = int(request.POST.get("edit_seq", "-1"))
        services.create_revision(request.principal, content_id, expected_seq=expected)
    except ValueError:
        messages.error(request, "Invalid request.")
    except ServiceError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "A new revision has been started. It must be approved again before publishing.")
        return redirect("content:edit", content_id=content_id)
    return redirect("content:detail", content_id=content_id)
