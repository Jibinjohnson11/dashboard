"""Permission, cross-brand isolation and security tests."""
from django.test import Client
from django.urls import reverse

from core.sanitize import sanitize_html
from core.testing import PASSWORD, WorkflowFixture
from teams.repositories import team_profiles


class ModuleAccessTests(WorkflowFixture):
    MODULE_URLS = {
        "dashboard": "dashboard:home",
        "brands": "brands:list",
        "campaigns": "campaigns:list",
        "team": "teams:list",
        "content": "content:list",
        "approvals": "approvals:list",
        "published": "publications:list",
        "settings": "accounts:settings",
    }
    EXPECTED = {
        "admin": {"dashboard", "brands", "campaigns", "team", "content", "approvals", "published", "settings"},
        "team_member": {"dashboard", "brands", "campaigns", "content", "published", "settings"},
        "reviewer": {"dashboard", "brands", "campaigns", "approvals", "published", "settings"},
        "client": {"dashboard", "brands", "approvals", "published", "settings"},
    }

    def test_role_module_matrix(self):
        users = {"admin": self.admin, "team_member": self.writer, "reviewer": self.reviewer, "client": self.client_user}
        for role, user in users.items():
            self.client.force_login(user)
            for module, url_name in self.MODULE_URLS.items():
                status = self.client.get(reverse(url_name)).status_code
                expected = 200 if module in self.EXPECTED[role] else 403
                self.assertEqual(status, expected, f"{role} -> {module}")

    def test_sidebar_only_shows_permitted_modules(self):
        self.client.force_login(self.client_user)
        html = self.client.get(reverse("dashboard:home")).content.decode()
        self.assertIn(reverse("approvals:list"), html)
        self.assertNotIn('href="/team/"', html)
        self.assertNotIn('href="/content/"', html)

    def test_pending_profile_has_no_access(self):
        team_profiles.update_for_user(self.writer.pk, {"status": "pending"})
        self.client.force_login(self.writer)
        self.assertRedirects(self.client.get(reverse("brands:list")), reverse("accounts:pending"))

    def test_user_without_profile_is_denied(self):
        team_profiles.col.delete_many({"django_user_id": self.writer.pk})
        self.client.force_login(self.writer)
        self.assertRedirects(self.client.get(reverse("dashboard:home")), reverse("accounts:pending"))

    def test_superuser_without_profile_bootstraps_admin(self):
        from django.contrib.auth import get_user_model

        root = get_user_model().objects.create_superuser(email="root@example.com", password=PASSWORD, full_name="Root")
        self.client.force_login(root)
        self.assertEqual(self.client.get(reverse("teams:list")).status_code, 200)
        self.assertEqual(team_profiles.for_user(root.pk)["role"], "admin")


