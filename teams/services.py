"""Team members: Django auth users + MongoDB team profiles, brand access and campaign assignments.

Creating a member spans two databases (Django's relational auth DB and MongoDB).
There is no cross-database transaction, so every multi-step write either runs the
MongoDB step after the Django step and *compensates* (deletes the new Django user)
if MongoDB fails, or is idempotent so it can simply be retried.
"""
import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from pymongo import DESCENDING
from pymongo.errors import DuplicateKeyError, PyMongoError

from accounts.models import normalize_email
from accounts.tokens import invite_token_generator
from brands.repositories import brands
from campaigns.repositories import campaign_assignments, campaigns
from core import audit
from core.exceptions import NotFound, ServiceError
from core.permissions import (
    ACTIVE, ADMIN, ALL_PERMISSIONS, CLIENT, INACTIVE, INVITED, PENDING, PERMISSION_CATALOG, REVIEWER, ROLE_CEILINGS,
    ROLE_LABELS, ROLES, TEAM_MEMBER, effective_permissions, require, role_default_permissions,
)
from core.utils import search_regex, utcnow
from teams.repositories import brand_access, role_permissions, team_profiles

logger = logging.getLogger("seo.teams")
User = get_user_model()

# Django ``is_active`` follows the profile status. Invited users are active in Django
# so the account-setup link works, but have no usable password and no app access
# until they set one (their profile only becomes "active" then).
DJANGO_ACTIVE_FOR_STATUS = {ACTIVE: True, INVITED: True, PENDING: False, INACTIVE: False}


# --------------------------------------------------------------------------- profiles

def profile_for_user(user):
    profile = team_profiles.for_user(user.pk)
    if profile is None and user.is_superuser and user.is_active:
        # Bootstrap: a superuser created with `createsuperuser` becomes an admin.
        profile = _create_profile(user, ADMIN, ACTIVE)
        logger.info("teams.superuser_profile_bootstrapped", extra={"user": user.pk})
    return profile


def _create_profile(user, role, status):
    try:
        return team_profiles.insert({
            "django_user_id": user.pk,
            "display_name": user.full_name,
            "email": user.email,
            "role": role,
            "status": status,
        })
    except DuplicateKeyError:
        return team_profiles.for_user(user.pk)


def _sync_django_active(user, status):
    desired = DJANGO_ACTIVE_FOR_STATUS[status]
    if user.is_active != desired:
        user.is_active = desired
        user.save(update_fields=["is_active"])


# --------------------------------------------------------------------------- registration

def register_public(full_name, email, password):
    """Create a pending account (no role, no access) for admin review.

    Returns the user, or ``None`` if the email is already registered. Callers show
    the same message either way so the form cannot be used to enumerate accounts.
    """
    email = normalize_email(email)
    if User.objects.filter(email__iexact=email).exists():
        logger.info("accounts.registration_duplicate")
        return None
    try:
        with transaction.atomic():
            user = User.objects.create_user(email=email, password=password, full_name=full_name, is_active=False)
    except IntegrityError:
        return None
    try:
        team_profiles.insert({
            "django_user_id": user.pk,
            "display_name": full_name,
            "email": email,
            "role": None,
            "status": PENDING,
        })
    except PyMongoError:
        user.delete()  # compensate: no orphaned auth user without a profile
        logger.exception("accounts.registration_profile_failed")
        raise ServiceError("We could not complete your registration. Please try again later.")
    audit.record(user.pk, "account.registered", "user", user.pk)
    return user


# --------------------------------------------------------------------------- invitations

def build_invite_url(request, user):
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = invite_token_generator.make_token(user)
    return request.build_absolute_uri(reverse("accounts:invite_confirm", kwargs={"uidb64": uid, "token": token}))


