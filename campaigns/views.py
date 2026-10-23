from django.contrib import messages
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from brands.services import brand_choices
from campaigns import services
from campaigns.forms import CampaignForm
from core.exceptions import ServiceError
from core.permissions import module_required, require
from core.utils import datetime_to_date, page_number, valid_oid_or_blank
from content.services import can_create as can_create_content
from teams.services import assignable_users


@module_required("campaigns")
def campaign_list(request):
    q = request.GET.get("q", "")
    status = request.GET.get("status", "")
    if status not in dict(services.FILTER_STATUS_CHOICES):
        status = ""
    brand_id = valid_oid_or_blank(request.GET.get("brand", ""))
    page = services.list_campaigns(request.principal, q=q, brand_id=brand_id, status=status, page=page_number(request))
    return render(request, "campaigns/list.html", {
        "page": page,
        "q": q,
        "status": status,
        "brand": brand_id,
        "status_choices": services.FILTER_STATUS_CHOICES,
        "brand_choices": brand_choices(request.principal, include_inactive=True),
    })


@module_required("campaigns")
@require_http_methods(["GET", "POST"])
def campaign_create(request):
    require(request.principal.is_admin)
    initial = {"brand_id": request.GET.get("brand", "")}
    form = CampaignForm(
        request.POST or None,
        initial=initial,
        brand_choices=brand_choices(request.principal),
        member_choices=assignable_users(),
    )
    if request.method == "POST" and form.is_valid():
        try:
            campaign = services.create_campaign(request.principal, form.cleaned_data)
        except ServiceError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, f"Campaign “{campaign['name']}” created.")
            return redirect("campaigns:detail", campaign_id=campaign["id"])
    return render(request, "campaigns/form.html", {"form": form, "is_edit": False})


@module_required("campaigns")
def campaign_detail(request, campaign_id):
    campaign = services.get_campaign(request.principal, campaign_id)
    return render(request, "campaigns/detail.html", {
        "campaign": campaign,
        "groups": services.campaign_content(request.principal, campaign),
        "can_write": can_create_content(request.principal) and campaign["status"] in services.WRITABLE_STATUSES,
    })


@module_required("campaigns")
@require_http_methods(["GET", "POST"])
def campaign_edit(request, campaign_id):
    require(request.principal.is_admin)
    campaign = services.get_campaign(request.principal, campaign_id)
    initial = dict(
        campaign,
        brand_id=str(campaign["brand_id"]),
        secondary_keywords="\n".join(campaign.get("secondary_keywords", [])),
        start_date=datetime_to_date(campaign.get("start_date")),
        end_date=datetime_to_date(campaign.get("end_date")),
    )
    form = CampaignForm(
        request.POST or None,
        initial=initial,
        brand_choices=[(str(campaign["brand_id"]), campaign["brand"]["name"] if campaign["brand"] else "Unknown brand")],
        member_choices=assignable_users(),
        lock_brand=True,
    )
    if request.method == "POST" and form.is_valid():
        try:
            services.update_campaign(request.principal, campaign_id, form.cleaned_data)
        except ServiceError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "Campaign updated.")
            return redirect("campaigns:detail", campaign_id=campaign_id)
    return render(request, "campaigns/form.html", {"form": form, "campaign": campaign, "is_edit": True})


@module_required("campaigns")
@require_POST
def campaign_archive(request, campaign_id):
    try:
        services.archive_campaign(request.principal, campaign_id)
        messages.success(request, "Campaign archived. Its content history is preserved.")
    except ServiceError as exc:
        messages.error(request, str(exc))
    return redirect("campaigns:detail", campaign_id=campaign_id)


@module_required("campaigns")
@require_POST
def campaign_restore(request, campaign_id):
    try:
        services.restore_campaign(request.principal, campaign_id)
        messages.success(request, "Campaign restored.")
    except ServiceError as exc:
        messages.error(request, str(exc))
    return redirect("campaigns:detail", campaign_id=campaign_id)
