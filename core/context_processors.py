from django.conf import settings

NAV_ITEMS = [
    ("dashboard", "Dashboard", "dashboard:home", "layout-dashboard"),
    ("brands", "Brands", "brands:list", "building"),
    ("campaigns", "Campaigns", "campaigns:list", "target"),
    ("team", "Team", "teams:list", "users"),
    ("content", "Content Writing", "content:list", "pen"),
    ("approvals", "Content Approvals", "approvals:list", "check-circle"),
    ("drafts", "Content Drafts", "drafts:list", "inbox"),
    ("published", "Published Content", "publications:list", "globe"),
    ("settings", "Settings", "accounts:settings", "settings"),
]

NAMESPACE_MODULE = {
    "dashboard": "dashboard",
    "brands": "brands",
    "campaigns": "campaigns",
    "teams": "team",
    "content": "content",
    "approvals": "approvals",
    "drafts": "drafts",
    "publications": "published",
    "accounts": "settings",
}


def navigation(request):
    principal = getattr(request, "principal", None)
    if not request.user.is_authenticated or principal is None or not principal.is_active:
        return {"registration_open": settings.REGISTRATION_MODE == "open_pending"}

    match = getattr(request, "resolver_match", None)
    active_module = NAMESPACE_MODULE.get(match.namespace if match else "", "")
    nav = [
        {"key": key, "label": label, "url_name": url_name, "icon": icon, "active": key == active_module}
        for key, label, url_name, icon in NAV_ITEMS
        if principal.can_access(key)
    ]
    from dashboard.services import notification_summary

    return {
        "nav_items": nav,
        "active_module": active_module,
        "principal": principal,
        "notifications": notification_summary(principal),
        "registration_open": settings.REGISTRATION_MODE == "open_pending",
    }
