from django.contrib import messages
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from brands.repositories import brands
from campaigns.repositories import campaigns
from core.exceptions import ServiceError
from django.urls import reverse

from core.permissions import ACCOUNT_STATUS_LABELS, PERMISSION_CATALOG, ROLE_CHOICES, admin_required
from core.utils import page_number, parse_oid
from teams import services
from teams.forms import MemberForm


def _form_kwargs():
    brand_items = brands.find({"status": {"$ne": "archived"}}, sort=[("name_lower", 1)])
    brand_names = {b["_id"]: b["name"] for b in brand_items}
    campaign_items = campaigns.find({"status": {"$ne": "archived"}}, sort=[("name_lower", 1)])
    return {
        "brand_choices": [(b["id"], b["name"]) for b in brand_items],
        "campaign_choices": [
            (c["id"], f"{c['name']} — {brand_names.get(c['brand_id'], 'Archived brand')}") for c in campaign_items
        ],
    }


@admin_required
def member_list(request):
    q = request.GET.get("q", "")
    role = request.GET.get("role", "")
    status = request.GET.get("status", "")
    if role not in dict(ROLE_CHOICES):
        role = ""
    if status not in ACCOUNT_STATUS_LABELS:
        status = ""
    page = services.list_members(request.principal, q=q, role=role, status=status, page=page_number(request))
    pending_count = services.team_profiles.count({"status": "pending"})
    return render(request, "teams/list.html", {
        "page": page,
        "q": q,
        "role": role,
        "status": status,
        "role_choices": ROLE_CHOICES,
        "status_choices": list(ACCOUNT_STATUS_LABELS.items()),
        "pending_count": pending_count,
    })


@admin_required
@require_http_methods(["GET", "POST"])
def member_create(request):
    form = MemberForm(request.POST or None, **_form_kwargs())
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            user, email_sent = services.invite_member(
                request.principal,
                request,
                full_name=data["full_name"],
                email=data["email"],
                role=data["role"],
                status=data["status"],
                brand_ids=[parse_oid(b) for b in data["brand_ids"]],
                campaign_ids=[parse_oid(c) for c in data["campaign_ids"]],
            )
        except ServiceError as exc:
            form.add_error(None, str(exc))
        else:
            if data["status"] == "active":
                if email_sent:
                    messages.success(request, f"Invitation sent to {user.email}.")
                else:
                    messages.warning(request, "Member created, but the invitation email could not be sent. Check SMTP settings and resend.")
            else:
                messages.success(request, "Member created as inactive.")
            return redirect("teams:detail", user_id=user.pk)
    return render(request, "teams/form.html", {"form": form, "is_edit": False})


@admin_required
def member_detail(request, user_id):
    user, profile = services.get_member(user_id)
    member_brands, member_campaigns = services.member_assignments(user.pk)
    return render(request, "teams/detail.html", {
        "member": user,
        "profile": profile,
        "member_brands": member_brands,
        "member_campaigns": member_campaigns,
        "stats": services.member_stats(user.pk),
        "history": services.work_history(user.pk),
        "role_choices": ROLE_CHOICES,
        "is_self": user.pk == request.principal.user_id,
    })


@admin_required
@require_http_methods(["GET", "POST"])
def member_edit(request, user_id):
    user, profile = services.get_member(user_id)
    member_brands, member_campaigns = services.member_assignments(user.pk)
    initial = {
        "full_name": profile.get("display_name") or user.full_name,
        "email": user.email,
        "role": profile.get("role") or "team_member",
        "status": "active" if profile["status"] in ("active", "invited", "pending") else "inactive",
        "brand_ids": [b["id"] for b in member_brands],
        "campaign_ids": [c["id"] for c in member_campaigns],
    }
    form = MemberForm(request.POST or None, initial=initial, is_edit=True, **_form_kwargs())
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            services.update_member(
                request.principal,
                user.pk,
                full_name=data["full_name"],
                role=data["role"],
                status=data["status"],
                brand_ids=[parse_oid(b) for b in data["brand_ids"]],
                campaign_ids=[parse_oid(c) for c in data["campaign_ids"]],
            )
        except ServiceError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "Team member updated.")
            return redirect("teams:detail", user_id=user.pk)
    return render(request, "teams/form.html", {"form": form, "is_edit": True, "member": user, "profile": profile})


