"""Role-based and object-level authorisation.

Every view resolves ``request.principal`` (the logged-in user plus their
MongoDB team profile) and every service call scopes its MongoDB queries
through the ``*_scope`` helpers below. Access is denied by default: a user
without an active profile can reach nothing but the "pending" page.
"""
import logging
from functools import cached_property, wraps

from django.shortcuts import redirect

from core.exceptions import Forbidden, NotFound

logger = logging.getLogger("seo.permissions")

ADMIN = "admin"
TEAM_MEMBER = "team_member"
REVIEWER = "reviewer"
CLIENT = "client"

ROLE_CHOICES = [
    (ADMIN, "Admin"),
    (TEAM_MEMBER, "Team Member"),
    (REVIEWER, "Reviewer"),
    (CLIENT, "Client"),
]
ROLE_LABELS = dict(ROLE_CHOICES)
ROLES = set(ROLE_LABELS)

ACTIVE = "active"
PENDING = "pending"
INACTIVE = "inactive"
INVITED = "invited"
ACCOUNT_STATUS_LABELS = {ACTIVE: "Active", PENDING: "Pending", INACTIVE: "Inactive", INVITED: "Invited"}

ALL_ROLES = frozenset(ROLES)
MODULE_ROLES = {
    "dashboard": ALL_ROLES,
    "brands": ALL_ROLES,
    "campaigns": frozenset({ADMIN, TEAM_MEMBER, REVIEWER}),
    "team": frozenset({ADMIN}),
    "content": frozenset({ADMIN, TEAM_MEMBER}),
    "approvals": frozenset({ADMIN, REVIEWER, CLIENT}),
    "drafts": ALL_ROLES,
    "published": ALL_ROLES,
    "settings": ALL_ROLES,
}

# --------------------------------------------------------------------------- action permissions
# Each permission is "<module>.<action>". "<module>.view" is the module toggle: without it the
# module disappears and none of its actions apply.
PERMISSION_CATALOG = [
    ("dashboard", "Dashboard", [("view", "View")]),
    ("brands", "Brands", [("view", "View"), ("create", "Create"), ("edit", "Edit"), ("delete", "Archive / restore")]),
    ("campaigns", "Campaigns", [("view", "View"), ("create", "Create"), ("edit", "Edit"), ("delete", "Archive / restore")]),
    ("team", "Team", [("view", "View"), ("manage", "Manage members & permissions")]),
    ("content", "Content Writing", [("view", "View"), ("create", "Create"), ("edit", "Edit & submit")]),
    ("approvals", "Content Approvals", [("view", "View"), ("approve", "Approve"), ("reject", "Reject"), ("request_changes", "Request changes")]),
    ("drafts", "Content Drafts", [("view", "View"), ("edit", "Edit & resubmit")]),
    ("published", "Published Content", [("view", "View"), ("create", "Record publication"), ("edit", "Edit publication")]),
    ("settings", "Settings", [("view", "View")]),
]
ALL_PERMISSIONS = frozenset(f"{module}.{action}" for module, _, actions in PERMISSION_CATALOG for action, _ in actions)

# The most each role can ever hold. Admins can switch these off and on per role or per member,
# but never grant beyond them: that is what stops privilege escalation (e.g. a client managing
# the team). These match the business rules the services already enforce.
_VIEW = lambda *modules: {f"{m}.view" for m in modules}  # noqa: E731
ROLE_CEILINGS = {
    ADMIN: ALL_PERMISSIONS,
    TEAM_MEMBER: frozenset(_VIEW("dashboard", "brands", "campaigns", "content", "drafts", "published", "settings")
                           | {"content.create", "content.edit", "drafts.edit", "published.create", "published.edit"}),
    REVIEWER: frozenset(_VIEW("dashboard", "brands", "campaigns", "approvals", "drafts", "published", "settings")
                        | {"approvals.approve", "approvals.reject", "approvals.request_changes"}),
    CLIENT: frozenset(_VIEW("dashboard", "brands", "approvals", "drafts", "published", "settings")
                      | {"approvals.approve", "approvals.reject", "approvals.request_changes"}),
}


def role_default_permissions(role, stored=None):
    """Defaults for a role: the admin-edited set if one is stored, else the full ceiling."""
    ceiling = ROLE_CEILINGS.get(role, frozenset())
    if role == ADMIN or stored is None:
        return set(ceiling)
    return set(stored.get("grants", [])) & ceiling


def effective_permissions(role, profile, stored_role_defaults=None):
    """ceiling ∩ ((role defaults ∪ member grants) − member revokes). Unknown keys are ignored (deny)."""
    if role == ADMIN:
        return set(ALL_PERMISSIONS)
    ceiling = ROLE_CEILINGS.get(role)
    if not ceiling:
        return set()
    overrides = (profile or {}).get("permission_overrides") or {}
    granted = role_default_permissions(role, stored_role_defaults) | set(overrides.get("grants", []))
    granted = (granted - set(overrides.get("revokes", []))) & ceiling
    return {p for p in granted if p.endswith(".view") or f"{p.split('.', 1)[0]}.view" in granted}


# Roles that may record decisions on submitted content.
REVIEW_ROLES = frozenset({ADMIN, REVIEWER, CLIENT})
# Roles that may record publications.
PUBLISH_ROLES = frozenset({ADMIN, TEAM_MEMBER})


