import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, update_session_auth_hash
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_not_required
from django.contrib.auth.forms import PasswordChangeForm
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods

from accounts.forms import AccountSetPasswordForm, EmailPasswordResetForm, LoginForm, ProfileForm, RegistrationForm
from accounts.models import normalize_email
from accounts.tokens import invite_token_generator
from core import audit, ratelimit
from core.exceptions import ServiceError
from core.mongo import ping
from core.permissions import module_required
from core.utils import client_ip
from core.views import rate_limited
from teams import services as team_services
from teams.repositories import team_profiles

logger = logging.getLogger("seo.accounts")


def _safe_next(request):
    target = request.POST.get("next") or request.GET.get("next")
    if target and url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return target
    return None


@login_not_required
@sensitive_post_parameters("password")
@csrf_protect
@never_cache
@require_http_methods(["GET", "HEAD", "POST"])
def login_view(request):
    if request.user.is_authenticated:
        return redirect(_safe_next(request) or settings.LOGIN_REDIRECT_URL)

    form = LoginForm(request.POST or None, request=request)
    status = 200
    if request.method == "POST":
        ip = client_ip(request)
        email = normalize_email(request.POST.get("email", ""))
        limit, window = settings.LOGIN_RATE_LIMIT, settings.LOGIN_RATE_WINDOW
        if ratelimit.is_limited("login-email", email, limit) or ratelimit.is_limited("login-ip", ip, limit * 4):
            logger.warning("accounts.login_rate_limited", extra={"ip": ip})
            form = LoginForm(request=request, initial={"email": email})
            form.rate_limited_message = f"Too many failed sign-in attempts. Please wait {window // 60} minutes and try again."
            status = 429
        elif form.is_valid():
            user = form.user
            login(request, user)
            request.session.set_expiry(settings.REMEMBER_ME_AGE if form.cleaned_data["remember_me"] else 0)
            ratelimit.reset("login-email", email)
            audit.record(user.pk, "account.login", "user", user.pk)
            return redirect(_safe_next(request) or settings.LOGIN_REDIRECT_URL)
        else:
            ratelimit.hit("login-email", email, window)
            ratelimit.hit("login-ip", ip, window)
            logger.info("accounts.login_failed", extra={"ip": ip})
            status = 400
    return render(request, "accounts/login.html", {"form": form, "next": _safe_next(request) or ""}, status=status)


@login_not_required
@sensitive_post_parameters("password1", "password2")
@csrf_protect
@never_cache
@require_http_methods(["GET", "HEAD", "POST"])
def register_view(request):
    if request.user.is_authenticated:
        return redirect(settings.LOGIN_REDIRECT_URL)
    if settings.REGISTRATION_MODE != "open_pending":
        return render(request, "accounts/register_closed.html", status=403)

    form = RegistrationForm(request.POST or None)
    if request.method == "POST":
        ip = client_ip(request)
        if ratelimit.is_limited("register-ip", ip, settings.PASSWORD_RESET_RATE_LIMIT * 2):
            return rate_limited(request, "Too many registration attempts from your network. Please try again later.")
        if form.is_valid():
            ratelimit.hit("register-ip", ip, settings.PASSWORD_RESET_RATE_WINDOW)
            try:
                team_services.register_public(
                    form.cleaned_data["full_name"], form.cleaned_data["email"], form.cleaned_data["password1"]
                )
            except ServiceError as exc:
                form.add_error(None, str(exc))
            else:
                return redirect("accounts:register_done")
    return render(request, "accounts/register.html", {"form": form})


@login_not_required
def register_done_view(request):
    return render(request, "accounts/register_done.html")


