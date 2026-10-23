from django import template
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from core import sanitize
from core.permissions import ACCOUNT_STATUS_LABELS, ROLE_LABELS

register = template.Library()

STATUS_STYLES = {
    # content
    "draft": ("Draft", "neutral"),
    "pending": ("Pending Approval", "warning"),
    "approved": ("Approved", "success"),
    "needs_edit": ("Needs Edit", "orange"),
    "rejected": ("Rejected", "danger"),
    "published": ("Published", "primary"),
    # brands / campaigns / accounts
    "active": ("Active", "success"),
    "inactive": ("Inactive", "neutral"),
    "archived": ("Archived", "neutral"),
    "paused": ("Paused", "warning"),
    "completed": ("Completed", "primary"),
    "invited": ("Invited", "primary"),
    # approval actions
    "approve": ("Approved", "success"),
    "request_changes": ("Changes Requested", "orange"),
    "reject": ("Rejected", "danger"),
    "submit": ("Submitted", "warning"),
}


AUDIT_LABELS = {
    "account.registered": "Registered an account",
    "account.login": "Signed in",
    "account.password_reset": "Reset their password",
    "account.password_changed": "Changed their password",
    "account.profile_updated": "Updated their profile",
    "brand.created": "Created a brand",
    "brand.updated": "Updated a brand",
    "brand.archived": "Archived a brand",
    "brand.restored": "Restored a brand",
    "campaign.created": "Created a campaign",
    "campaign.updated": "Updated a campaign",
    "campaign.archived": "Archived a campaign",
    "campaign.restored": "Restored a campaign",
    "content.created": "Created content",
    "content.saved": "Saved content",
    "content.submitted": "Submitted content for approval",
    "content.approve": "Approved content",
    "content.request_changes": "Requested changes on content",
    "content.reject": "Rejected content",
    "content.revision_started": "Started a new content revision",
    "publication.created": "Recorded a publication",
    "publication.updated": "Updated publication details",
    "publication.exported": "Exported published content",
    "member.invited": "Invited a team member",
    "member.invite_resent": "Resent an invitation",
    "member.invite_accepted": "Accepted their invitation",
    "member.updated": "Updated a team member",
    "member.role_assigned": "Assigned a role",
    "member.status_active": "Activated an account",
    "member.status_invited": "Re-enabled an account",
    "member.status_inactive": "Deactivated an account",
    "member.access_removed": "Removed a member's access",
    "member.permissions_updated": "Changed a member's role or permissions",
    "member.permissions_reset": "Reset a member's permissions",
    "role.permissions_updated": "Changed role default permissions",
    "role.permissions_reset": "Reset role default permissions",
}


@register.filter
def audit_label(action):
    return AUDIT_LABELS.get(action, str(action or "").replace(".", " ").replace("_", " ").capitalize())


@register.simple_tag
def icon(name, css_class=""):
    return format_html('<svg class="icon {}" aria-hidden="true" focusable="false"><use href="#i-{}"></use></svg>', css_class, name)


@register.filter
def status_badge(status):
    label, tone = STATUS_STYLES.get(status, (str(status or "—").replace("_", " ").title(), "neutral"))
    return format_html('<span class="badge badge-{}">{}</span>', tone, label)


@register.filter
def account_badge(status):
    tone = {"active": "success", "pending": "warning", "inactive": "neutral", "invited": "primary"}.get(status, "neutral")
    return format_html('<span class="badge badge-{}">{}</span>', tone, ACCOUNT_STATUS_LABELS.get(status, status or "—"))


@register.filter
def status_label(status):
    return STATUS_STYLES.get(status, (str(status or "").replace("_", " ").title(), ""))[0]


@register.filter
def role_label(role):
    return ROLE_LABELS.get(role, "Pending")


@register.filter(name="sanitize_html")
def sanitize_html_filter(value):
    # Content is sanitised before storage; sanitising again on output is defence in depth.
    return mark_safe(sanitize.sanitize_html(value))


@register.filter
def has_perm(principal, permission):
    return bool(principal) and principal.has_perm(permission)


@register.filter
def get_item(mapping, key):
    if not mapping:
        return None
    return mapping.get(key)


@register.filter
def first_name(name):
    parts = str(name or "").split()
    return parts[0] if parts else ""


@register.filter
def initials(name):
    parts = [p for p in str(name or "?").split() if p]
    return "".join(p[0] for p in parts[:2]).upper() or "?"