def send_invite_email(request, user, inviter_name):
    context = {
        "user": user,
        "inviter_name": inviter_name,
        "invite_url": build_invite_url(request, user),
        "valid_hours": settings.INVITE_TOKEN_TIMEOUT // 3600,
    }
    subject = "You're invited to the SEO Management Dashboard"
    body = render_to_string("emails/invite.txt", context)
    html = render_to_string("emails/invite.html", context)
    try:
        send_mail(subject, body, None, [user.email], html_message=html)
    except Exception:
        logger.exception("teams.invite_email_failed", extra={"user": user.pk})
        return False
    return True


# --------------------------------------------------------------------------- assignments

def _validate_brand_ids(brand_ids):
    brand_ids = set(brand_ids)
    if not brand_ids:
        return set()
    found = {b["_id"] for b in brands.find({"_id": {"$in": list(brand_ids)}, "status": {"$ne": "archived"}})}
    if found != brand_ids:
        raise ServiceError("One or more selected brands no longer exist or are archived.")
    return brand_ids


def _validate_campaign_ids(campaign_ids):
    campaign_ids = set(campaign_ids)
    if not campaign_ids:
        return set()
    found = {c["_id"] for c in campaigns.find({"_id": {"$in": list(campaign_ids)}, "status": {"$ne": "archived"}})}
    if found != campaign_ids:
        raise ServiceError("One or more selected campaigns no longer exist or are archived.")
    return campaign_ids


def _apply_assignments(user_id, role, brand_ids, campaign_ids):
    """Admins need no assignments; reviewers/clients get brand access only; team members get both."""
    if role == ADMIN:
        brand_ids, campaign_ids = set(), set()
    elif role in (REVIEWER, CLIENT):
        campaign_ids = set()
    brand_ids = _validate_brand_ids(brand_ids)
    campaign_ids = _validate_campaign_ids(campaign_ids)
    brand_access.replace_for_user(user_id, brand_ids, role)
    old_campaigns = set(campaign_assignments.campaign_ids_for_user(user_id))
    campaign_assignments.replace_for_user(user_id, campaign_ids)
    # Keep the denormalised ``assigned_user_ids`` on each campaign in sync.
    for campaign_id in old_campaigns - campaign_ids:
        campaigns.col.update_one({"_id": campaign_id}, {"$pull": {"assigned_user_ids": user_id}})
    for campaign_id in campaign_ids:
        campaigns.col.update_one({"_id": campaign_id}, {"$addToSet": {"assigned_user_ids": user_id}})


def _check_role(role):
    if role not in ROLES:
        raise ServiceError("Please choose a valid role.")


# --------------------------------------------------------------------------- admin actions

def invite_member(principal, request, *, full_name, email, role, status, brand_ids, campaign_ids):
    require(principal.is_admin)
    _check_role(role)
    email = normalize_email(email)
    profile_status = INVITED if status == ACTIVE else INACTIVE
    _validate_brand_ids(brand_ids)
    _validate_campaign_ids(campaign_ids)
    try:
        with transaction.atomic():
            user = User.objects.create_user(
                email=email, password=None, full_name=full_name, is_active=DJANGO_ACTIVE_FOR_STATUS[profile_status]
            )
    except IntegrityError:
        raise ServiceError("A user with this email address already exists.")
    try:
        team_profiles.insert({
            "django_user_id": user.pk,
            "display_name": full_name,
            "email": email,
            "role": role,
            "status": profile_status,
            "invited_by_user_id": principal.user_id,
        })
        _apply_assignments(user.pk, role, brand_ids, campaign_ids)
    except PyMongoError:
        # Compensate across databases: remove partial Mongo rows, then the auth user.
        team_profiles.col.delete_many({"django_user_id": user.pk})
        brand_access.col.delete_many({"django_user_id": user.pk})
        campaign_assignments.col.delete_many({"django_user_id": user.pk})
        user.delete()
        logger.exception("teams.invite_failed")
        raise ServiceError("Could not save the team member. Please try again.")
    audit.record(principal.user_id, "member.invited", "user", user.pk, {"role": role, "status": profile_status})
    email_sent = profile_status == INVITED and send_invite_email(request, user, principal.display_name)
    return user, email_sent