@admin_required
@require_POST
def member_set_status(request, user_id):
    try:
        new_status = services.set_member_status(
            request.principal, user_id, request.POST.get("status", ""), role=request.POST.get("role") or None
        )
    except ServiceError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, {
            "active": "Account activated.",
            "inactive": "Account deactivated. Their content and publication history is preserved.",
            "invited": "Account enabled. The member must set a password using an invitation link.",
        }.get(new_status, "Account updated."))
    return redirect("teams:detail", user_id=user_id)


@admin_required
@require_POST
def member_remove_access(request, user_id):
    try:
        services.remove_access(request.principal, user_id)
    except ServiceError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Access removed. The account is deactivated and unassigned; its history is preserved.")
    return redirect("teams:detail", user_id=user_id)


@admin_required
@require_http_methods(["GET", "POST"])
def member_permissions(request, user_id):
    user, profile = services.get_member(user_id)
    role = profile.get("role") or "team_member"
    if request.method == "POST":
        if "reset" in request.POST:
            try:
                services.reset_member_permissions(request.principal, user.pk)
            except ServiceError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, "Permissions reset to the role defaults.")
            return redirect("teams:permissions", user_id=user.pk)
        try:
            services.update_member_permissions(
                request.principal, user.pk, role=request.POST.get("role", ""), permissions=request.POST.getlist("perm")
            )
        except ServiceError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "Permissions saved. They apply from the member's next request.")
            return redirect("teams:permissions", user_id=user.pk)
    defaults = services.role_defaults_map()
    effective = services.member_effective_permissions(dict(profile, status="active")) if role in defaults else set()
    return render(request, "teams/permissions.html", {
        "member": user,
        "profile": profile,
        "role": role,
        "role_choices": ROLE_CHOICES,
        "rows": services.permission_rows(role, effective, defaults.get(role, set())),
        "editor_config": services.editor_config(),
        "is_self": user.pk == request.principal.user_id,
        "has_overrides": bool(profile.get("permission_overrides")),
    })


@admin_required
@require_http_methods(["GET", "POST"])
def role_permissions(request):
    if request.method == "POST":
        role = request.POST.get("role", "")
        try:
            if "reset" in request.POST:
                services.reset_role_defaults(request.principal, role)
                messages.success(request, "Role defaults restored.")
            else:
                services.update_role_defaults(request.principal, role, request.POST.getlist("perm"))
                messages.success(request, "Role defaults saved. Members without overrides get them on their next request.")
        except ServiceError as exc:
            messages.error(request, str(exc))
        return redirect(f"{reverse('teams:roles')}?role={role}")
    defaults = services.role_defaults_map()
    editable = [(value, label) for value, label in ROLE_CHOICES if value != "admin"]
    selected = request.GET.get("role", "team_member")
    if selected not in dict(editable):
        selected = "team_member"
    customised = services.role_permissions.all_by_role()
    return render(request, "teams/roles.html", {
        "roles": editable,
        "selected": selected,
        "selected_label": dict(ROLE_CHOICES)[selected],
        "rows": services.permission_rows(selected, defaults[selected], defaults[selected]),
        "customised": selected in customised,
        "member_count": services.team_profiles.count({"role": selected}),
    })


@admin_required
def access_overview(request):
    q = request.GET.get("q", "")
    role = request.GET.get("role", "")
    if role not in dict(ROLE_CHOICES):
        role = ""
    return render(request, "teams/access.html", {
        "page": services.access_matrix(request.principal, q=q, role=role, page=page_number(request)),
        "modules": [(m, label) for m, label, _ in PERMISSION_CATALOG],
        "q": q,
        "role": role,
        "role_choices": ROLE_CHOICES,
    })


@admin_required
@require_POST
def member_resend_invite(request, user_id):
    try:
        sent = services.resend_invite(request.principal, request, user_id)
    except ServiceError as exc:
        messages.error(request, str(exc))
    else:
        if sent:
            messages.success(request, "Invitation email sent.")
        else:
            messages.error(request, "The invitation email could not be sent. Check SMTP settings.")
    return redirect("teams:detail", user_id=user_id)
