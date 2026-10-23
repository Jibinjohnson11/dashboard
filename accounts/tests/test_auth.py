import re

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import override_settings
from django.urls import reverse

from core.testing import PASSWORD, MongoTestCase
from teams.repositories import team_profiles

User = get_user_model()


class RegistrationTests(MongoTestCase):
    def post_register(self, email="new@example.com", password=PASSWORD, **extra):
        data = {"full_name": "New Person", "email": email, "password1": password, "password2": password}
        data.update(extra)
        return self.client.post(reverse("accounts:register"), data)

    def test_registration_creates_pending_account_without_role(self):
        response = self.post_register(email="  New@Example.COM ")
        self.assertRedirects(response, reverse("accounts:register_done"))
        user = User.objects.get(email="new@example.com")  # normalised
        self.assertFalse(user.is_active)
        self.assertFalse(user.is_staff or user.is_superuser)
        profile = team_profiles.for_user(user.pk)
        self.assertEqual(profile["status"], "pending")
        self.assertIsNone(profile["role"])
        self.assertTrue(user.check_password(PASSWORD))
        self.assertNotEqual(user.password, PASSWORD)  # hashed

    def test_cannot_choose_role_during_registration(self):
        self.post_register(role="admin", is_superuser="on", status="active")
        user = User.objects.get(email="new@example.com")
        self.assertIsNone(team_profiles.for_user(user.pk)["role"])
        self.assertFalse(user.is_superuser)

    def test_weak_and_mismatched_passwords_rejected(self):
        response = self.post_register(password="password")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(email="new@example.com").exists())
        response = self.client.post(reverse("accounts:register"), {
            "full_name": "X Y", "email": "a@example.com", "password1": PASSWORD, "password2": PASSWORD + "x"})
        self.assertContains(response, "do not match")

    def test_duplicate_email_does_not_create_second_account_or_reveal_existence(self):
        self.post_register()
        response = self.post_register(email="NEW@example.com")
        self.assertRedirects(response, reverse("accounts:register_done"))
        self.assertEqual(User.objects.filter(email__iexact="new@example.com").count(), 1)

    def test_pending_account_cannot_log_in(self):
        self.post_register()
        response = self.client.post(reverse("accounts:login"), {"email": "new@example.com", "password": PASSWORD})
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Invalid email or password", status_code=400)

    @override_settings(REGISTRATION_MODE="invite_only")
    def test_invite_only_mode_disables_public_registration(self):
        response = self.post_register()
        self.assertEqual(response.status_code, 403)
        self.assertFalse(User.objects.filter(email="new@example.com").exists())


class ActivationTests(MongoTestCase):
    def test_admin_activates_pending_account_with_role(self):
        admin = self.make_user("admin@example.com", "admin")
        pending = User.objects.create_user(email="p@example.com", password=PASSWORD, full_name="Pen Ding")
        team_profiles.insert({"django_user_id": pending.pk, "display_name": "Pen Ding", "email": "p@example.com", "role": None, "status": "pending"})
        self.login(admin)
        response = self.client.post(reverse("teams:set_status", args=[pending.pk]), {"status": "active", "role": "team_member"})
        self.assertRedirects(response, reverse("teams:detail", args=[pending.pk]))
        pending.refresh_from_db()
        self.assertTrue(pending.is_active)
        profile = team_profiles.for_user(pending.pk)
        self.assertEqual((profile["role"], profile["status"]), ("team_member", "active"))
        self.client.logout()
        response = self.client.post(reverse("accounts:login"), {"email": "p@example.com", "password": PASSWORD})
        self.assertRedirects(response, reverse("dashboard:home"))

    def test_non_admin_cannot_activate_accounts(self):
        writer = self.make_user("w@example.com", "team_member")
        pending = User.objects.create_user(email="p@example.com", password=PASSWORD, full_name="Pen Ding")
        team_profiles.insert({"django_user_id": pending.pk, "display_name": "Pen Ding", "email": "p@example.com", "role": None, "status": "pending"})
        self.login(writer)
        response = self.client.post(reverse("teams:set_status", args=[pending.pk]), {"status": "active", "role": "admin"})
        self.assertEqual(response.status_code, 403)
        self.assertIsNone(team_profiles.for_user(pending.pk)["role"])

    def test_invited_member_sets_password_via_email_link(self):
        admin = self.make_user("admin@example.com", "admin")
        self.login(admin)
        response = self.client.post(reverse("teams:create"), {
            "full_name": "Ivy Invite", "email": "ivy@example.com", "role": "team_member", "status": "active"})
        user = User.objects.get(email="ivy@example.com")
        self.assertRedirects(response, reverse("teams:detail", args=[user.pk]))
        self.assertFalse(user.has_usable_password())
        self.assertEqual(team_profiles.for_user(user.pk)["status"], "invited")
        self.assertEqual(len(mail.outbox), 1)
        self.assertNotIn(PASSWORD, mail.outbox[0].body)
        link = re.search(r"http://testserver(\S+)", mail.outbox[0].body).group(1)

        self.client.logout()
        response = self.client.get(link, follow=True)
        self.assertContains(response, "Set up your account")
        response = self.client.post(response.redirect_chain[-1][0], {"new_password1": PASSWORD, "new_password2": PASSWORD})
        self.assertRedirects(response, reverse("accounts:invite_complete"))
        self.assertEqual(team_profiles.for_user(user.pk)["status"], "active")
        self.assertTrue(self.client.login(email="ivy@example.com", password=PASSWORD))


