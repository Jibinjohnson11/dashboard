from django.urls import path

from drafts import views

app_name = "drafts"

urlpatterns = [
    path("", views.draft_list, name="list"),
    path("<oid:content_id>/", views.draft_detail, name="detail"),
]