def resend_invite(principal, request, user_id):
    require(principal.is_admin)
    user, profile = get_member(user_id)
    if profile["status"] != INVITED:
        raise ServiceError("Invitations can only be resent to members who have not set up their account.")
    audit.record(principal.user_id, "member.invite_resent", "user", user.pk)
    return send_invite_email(request, user, principal.display_name)


def complete_invite(user):
    """Called once an invited user has set their password."""
    profile = team_profiles.for_user(user.pk)
    if profile and profile["status"] == INVITED:
        team_profiles.update_for_user(user.pk, {"status": ACTIVE, "activated_at": utcnow()})
        _sync_django_active(user, ACTIVE)
        audit.record(user.pk, "member.invite_accepted", "user", user.pk)


def update_member(principal, user_id, *, full_name, role, status, brand_ids, campaign_ids):
    require(principal.is_admin)
    _check_role(role)
    user, profile = get_member(user_id)
    if user.pk == principal.user_id and (role != ADMIN or status != ACTIVE):
        raise ServiceError("You cannot remove your own admin role or deactivate your own account.")
    _protect_last_admin(user, profile, role, status)
    if profile["status"] in (INVITED,) and status == ACTIVE:
        status = INVITED  # stays invited until the user sets a password
    if profile["status"] == PENDING and status == ACTIVE and not user.has_usable_password():
        status = INVITED
    if status not in DJANGO_ACTIVE_FOR_STATUS:
        raise ServiceError("Please choose a valid account status.")

    _apply_assignments(user.pk, role, brand_ids, campaign_ids)
    fields = {"display_name": full_name, "role": role, "status": status}
    if role != profile.get("role"):
        fields["permission_overrides"] = None  # overrides were relative to the old role's defaults
    team_profiles.update_for_user(user.pk, fields)
    if user.full_name != full_name:
        user.full_name = full_name
        user.save(update_fields=["full_name"])
    _sync_django_active(user, status)
    changes = {"role": [profile.get("role"), role], "status": [profile.get("status"), status]}
    audit.record(principal.user_id, "member.updated", "user", user.pk, changes)
    return user


def set_member_status(principal, user_id, status, role=None):
    """Activate/deactivate an account. Activating a pending registration also assigns its first role."""
    require(principal.is_admin)
    user, profile = get_member(user_id)
    if user.pk == principal.user_id:
        raise ServiceError("You cannot change the status of your own account.")
    if status == ACTIVE:
        if role and not profile.get("role"):
            _check_role(role)
            team_profiles.update_for_user(user.pk, {"role": role})
            profile["role"] = role
            audit.record(principal.user_id, "member.role_assigned", "user", user.pk, {"role": role})
        if not profile.get("role"):
            raise ServiceError("Assign a role before activating this account.")
        if not user.has_usable_password():
            status = INVITED
    if status not in DJANGO_ACTIVE_FOR_STATUS:
        raise ServiceError("Invalid status.")
    _protect_last_admin(user, profile, profile.get("role"), status)
    team_profiles.update_for_user(user.pk, {"status": status})
    _sync_django_active(user, status)
    audit.record(principal.user_id, f"member.status_{status}", "user", user.pk, {"from": profile.get("status")})
    return status


def _protect_last_admin(user, profile, new_role, new_status):
    """Never leave the system without an active administrator."""
    if profile.get("role") != ADMIN or profile.get("status") != ACTIVE:
        return
    if new_role == ADMIN and new_status == ACTIVE:
        return
    others = team_profiles.count({"role": ADMIN, "status": ACTIVE, "django_user_id": {"$ne": user.pk}})
    if others == 0:
        raise ServiceError("This is the last active admin. Make another member an admin first.")


