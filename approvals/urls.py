from django.urls import path

from approvals import views

app_name = "approvals"

urlpatterns = [
    path("", views.approval_list, name="list"),
    path("<oid:content_id>/", views.review, name="review"),
]
