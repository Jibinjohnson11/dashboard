from django.urls import path

from teams import views

app_name = "teams"

urlpatterns = [
    path("", views.member_list, name="list"),
    path("new/", views.member_create, name="create"),
    path("<int:user_id>/", views.member_detail, name="detail"),
    path("<int:user_id>/edit/", views.member_edit, name="edit"),
    path("<int:user_id>/status/", views.member_set_status, name="set_status"),
    path("<int:user_id>/resend-invite/", views.member_resend_invite, name="resend_invite"),
    path("<int:user_id>/remove-access/", views.member_remove_access, name="remove_access"),
    path("<int:user_id>/permissions/", views.member_permissions, name="permissions"),
    path("roles/", views.role_permissions, name="roles"),
    path("access/", views.access_overview, name="access"),
]