def remove_access(principal, user_id):
    """Soft removal: deactivate and clear brand/campaign assignments. Authored content,
    approvals, publications and audit history are kept."""
    require(principal.is_admin and principal.has_perm("team.manage"))
    user, profile = get_member(user_id)
    if user.pk == principal.user_id:
        raise ServiceError("You cannot remove your own access.")
    _protect_last_admin(user, profile, profile.get("role"), INACTIVE)
    brands_before = len(brand_access.brand_ids_for_user(user.pk))
    campaigns_before = campaign_assignments.campaign_ids_for_user(user.pk)
    brand_access.replace_for_user(user.pk, set(), profile.get("role"))
    campaign_assignments.replace_for_user(user.pk, set())
    for campaign_id in campaigns_before:
        campaigns.col.update_one({"_id": campaign_id}, {"$pull": {"assigned_user_ids": user.pk}})
    team_profiles.update_for_user(user.pk, {"status": INACTIVE})
    _sync_django_active(user, INACTIVE)
    audit.record(principal.user_id, "member.access_removed", "user", user.pk,
                 {"brands_removed": brands_before, "campaigns_removed": len(campaigns_before)})


# --------------------------------------------------------------------------- permissions

def role_defaults_map():
    stored = role_permissions.all_by_role()
    return {role: role_default_permissions(role, stored.get(role)) for role in ROLES}


def member_effective_permissions(profile, stored=None):
    stored = role_permissions.all_by_role() if stored is None else stored
    role = profile.get("role")
    if profile.get("status") != ACTIVE or role not in ROLES:
        return set()
    return effective_permissions(role, profile, stored.get(role))


def accessible_modules(permissions):
    return [label for module, label, _ in PERMISSION_CATALOG if f"{module}.view" in permissions]


def permission_rows(role, checked, defaults):
    """Editor rows for one role: each action knows if it is allowed, a role default, and checked."""
    ceiling = ROLE_CEILINGS.get(role, frozenset())
    rows = []
    for module, label, actions in PERMISSION_CATALOG:
        items = []
        for action, action_label in actions:
            key = f"{module}.{action}"
            items.append({
                "key": key, "label": action_label, "is_view": action == "view",
                "allowed": key in ceiling, "default": key in defaults, "checked": key in checked,
            })
        rows.append({"module": module, "label": label, "actions": items, "available": any(i["allowed"] for i in items)})
    return rows


def editor_config():
    """Per-role ceilings and defaults for the client-side editor (role dropdown changes)."""
    defaults = role_defaults_map()
    return {role: {"ceiling": sorted(ROLE_CEILINGS[role]), "defaults": sorted(defaults[role])} for role in ROLES}


def _clean_permission_keys(role, keys):
    keys = set(keys)
    unknown = keys - ALL_PERMISSIONS
    if unknown:
        raise ServiceError("Unknown permission: " + ", ".join(sorted(unknown)))
    beyond = keys - ROLE_CEILINGS.get(role, frozenset())
    if beyond:
        raise ServiceError(f"These permissions are not available for the {ROLE_LABELS[role]} role: " + ", ".join(sorted(beyond)))
    # An action without its module's View has no effect; drop it rather than store noise.
    return {k for k in keys if k.endswith(".view") or f"{k.split('.', 1)[0]}.view" in keys}


