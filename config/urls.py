from django.urls import include, path, register_converter

from core.converters import ObjectIdConverter
from dashboard import views as dashboard_views

register_converter(ObjectIdConverter, "oid")

urlpatterns = [
    path("healthz/", dashboard_views.healthz, name="healthz"),
    path("", include("dashboard.urls")),
    path("accounts/", include("accounts.urls")),
    path("brands/", include("brands.urls")),
    path("campaigns/", include("campaigns.urls")),
    path("team/", include("teams.urls")),
    path("content/", include("content.urls")),
    path("approvals/", include("approvals.urls")),
    path("drafts/", include("drafts.urls")),
    path("published/", include("publications.urls")),
]

handler403 = "core.views.permission_denied"
handler404 = "core.views.page_not_found"
handler500 = "core.views.server_error"
