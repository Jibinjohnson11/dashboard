from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from brands.services import brand_choices
from campaigns.services import campaign_filter_choices
from content.repositories import content_versions
from core.exceptions import ServiceError, WorkflowConflict
from core.permissions import module_required, require
from core.utils import datetime_to_date, page_number, valid_oid_or_blank
from publications import services
from publications.forms import PublicationFilterForm, PublicationForm


def _filters(request):
    dates = PublicationFilterForm(request.GET)
    dates.is_valid()
    return {
        "q": request.GET.get("q", ""),
        "brand_id": valid_oid_or_blank(request.GET.get("brand")),
        "campaign_id": valid_oid_or_blank(request.GET.get("campaign")),
        "date_from": dates.cleaned_data.get("date_from"),
        "date_to": dates.cleaned_data.get("date_to"),
    }


@module_required("published")
def publication_list(request):
    filters = _filters(request)
    page = services.list_publications(request.principal, page=page_number(request), **filters)
    for item in page.items:
        item["can_edit"] = services.can_edit_publication(request.principal, item)
    return render(request, "publications/list.html", {
        "page": page,
        "filters": filters,
        "brand_choices": brand_choices(request.principal, include_inactive=True),
        "campaign_choices": campaign_filter_choices(request.principal),
        "can_publish": services.can_publish(request.principal),
    })


@module_required("published")
def publication_export(request):
    filters = _filters(request)
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="published-content-{timezone.localdate().isoformat()}.csv"'
    services.write_csv(request.principal, response, **filters)
    return response


@module_required("published")
@require_http_methods(["GET", "POST"])
def publication_create(request):
    principal = request.principal
    require(services.can_publish(principal), "Only admins and team members can record publications.")
    items = services.publishable_content(principal)
    choices = [
        (c["id"], f"{c['title']} — {(c.get('brand') or {}).get('name', '')} (v{c['approved_version']})") for c in items
    ]
    initial = {"published_by_user_id": principal.user_id, "published_date": timezone.localdate()}
    preset = valid_oid_or_blank(request.GET.get("content"))
    if preset:
        initial["content_id"] = preset
    form = PublicationForm(request.POST or None, initial=initial, content_choices=choices, publisher_choices=services.publisher_choices())
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            publication = services.publish(
                principal,
                content_id=data["content_id"],
                published_url=data["published_url"],
                published_by_user_id=data["published_by_user_id"],
                published_date=data["published_date"],
                notes=data["notes"],
            )
        except (ServiceError, WorkflowConflict) as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "Publication recorded.")
            return redirect("publications:detail", publication_id=publication["id"])
    return render(request, "publications/form.html", {"form": form, "is_edit": False, "has_content": bool(items)})


@module_required("published")
def publication_detail(request, publication_id):
    publication = services.get_publication(request.principal, publication_id)
    version = content_versions.get(publication["approved_version_id"])
    return render(request, "publications/detail.html", {
        "publication": publication,
        "version": version,
        "history": services.publication_history(publication),
        "can_edit": services.can_edit_publication(request.principal, publication),
        "can_view_content": request.principal.can_access("content"),
    })


@module_required("published")
@require_http_methods(["GET", "POST"])
def publication_edit(request, publication_id):
    principal = request.principal
    publication = services.get_publication(principal, publication_id)
    require(services.can_edit_publication(principal, publication), "You cannot edit this publication.")
    initial = {
        "published_url": publication["published_url"],
        "published_by_user_id": publication["published_by_user_id"],
        "published_date": datetime_to_date(publication["published_at"]),
        "notes": publication.get("notes", ""),
    }
    form = PublicationForm(request.POST or None, initial=initial, publisher_choices=services.publisher_choices())
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            services.update_publication(
                principal,
                publication_id,
                published_url=data["published_url"],
                published_by_user_id=data["published_by_user_id"],
                published_date=data["published_date"],
                notes=data["notes"],
            )
        except ServiceError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "Publication details updated.")
            return redirect("publications:detail", publication_id=publication_id)
    return render(request, "publications/form.html", {"form": form, "is_edit": True, "publication": publication})