def update_member_permissions(principal, user_id, *, role, permissions):
    """Change a member's role and/or per-member permission overrides (stored as grants/revokes
    relative to the role defaults, so the editor can show which values are overrides)."""
    require(principal.is_admin and principal.has_perm("team.manage"))
    _check_role(role)
    user, profile = get_member(user_id)
    if user.pk == principal.user_id:
        raise ServiceError("You cannot change your own role or permissions.")
    old_role = profile.get("role")
    if role != old_role:
        _protect_last_admin(user, profile, role, profile.get("status"))
    before = member_effective_permissions(profile)

    if role == ADMIN:
        overrides = None
    else:
        selected = _clean_permission_keys(role, permissions)
        defaults = role_default_permissions(role, role_permissions.for_role(role))
        overrides = {"grants": sorted(selected - defaults), "revokes": sorted(defaults - selected)}
        if not overrides["grants"] and not overrides["revokes"]:
            overrides = None

    if role != old_role:
        existing_brands = set(brand_access.brand_ids_for_user(user.pk))
        existing_campaigns = set(campaign_assignments.campaign_ids_for_user(user.pk))
        _apply_assignments(user.pk, role, existing_brands, existing_campaigns)
    team_profiles.update_for_user(user.pk, {"role": role, "permission_overrides": overrides})

    after = member_effective_permissions(dict(profile, role=role, permission_overrides=overrides))
    audit.record(principal.user_id, "member.permissions_updated", "user", user.pk, {
        "role": [old_role, role],
        "granted": sorted(after - before),
        "revoked": sorted(before - after),
        "overrides": overrides or {},
    })
    return after


def reset_member_permissions(principal, user_id):
    require(principal.is_admin and principal.has_perm("team.manage"))
    user, profile = get_member(user_id)
    if user.pk == principal.user_id:
        raise ServiceError("You cannot change your own permissions.")
    team_profiles.update_for_user(user.pk, {"permission_overrides": None})
    audit.record(principal.user_id, "member.permissions_reset", "user", user.pk, {"role": profile.get("role")})


def update_role_defaults(principal, role, permissions):
    require(principal.is_admin and principal.has_perm("team.manage"))
    _check_role(role)
    if role == ADMIN:
        raise ServiceError("Admins always have full access; their permissions cannot be changed.")
    before = role_default_permissions(role, role_permissions.for_role(role))
    selected = _clean_permission_keys(role, permissions)
    role_permissions.save(role, selected, principal.user_id)
    audit.record(principal.user_id, "role.permissions_updated", "role", role,
                 {"granted": sorted(selected - before), "revoked": sorted(before - selected)})


def reset_role_defaults(principal, role):
    require(principal.is_admin and principal.has_perm("team.manage"))
    _check_role(role)
    if role == ADMIN:
        raise ServiceError("Admin permissions cannot be changed.")
    role_permissions.reset(role)
    audit.record(principal.user_id, "role.permissions_reset", "role", role)


def access_matrix(principal, *, q="", role="", page=1):
    """All members with their effective module access, for the Member Access overview."""
    require(principal.is_admin)
    query = {"role": role} if role else {}
    regex = search_regex(q)
    if regex:
        query["$or"] = [{"display_name": regex}, {"email": regex}]
    result = team_profiles.find_page(query, page=page, sort=[("display_name", 1)])
    stored = role_permissions.all_by_role()
    defaults = role_defaults_map()
    for profile in result.items:
        effective = member_effective_permissions(profile, stored)
        role_set = defaults.get(profile.get("role"), set())
        profile["module_access"] = [
            {"module": m, "on": f"{m}.view" in effective, "override": (f"{m}.view" in effective) != (f"{m}.view" in role_set)}
            for m, _, _ in PERMISSION_CATALOG
        ]
        profile["has_overrides"] = bool(profile.get("permission_overrides"))
    return result


# --------------------------------------------------------------------------- queries

def get_member(user_id):
    try:
        user = User.objects.get(pk=int(user_id))
    except (User.DoesNotExist, ValueError, TypeError):
        raise NotFound("Team member not found.")
    profile = team_profiles.for_user(user.pk)
    if profile is None:
        # Self-heal an orphaned auth user (e.g. a failed compensation): treat as pending.
        profile = _create_profile(user, None, PENDING)
    return user, profile


