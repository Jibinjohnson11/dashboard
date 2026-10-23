from django.urls import path

from campaigns import views

app_name = "campaigns"

urlpatterns = [
    path("", views.campaign_list, name="list"),
    path("new/", views.campaign_create, name="create"),
    path("<oid:campaign_id>/", views.campaign_detail, name="detail"),
    path("<oid:campaign_id>/edit/", views.campaign_edit, name="edit"),
    path("<oid:campaign_id>/archive/", views.campaign_archive, name="archive"),
    path("<oid:campaign_id>/restore/", views.campaign_restore, name="restore"),
]
