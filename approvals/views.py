from django.contrib import messages
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from approvals import services
from approvals.forms import DecisionForm
from brands.services import brand_choices
from campaigns.services import campaign_filter_choices
from content import services as content_services
from core.exceptions import ServiceError, WorkflowConflict
from core.permissions import module_required
from core.utils import page_number, valid_oid_or_blank


@module_required("approvals")
def approval_list(request):
    status = request.GET.get("status", "pending")
    if status not in dict(services.FILTER_CHOICES):
        status = "pending"
    filters = {
        "q": request.GET.get("q", ""),
        "brand_id": valid_oid_or_blank(request.GET.get("brand")),
        "campaign_id": valid_oid_or_blank(request.GET.get("campaign")),
    }
    page = services.list_approvals(request.principal, status=status, page=page_number(request), **filters)
    counts = content_services.content_counts(request.principal)
    return render(request, "approvals/list.html", {
        "page": page,
        "status": status,
        "filters": filters,
        "filter_choices": services.FILTER_CHOICES,
        "brand_choices": brand_choices(request.principal, include_inactive=True),
        "campaign_choices": campaign_filter_choices(request.principal),
        "counts": counts,
    })


@module_required("approvals")
@require_http_methods(["GET", "POST"])
def review(request, content_id):
    principal = request.principal
    item = content_services.get_content(principal, content_id)
    version = content_services.current_version(item)
    form = DecisionForm(request.POST or None, initial={"version_number": item["current_version"]})
    if request.method == "POST" and form.is_valid():
        try:
            services.decide(
                principal,
                content_id,
                action=form.cleaned_data["action"],
                feedback=form.cleaned_data["feedback"],
                version_number=form.cleaned_data["version_number"],
            )
        except WorkflowConflict as exc:
            messages.error(request, str(exc))
            return redirect("approvals:review", content_id=content_id)
        except ServiceError as exc:
            form.add_error("feedback", str(exc))
        else:
            messages.success(request, {
                "approve": "Content approved.",
                "request_changes": "Changes requested. The writer has been notified in their dashboard.",
                "reject": "Content rejected.",
            }[form.cleaned_data["action"]])
            return redirect("approvals:list")
    return render(request, "approvals/review.html", {
        "item": item,
        "version": version,
        "form": form,
        "history": services.approval_history(item),
        "can_review": services.can_review(principal, item),
        "allowed_actions": services.allowed_actions(principal),
        "self_blocked": services.self_review_blocked(principal, item),
        "show_internal_notes": principal.role != "client",
    })