def reconcile_orphaned_users():
    """Give any auth user without a MongoDB profile (e.g. after a partial failure) a pending one,
    so it shows up in the Team list for an admin to repair. Cheap when nothing is missing."""
    if User.objects.count() == team_profiles.count():
        return 0
    known = {p["django_user_id"] for p in team_profiles.col.find({}, {"django_user_id": 1})}
    repaired = 0
    for user in User.objects.exclude(pk__in=known):
        _create_profile(user, ADMIN if user.is_superuser else None, ACTIVE if user.is_superuser and user.is_active else PENDING)
        repaired += 1
    if repaired:
        logger.warning("teams.orphaned_users_reconciled", extra={"count": repaired})
    return repaired


def list_members(principal, *, q="", role="", status="", page=1):
    require(principal.is_admin)
    reconcile_orphaned_users()
    query = {}
    if role:
        query["role"] = role
    if status:
        query["status"] = status
    regex = search_regex(q)
    if regex:
        query["$or"] = [{"display_name": regex}, {"email": regex}]
    result = team_profiles.find_page(query, page=page, sort=[("created_at", DESCENDING)])
    user_ids = [p["django_user_id"] for p in result.items]
    counts = {}
    for row in campaign_assignments.aggregate([
        {"$match": {"django_user_id": {"$in": user_ids}}},
        {"$group": {"_id": "$django_user_id", "n": {"$sum": 1}}},
    ]):
        counts[row["_id"]] = row["n"]
    stored = role_permissions.all_by_role()
    logins = dict(User.objects.filter(pk__in=user_ids).values_list("pk", "last_login"))
    for profile in result.items:
        profile["campaign_count"] = counts.get(profile["django_user_id"], 0)
        profile["modules"] = accessible_modules(member_effective_permissions(profile, stored))
        profile["last_login"] = logins.get(profile["django_user_id"])
        profile["has_overrides"] = bool(profile.get("permission_overrides"))
    return result


def member_assignments(user_id):
    brand_ids = brand_access.brand_ids_for_user(user_id)
    campaign_ids = campaign_assignments.campaign_ids_for_user(user_id)
    return (
        brands.find({"_id": {"$in": brand_ids}}, sort=[("name_lower", 1)]),
        campaigns.find({"_id": {"$in": campaign_ids}}, sort=[("name_lower", 1)]),
    )


def member_stats(user_id):
    from content.repositories import contents
    from publications.repositories import publications

    counts = contents.status_counts({"writer_user_id": user_id})
    return {
        "draft": counts.get("draft", 0) + counts.get("needs_edit", 0),
        "pending": counts.get("pending", 0),
        "approved": counts.get("approved", 0),
        "published": publications.count({"writer_user_id": user_id}),
    }


def work_history(user_id, limit=20):
    return audit.audit_logs.find({"actor_user_id": user_id}, sort=[("created_at", DESCENDING)], limit=limit)


def assignable_users(role=TEAM_MEMBER):
    """Active team members that can be assigned to campaigns."""
    profiles = team_profiles.find({"role": role, "status": {"$in": [ACTIVE, INVITED]}}, sort=[("display_name", 1)])
    return [(p["django_user_id"], p.get("display_name") or p.get("email")) for p in profiles]


def display_names(user_ids):
    profiles = team_profiles.by_user_ids(user_ids)
    names = {uid: (p.get("display_name") or p.get("email")) for uid, p in profiles.items()}
    missing = [uid for uid in user_ids if uid is not None and uid not in names]
    if missing:
        for user in User.objects.filter(pk__in=missing).only("pk", "full_name", "email"):
            names[user.pk] = user.full_name or user.email
    return names


def team_member_count(principal):
    if principal.is_admin:
        return team_profiles.count({"role": {"$in": [TEAM_MEMBER, REVIEWER, ADMIN]}, "status": {"$in": [ACTIVE, INVITED]}})
    user_ids = set()
    for c in campaigns.find({"_id": {"$in": list(principal.campaign_ids)}} if principal.role in (TEAM_MEMBER, REVIEWER) else {"brand_id": {"$in": list(principal.brand_ids)}}):
        user_ids.update(c.get("assigned_user_ids", []))
    return len(user_ids)

