from django.urls import path

from publications import views

app_name = "publications"

urlpatterns = [
    path("", views.publication_list, name="list"),
    path("new/", views.publication_create, name="create"),
    path("export.csv", views.publication_export, name="export"),
    path("<oid:publication_id>/", views.publication_detail, name="detail"),
    path("<oid:publication_id>/edit/", views.publication_edit, name="edit"),
]