class CrossBrandIsolationTests(WorkflowFixture):
    def setUp(self):
        super().setUp()
        self.acme_item = self.make_content(self.writer, self.campaign, title="Acme Post")
        self.submit(self.writer, self.acme_item)
        self.globex_item = self.make_content(self.writer2, self.other_campaign, title="Globex Secret Post")
        self.submit(self.writer2, self.globex_item)

    def test_client_cannot_see_other_clients_brand_or_content(self):
        self.client.force_login(self.client_user)
        self.assertEqual(self.client.get(reverse("brands:detail", args=[self.other_brand["id"]])).status_code, 404)
        self.assertEqual(self.client.get(reverse("approvals:review", args=[self.globex_item["id"]])).status_code, 404)
        listing = self.client.get(reverse("approvals:list")).content.decode()
        self.assertIn("Acme Post", listing)
        self.assertNotIn("Globex Secret Post", listing)
        search = self.client.get(reverse("dashboard:search"), {"q": "Globex"}).content.decode()
        self.assertNotIn("Globex Secret Post", search)

    def test_client_cannot_approve_other_brand_content(self):
        self.client.force_login(self.client_user)
        response = self.client.post(reverse("approvals:review", args=[self.globex_item["id"]]), {"action": "approve", "version_number": 1})
        self.assertEqual(response.status_code, 404)
        from content.repositories import contents

        self.assertEqual(contents.get(self.globex_item["_id"])["status"], "pending")

    def test_client_cannot_see_drafts(self):
        draft = self.make_content(self.writer, self.campaign, title="Unsubmitted Draft")
        self.client.force_login(self.client_user)
        self.assertEqual(self.client.get(reverse("approvals:review", args=[draft["id"]])).status_code, 404)

    def test_writer_cannot_access_unassigned_campaign_content(self):
        self.client.force_login(self.writer)
        self.assertEqual(self.client.get(reverse("content:detail", args=[self.globex_item["id"]])).status_code, 404)
        self.assertEqual(self.client.get(reverse("campaigns:detail", args=[self.other_campaign["id"]])).status_code, 404)

    def test_writer_cannot_create_content_in_unassigned_campaign(self):
        self.client.force_login(self.writer)
        response = self.client.post(reverse("content:create"), {
            "brand_id": self.other_brand["id"], "campaign_id": self.other_campaign["id"], "title": "Sneaky",
            "keyword": "x", "content_type": "blog", "body_html": "<p>x</p>", "action": "save"})
        self.assertEqual(response.status_code, 200)  # form re-rendered with an error
        from content.repositories import contents

        self.assertEqual(contents.count({"title": "Sneaky"}), 0)

    def test_reviewer_scoped_to_assigned_brands(self):
        self.client.force_login(self.reviewer)
        self.assertEqual(self.client.get(reverse("approvals:review", args=[self.acme_item["id"]])).status_code, 200)
        self.assertEqual(self.client.get(reverse("approvals:review", args=[self.globex_item["id"]])).status_code, 404)

    def test_publication_export_is_scoped(self):
        from publications.services import publish

        for writer, item in ((self.writer, self.acme_item), (self.writer2, self.globex_item)):
            reviewer = self.reviewer if item is self.acme_item else self.admin
            self.approve(reviewer, item)
            publish(self.principal(writer), content_id=item["id"], published_url=f"https://pub.example/{item['id']}",
                    published_by_user_id=writer.pk, published_date=None)
        self.client.force_login(self.client_user)
        csv_text = self.client.get(reverse("publications:export")).content.decode()
        self.assertIn("Acme Post", csv_text)
        self.assertNotIn("Globex Secret Post", csv_text)