class Principal:
    """The authenticated user as seen by the authorisation layer."""

    def __init__(self, user, profile):
        self.user = user
        self.user_id = user.pk
        self.profile = profile or {}
        self.role = self.profile.get("role")
        self.status = self.profile.get("status")
        self.display_name = self.profile.get("display_name") or user.full_name or user.email

    def __repr__(self):
        return f"<Principal user={self.user_id} role={self.role}>"

    @property
    def is_active(self):
        return bool(self.profile) and self.status == ACTIVE and self.role in ROLES and self.user.is_active

    @property
    def is_admin(self):
        return self.is_active and self.role == ADMIN

    @property
    def role_label(self):
        return ROLE_LABELS.get(self.role, "Pending")

    @cached_property
    def permissions(self):
        """Effective permission set, computed fresh on every request (so changes apply immediately)."""
        if not self.is_active:
            return set()
        if self.role == ADMIN:
            return set(ALL_PERMISSIONS)
        from teams.repositories import role_permissions

        return effective_permissions(self.role, self.profile, role_permissions.for_role(self.role))

    def has_perm(self, permission):
        return self.is_active and permission in self.permissions

    def can_access(self, module):
        return self.is_active and self.role in MODULE_ROLES.get(module, ()) and self.has_perm(f"{module}.view")

    @cached_property
    def modules(self):
        """{module: bool} for templates, which cannot call can_access() with an argument."""
        return {module: self.can_access(module) for module in MODULE_ROLES}

    # --- Scope sets (computed once per request) ---

    @cached_property
    def explicit_brand_ids(self):
        from teams.repositories import brand_access

        return set(brand_access.brand_ids_for_user(self.user_id))

    @cached_property
    def assigned_campaign_ids(self):
        from campaigns.repositories import campaign_assignments

        return set(campaign_assignments.campaign_ids_for_user(self.user_id))

    @cached_property
    def campaign_ids(self):
        """Campaigns a team member or reviewer may see (assigned, or under an assigned brand)."""
        from campaigns.repositories import campaigns

        if self.role == TEAM_MEMBER:
            ids = set(self.assigned_campaign_ids)
        else:
            ids = set()
        if self.role in (TEAM_MEMBER, REVIEWER) and self.explicit_brand_ids:
            ids |= {c["_id"] for c in campaigns.col.find({"brand_id": {"$in": list(self.explicit_brand_ids)}}, {"_id": 1})}
        return ids

    @cached_property
    def brand_ids(self):
        """Brands this user may see. Team members also see the brands of their assigned campaigns."""
        from campaigns.repositories import campaigns

        ids = set(self.explicit_brand_ids)
        if self.role == TEAM_MEMBER and self.assigned_campaign_ids:
            ids |= {
                c["brand_id"]
                for c in campaigns.col.find({"_id": {"$in": list(self.assigned_campaign_ids)}}, {"brand_id": 1})
            }
        return ids


NO_MATCH = {"_id": {"$in": []}}


def brand_scope(principal):
    if principal.is_admin:
        return {}
    if not principal.is_active:
        return NO_MATCH
    return {"_id": {"$in": list(principal.brand_ids)}}


def campaign_scope(principal):
    if principal.is_admin:
        return {}
    if principal.is_active and principal.role in (TEAM_MEMBER, REVIEWER):
        return {"_id": {"$in": list(principal.campaign_ids)}}
    return NO_MATCH


def campaign_read_scope(principal):
    """Campaigns whose *names* may be shown (filters, charts). Clients see those of their brands."""
    if principal.is_active and principal.role == CLIENT:
        return {"brand_id": {"$in": list(principal.brand_ids)}}
    return campaign_scope(principal)


def content_scope(principal):
    if principal.is_admin:
        return {}
    if not principal.is_active:
        return NO_MATCH
    if principal.role == TEAM_MEMBER:
        return {"$or": [{"campaign_id": {"$in": list(principal.campaign_ids)}}, {"writer_user_id": principal.user_id}]}
    # Reviewers and clients never see unsubmitted drafts.
    return {"brand_id": {"$in": list(principal.brand_ids)}, "status": {"$ne": "draft"}}


def publication_scope(principal):
    if principal.is_admin:
        return {}
    if not principal.is_active:
        return NO_MATCH
    if principal.role == TEAM_MEMBER:
        return {"$or": [{"campaign_id": {"$in": list(principal.campaign_ids)}}, {"brand_id": {"$in": list(principal.brand_ids)}}]}
    return {"brand_id": {"$in": list(principal.brand_ids)}}


def combine(*queries):
    queries = [q for q in queries if q]
    if not queries:
        return {}
    if len(queries) == 1:
        return queries[0]
    return {"$and": queries}


def require(condition, message="You do not have permission to perform this action."):
    if not condition:
        raise Forbidden(message)


def require_visible(doc):
    if doc is None:
        raise NotFound("Record not found.")
    return doc


def module_required(module):
    """View decorator: active profile + role allowed for ``module``. Denies by default."""

    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            principal = getattr(request, "principal", None)
            if principal is None or not request.user.is_authenticated:
                return redirect("accounts:login")
            if not principal.is_active:
                return redirect("accounts:pending")
            if not principal.can_access(module):
                logger.warning("permission.module_denied", extra={"user": principal.user_id, "module_name": module})
                raise Forbidden("You do not have access to this module.")
            return view(request, *args, **kwargs)

        wrapped.required_module = module
        return wrapped

    return decorator


def admin_required(view):
    @module_required("team")
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        require(request.principal.is_admin)
        return view(request, *args, **kwargs)

    return wrapped