class LoginLogoutTests(MongoTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.make_user("member@example.com", "team_member")

    def test_login_success_and_session_expiry(self):
        response = self.client.post(reverse("accounts:login"), {"email": "MEMBER@example.com", "password": PASSWORD})
        self.assertRedirects(response, reverse("dashboard:home"))
        self.assertTrue(self.client.session.get_expire_at_browser_close())

    def test_remember_me_sets_persistent_session(self):
        self.client.post(reverse("accounts:login"), {"email": "member@example.com", "password": PASSWORD, "remember_me": "on"})
        self.assertFalse(self.client.session.get_expire_at_browser_close())

    def test_invalid_credentials_generic_message(self):
        wrong = self.client.post(reverse("accounts:login"), {"email": "member@example.com", "password": "nope-nope-1"})
        unknown = self.client.post(reverse("accounts:login"), {"email": "ghost@example.com", "password": "nope-nope-1"})
        for response in (wrong, unknown):
            self.assertContains(response, "Invalid email or password", status_code=400)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_safe_redirect_after_login(self):
        url = reverse("accounts:login")
        ok = self.client.post(url, {"email": "member@example.com", "password": PASSWORD, "next": "/brands/"})
        self.assertRedirects(ok, "/brands/", fetch_redirect_response=False)
        self.client.logout()
        evil = self.client.post(url, {"email": "member@example.com", "password": PASSWORD, "next": "https://evil.example/phish"})
        self.assertRedirects(evil, reverse("dashboard:home"))

    def test_logout_requires_post(self):
        self.login(self.user)
        self.assertEqual(self.client.get(reverse("accounts:logout")).status_code, 405)
        response = self.client.post(reverse("accounts:logout"))
        self.assertRedirects(response, reverse("accounts:login"))
        self.assertNotIn("_auth_user_id", self.client.session)

    @override_settings(LOGIN_RATE_LIMIT=3)
    def test_login_rate_limiting(self):
        url = reverse("accounts:login")
        for _ in range(3):
            self.client.post(url, {"email": "member@example.com", "password": "wrong-pass-1"})
        # Even the correct password is refused while limited.
        response = self.client.post(url, {"email": "member@example.com", "password": PASSWORD})
        self.assertEqual(response.status_code, 429)
        self.assertContains(response, "Too many failed sign-in attempts", status_code=429)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_deactivated_user_is_logged_out(self):
        self.login(self.user)
        self.assertEqual(self.client.get(reverse("dashboard:home")).status_code, 200)
        self.user.is_active = False
        self.user.save()
        response = self.client.get(reverse("dashboard:home"))
        self.assertRedirects(response, f"{reverse('accounts:login')}?next=/", fetch_redirect_response=False)

    def test_pages_require_login(self):
        for name in ["dashboard:home", "brands:list", "campaigns:list", "content:list", "approvals:list", "publications:list", "teams:list"]:
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 302, name)
            self.assertTrue(response["Location"].startswith(reverse("accounts:login")), name)


class PasswordResetTests(MongoTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.make_user("member@example.com", "team_member")

    def test_full_password_reset_flow(self):
        response = self.client.post(reverse("accounts:password_reset"), {"email": "Member@Example.com"})
        self.assertRedirects(response, reverse("accounts:password_reset_done"))
        self.assertEqual(len(mail.outbox), 1)
        link = re.search(r"http://testserver(\S+)", mail.outbox[0].body).group(1)
        response = self.client.get(link, follow=True)
        set_password_url = response.redirect_chain[-1][0]
        self.assertContains(response, "Choose a new password")
        new_password = "An0ther-Str0ng-Pass!"
        response = self.client.post(set_password_url, {"new_password1": new_password, "new_password2": new_password})
        self.assertRedirects(response, reverse("accounts:password_reset_complete"))
        self.assertTrue(self.client.login(email="member@example.com", password=new_password))
        # The link is single-use.
        self.client.logout()
        response = self.client.get(link, follow=True)
        self.assertContains(response, "no longer valid")

    def test_unknown_email_gets_same_response_and_no_email(self):
        response = self.client.post(reverse("accounts:password_reset"), {"email": "ghost@example.com"})
        self.assertRedirects(response, reverse("accounts:password_reset_done"))
        self.assertEqual(len(mail.outbox), 0)

    @override_settings(PASSWORD_RESET_TIMEOUT=60)
    def test_expired_token_rejected(self):
        from unittest import mock

        from django.contrib.auth.tokens import default_token_generator

        self.client.post(reverse("accounts:password_reset"), {"email": "member@example.com"})
        link = re.search(r"http://testserver(\S+)", mail.outbox[0].body).group(1)
        real_now = default_token_generator._now()
        with mock.patch.object(type(default_token_generator), "_now", return_value=real_now.replace(year=real_now.year + 1)):
            response = self.client.get(link, follow=True)
        self.assertContains(response, "no longer valid")

    @override_settings(PASSWORD_RESET_RATE_LIMIT=2)
    def test_password_reset_rate_limited(self):
        url = reverse("accounts:password_reset")
        self.client.post(url, {"email": "member@example.com"})
        self.client.post(url, {"email": "member@example.com"})
        response = self.client.post(url, {"email": "member@example.com"})
        self.assertEqual(response.status_code, 429)
        self.assertEqual(len(mail.outbox), 2)