class PrivilegeEscalationTests(WorkflowFixture):
    def test_non_admin_cannot_create_brands_or_campaigns(self):
        for user in (self.writer, self.reviewer):
            self.client.force_login(user)
            self.assertEqual(self.client.post(reverse("brands:create"), {"name": "Hack", "status": "active"}).status_code, 403)
            self.assertEqual(self.client.post(reverse("campaigns:create"), {"name": "Hack"}).status_code, 403)
            self.assertEqual(self.client.post(reverse("brands:archive", args=[self.brand["id"]])).status_code, 403)

    def test_non_admin_cannot_change_roles(self):
        self.client.force_login(self.writer)
        response = self.client.post(reverse("teams:edit", args=[self.writer.pk]), {
            "full_name": "Wendy", "email": "writer@example.com", "role": "admin", "status": "active"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(team_profiles.for_user(self.writer.pk)["role"], "team_member")

    def test_admin_cannot_demote_self(self):
        self.client.force_login(self.admin)
        self.client.post(reverse("teams:edit", args=[self.admin.pk]), {
            "full_name": "Ada", "email": "admin@example.com", "role": "team_member", "status": "active"})
        self.assertEqual(team_profiles.for_user(self.admin.pk)["role"], "admin")

    def test_writer_cannot_edit_someone_elses_draft(self):
        item = self.make_content(self.writer, self.campaign)
        other = self.make_user("w3@example.com", "team_member")
        self.make_campaign(self.admin, self.brand, "Second", members=[other])
        from campaigns.repositories import campaign_assignments

        campaign_assignments.replace_for_user(other.pk, {self.campaign["_id"]})
        self.client.force_login(other)
        response = self.client.get(reverse("content:edit", args=[item["id"]]))
        self.assertRedirects(response, reverse("content:detail", args=[item["id"]]))


class SecurityTests(WorkflowFixture):
    def test_csrf_enforced(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.admin)
        response = csrf_client.post(reverse("brands:create"), {"name": "No Token", "status": "active"})
        self.assertEqual(response.status_code, 403)
        response = Client(enforce_csrf_checks=True).post(reverse("accounts:login"), {"email": "admin@example.com", "password": PASSWORD})
        self.assertEqual(response.status_code, 403)

    def test_html_sanitisation(self):
        dirty = (
            '<h2 onclick="x()">Title</h2><script>alert(1)</script><p style="color:red">Text '
            '<a href="javascript:alert(1)">bad</a> <a href="https://ok.example" target="_blank">ok</a>'
            '<img src=x onerror=alert(1)><iframe src="https://evil"></iframe></p>'
        )
        clean = sanitize_html(dirty)
        for bad in ("script", "onclick", "onerror", "javascript:", "style=", "<img", "<iframe", "target="):
            self.assertNotIn(bad, clean)
        self.assertIn("<h2>Title</h2>", clean)
        self.assertIn('href="https://ok.example"', clean)
        self.assertIn('rel="noopener noreferrer nofollow"', clean)

    def test_stored_content_is_sanitised(self):
        from content.services import current_version

        item = self.make_content(self.writer, self.campaign, body='<p>Hi<script>steal()</script><b onmouseover="x">b</b></p>')
        body = current_version(item)["body_html"]
        self.assertNotIn("script", body)
        self.assertNotIn("onmouseover", body)

    def test_invalid_object_ids_return_404(self):
        self.client.force_login(self.admin)
        for path in ["/brands/not-an-id/", "/brands/123/", "/content/zzzzzzzzzzzzzzzzzzzzzzzz/", "/approvals/%24where/"]:
            self.assertEqual(self.client.get(path).status_code, 404, path)
        # Valid format, but no such record.
        self.assertEqual(self.client.get("/brands/0123456789abcdef01234567/").status_code, 404)
        # Invalid ids in query-string filters are ignored, not errors.
        self.assertEqual(self.client.get(reverse("content:list"), {"brand": "bad", "campaign": "{'$ne':1}"}).status_code, 200)

    def test_search_input_is_escaped_not_regex(self):
        self.client.force_login(self.admin)
        for q in [".*", "(", "[a-", "$where", "\\"]:
            self.assertEqual(self.client.get(reverse("brands:list"), {"q": q}).status_code, 200)
        page = self.client.get(reverse("brands:list"), {"q": ".*"}).content.decode()
        self.assertNotIn("Acme", page)

    def test_output_is_escaped(self):
        self.make_brand(self.admin, '<script>alert("x")</script>')
        self.client.force_login(self.admin)
        html = self.client.get(reverse("brands:list")).content.decode()
        self.assertNotIn('<script>alert("x")</script>', html)
        self.assertIn("&lt;script&gt;", html)

    def test_security_headers(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("dashboard:home"))
        self.assertEqual(response["X-Frame-Options"], "DENY")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertIn("default-src 'self'", response["Content-Security-Policy"])
        self.assertIn("frame-ancestors 'none'", response["Content-Security-Policy"])
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertIn("camera=()", response["Permissions-Policy"])

    def test_csv_export_neutralises_formulas(self):
        from publications.services import _csv_safe

        self.assertEqual(_csv_safe("=HYPERLINK(1)"), "'=HYPERLINK(1)")
        self.assertEqual(_csv_safe("+1"), "'+1")
        self.assertEqual(_csv_safe("Normal"), "Normal")

    def test_healthcheck(self):
        response = Client().get(reverse("healthz"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["checks"], {"database": "ok", "mongodb": "ok"})
