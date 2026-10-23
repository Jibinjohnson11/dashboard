from django.urls import path

from content import views

app_name = "content"

urlpatterns = [
    path("", views.content_list, name="list"),
    path("new/", views.content_create, name="create"),
    path("<oid:content_id>/", views.content_detail, name="detail"),
    path("<oid:content_id>/edit/", views.content_edit, name="edit"),
    path("<oid:content_id>/revise/", views.content_revise, name="revise"),
    path("<oid:content_id>/versions/<int:version_number>/", views.content_version, name="version"),
]