class PasswordResetView(auth_views.PasswordResetView):
    """Always shows the same confirmation; rate-limited per IP and per email."""

    form_class = EmailPasswordResetForm
    template_name = "accounts/password_reset.html"
    email_template_name = "emails/password_reset.txt"
    html_email_template_name = "emails/password_reset.html"
    subject_template_name = "emails/password_reset_subject.txt"
    success_url = reverse_lazy("accounts:password_reset_done")

    def post(self, request, *args, **kwargs):
        ip = client_ip(request)
        email = normalize_email(request.POST.get("email", ""))
        limit, window = settings.PASSWORD_RESET_RATE_LIMIT, settings.PASSWORD_RESET_RATE_WINDOW
        if ratelimit.is_limited("reset-ip", ip, limit * 2) or ratelimit.is_limited("reset-email", email, limit):
            logger.warning("accounts.password_reset_rate_limited", extra={"ip": ip})
            return rate_limited(request, "Too many password reset requests. Please try again later.")
        ratelimit.hit("reset-ip", ip, window)
        if email:
            ratelimit.hit("reset-email", email, window)
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        logger.info("accounts.password_reset_requested")  # never log the email or token
        return super().form_valid(form)


class PasswordResetDoneView(auth_views.PasswordResetDoneView):
    template_name = "accounts/password_reset_done.html"


class PasswordResetConfirmView(auth_views.PasswordResetConfirmView):
    form_class = AccountSetPasswordForm
    template_name = "accounts/password_reset_confirm.html"
    success_url = reverse_lazy("accounts:password_reset_complete")
    page_title = "Choose a new password"

    def get_context_data(self, **kwargs):
        return super().get_context_data(page_title=self.page_title, **kwargs)

    def form_valid(self, form):
        response = super().form_valid(form)
        audit.record(form.user.pk, "account.password_reset", "user", form.user.pk)
        return response


class InviteConfirmView(PasswordResetConfirmView):
    """Account setup for invited team members (separate token salt and lifetime)."""

    token_generator = invite_token_generator
    reset_url_token = "set-up"
    success_url = reverse_lazy("accounts:invite_complete")
    page_title = "Set up your account"

    def form_valid(self, form):
        response = super().form_valid(form)
        team_services.complete_invite(form.user)
        return response


class PasswordResetCompleteView(auth_views.PasswordResetCompleteView):
    template_name = "accounts/password_reset_complete.html"


@login_not_required
def invite_complete_view(request):
    return render(request, "accounts/password_reset_complete.html", {"invite": True})


@never_cache
def pending_view(request):
    principal = request.principal
    if principal is not None and principal.is_active:
        return redirect(settings.LOGIN_REDIRECT_URL)
    return render(request, "accounts/pending.html")


@module_required("settings")
@sensitive_post_parameters("old_password", "new_password1", "new_password2")
@require_http_methods(["GET", "POST"])
def settings_view(request):
    principal = request.principal
    profile_form = ProfileForm(initial={"full_name": request.user.full_name}, prefix="profile")
    password_form = PasswordChangeForm(request.user, prefix="password")

    if request.method == "POST":
        if "save_profile" in request.POST:
            profile_form = ProfileForm(request.POST, prefix="profile")
            if profile_form.is_valid():
                name = profile_form.cleaned_data["full_name"]
                request.user.full_name = name
                request.user.save(update_fields=["full_name"])
                team_profiles.update_for_user(request.user.pk, {"display_name": name})
                audit.record(request.user.pk, "account.profile_updated", "user", request.user.pk)
                messages.success(request, "Your profile has been updated.")
                return redirect("accounts:settings")
        elif "change_password" in request.POST:
            password_form = PasswordChangeForm(request.user, request.POST, prefix="password")
            if password_form.is_valid():
                user = password_form.save()
                update_session_auth_hash(request, user)
                audit.record(user.pk, "account.password_changed", "user", user.pk)
                messages.success(request, "Your password has been changed.")
                return redirect("accounts:settings")

    system = None
    if principal.is_admin:
        try:
            ping()
            mongo_status = "Connected"
        except Exception:
            mongo_status = "Unavailable"
        system = {
            "environment": settings.ENVIRONMENT,
            "registration_mode": "Public sign-up (admin approval)" if settings.REGISTRATION_MODE == "open_pending" else "Invite only",
            "email_backend": settings.EMAIL_BACKEND.rsplit(".", 2)[-2],
            "mongo_status": mongo_status,
            "mongo_mock": settings.MONGODB_USE_MOCK,
            "session_hours": settings.SESSION_COOKIE_AGE // 3600,
        }
    return render(
        request,
        "accounts/settings.html",
        {"profile_form": profile_form, "password_form": password_form, "system": system},
    )
