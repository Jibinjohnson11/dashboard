from django.urls import path

from brands import views

app_name = "brands"

urlpatterns = [
    path("", views.brand_list, name="list"),
    path("new/", views.brand_create, name="create"),
    path("<oid:brand_id>/", views.brand_detail, name="detail"),
    path("<oid:brand_id>/edit/", views.brand_edit, name="edit"),
    path("<oid:brand_id>/logo/", views.brand_logo, name="logo"),
    path("<oid:brand_id>/archive/", views.brand_archive, name="archive"),
    path("<oid:brand_id>/restore/", views.brand_restore, name="restore"),
]
