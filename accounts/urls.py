from django.contrib.auth import views as auth_views
from django.urls import path

from accounts import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("register/", views.register_view, name="register"),
    path("register/done/", views.register_done_view, name="register_done"),
    path("pending/", views.pending_view, name="pending"),
    path("settings/", views.settings_view, name="settings"),
    path("password-reset/", views.PasswordResetView.as_view(), name="password_reset"),
    path("password-reset/sent/", views.PasswordResetDoneView.as_view(), name="password_reset_done"),
    path("reset/<uidb64>/<token>/", views.PasswordResetConfirmView.as_view(), name="password_reset_confirm"),
    path("reset/complete/", views.PasswordResetCompleteView.as_view(), name="password_reset_complete"),
    path("invite/<uidb64>/<token>/", views.InviteConfirmView.as_view(), name="invite_confirm"),
    path("invite/complete/", views.invite_complete_view, name="invite_complete"),
]
