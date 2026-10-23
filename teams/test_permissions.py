"""Team Management & Permissions: role defaults, per-member overrides and their enforcement."""
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from approvals.services import can_review
from content.repositories import contents
from core.audit import audit_logs
from core.permissions import ALL_PERMISSIONS, MODULE_ROLES, PERMISSION_CATALOG, ROLE_CEILINGS
from core.testing import PASSWORD, WorkflowFixture
from publications.repositories import publications
from teams.repositories import brand_access, role_permissions, team_profiles

User = get_user_model()


class PermissionTestBase(WorkflowFixture):
    def set_member_perms(self, user, role, perms):
        self.client.force_login(self.admin)
        return self.client.post(reverse("teams:permissions", args=[user.pk]), {"role": role, "perm": perms})

    def writer_defaults(self):
        return sorted(ROLE_CEILINGS["team_member"])

    def without(self, perms, *remove):
        return [p for p in perms if p not in remove]


class PermissionModelTests(PermissionTestBase):
    def test_role_ceilings_match_module_roles(self):
        for module, _, _ in PERMISSION_CATALOG:
            for role, ceiling in ROLE_CEILINGS.items():
                self.assertEqual(f"{module}.view" in ceiling, role in MODULE_ROLES[module], (role, module))

    def test_defaults_reproduce_existing_access(self):
        self.assertEqual(self.principal(self.writer).permissions, set(ROLE_CEILINGS["team_member"]))
        self.assertEqual(self.principal(self.admin).permissions, set(ALL_PERMISSIONS))

    def test_invalid_stored_overrides_are_denied(self):
        # Even if a record were tampered with directly, nothing beyond the role's limits takes effect.
        team_profiles.update_for_user(self.client_user.pk, {"permission_overrides": {
            "grants": ["team.view", "team.manage", "content.view", "content.create", "bogus.view", "bogus.thing"], "revokes": []}})
        perms = self.principal(self.client_user).permissions
        for key in ("team.view", "team.manage", "content.view", "content.create", "bogus.view", "bogus.thing"):
            self.assertNotIn(key, perms)
        self.client.force_login(self.client_user)
        self.assertEqual(self.client.get(reverse("teams:list")).status_code, 403)
        self.assertEqual(self.client.get(reverse("content:list")).status_code, 403)


class MemberManagementTests(PermissionTestBase):
    def test_admin_adds_member_with_role_defaults(self):
        self.client.force_login(self.admin)
        self.client.post(reverse("teams:create"), {"full_name": "Nora New", "email": "nora@example.com", "role": "reviewer",
                                                    "status": "active", "brand_ids": [self.brand["id"]]})
        user = User.objects.get(email="nora@example.com")
        profile = team_profiles.for_user(user.pk)
        self.assertEqual(profile["role"], "reviewer")
        listing = self.client.get(reverse("teams:list"), {"q": "nora"})
        self.assertContains(listing, "Content Approvals")  # accessible-modules column
        self.assertContains(listing, "Never")              # last login column

    def test_admin_changes_role_via_permission_editor(self):
        response = self.set_member_perms(self.writer, "reviewer", sorted(ROLE_CEILINGS["reviewer"]))
        self.assertRedirects(response, reverse("teams:permissions", args=[self.writer.pk]))
        profile = team_profiles.for_user(self.writer.pk)
        self.assertEqual(profile["role"], "reviewer")
        self.assertIsNone(profile["permission_overrides"])
        # Campaign assignments don't apply to reviewers and are dropped by the role rules.
        from campaigns.repositories import campaigns

        self.assertNotIn(self.writer.pk, campaigns.get(self.campaign["id"])["assigned_user_ids"])

    def test_list_and_pages_render(self):
        self.client.force_login(self.admin)
        for url in (reverse("teams:list"), reverse("teams:roles"), reverse("teams:roles") + "?role=client",
                    reverse("teams:access"), reverse("teams:permissions", args=[self.writer.pk]),
                    reverse("teams:permissions", args=[self.admin.pk]), reverse("accounts:settings")):
            self.assertEqual(self.client.get(url).status_code, 200, url)
        settings_page = self.client.get(reverse("accounts:settings"))
        self.assertContains(settings_page, "Team Management &amp; Permissions")
        self.assertContains(settings_page, reverse("teams:roles"))


class ModulePermissionTests(PermissionTestBase):
    def test_disabled_module_hidden_and_blocked(self):
        perms = self.without(self.writer_defaults(), "campaigns.view")
        self.set_member_perms(self.writer, "team_member", perms)
        self.client.force_login(self.writer)
        nav = [i["key"] for i in self.client.get(reverse("dashboard:home")).context["nav_items"]]
        self.assertNotIn("campaigns", nav)
        self.assertIn("content", nav)
        self.assertEqual(self.client.get(reverse("campaigns:list")).status_code, 403)
        self.assertEqual(self.client.get(reverse("campaigns:detail", args=[self.campaign["id"]])).status_code, 403)

    def test_permissions_survive_new_login_and_refresh(self):
        self.set_member_perms(self.writer, "team_member", self.without(self.writer_defaults(), "published.view", "published.create", "published.edit"))
        fresh = Client()
        self.assertTrue(fresh.login(email="writer@example.com", password=PASSWORD))
        for _ in range(2):  # refresh
            self.assertEqual(fresh.get(reverse("publications:list")).status_code, 403)
        self.assertNotIn("published", [i["key"] for i in fresh.get(reverse("dashboard:home")).context["nav_items"]])

    def test_revoking_access_from_signed_in_user_applies_next_request(self):
        self.client.force_login(self.reviewer)
        self.assertEqual(self.client.get(reverse("approvals:list")).status_code, 200)
        reviewer_client = self.client
        admin_client = Client()
        admin_client.force_login(self.admin)
        admin_client.post(reverse("teams:permissions", args=[self.reviewer.pk]),
                          {"role": "reviewer", "perm": self.without(sorted(ROLE_CEILINGS["reviewer"]), "approvals.view")})
        self.assertEqual(reviewer_client.get(reverse("approvals:list")).status_code, 403)

    def test_role_defaults_apply_to_members_without_overrides(self):
        self.client.force_login(self.admin)
        self.client.post(reverse("teams:roles"), {"role": "client", "perm": self.without(sorted(ROLE_CEILINGS["client"]), "published.view")})
        self.assertNotIn("published.view", self.principal(self.client_user).permissions)
        self.client.force_login(self.client_user)
        self.assertEqual(self.client.get(reverse("publications:list")).status_code, 403)
        self.client.force_login(self.admin)
        self.client.post(reverse("teams:roles"), {"role": "client", "reset": "1"})
        self.assertIsNone(role_permissions.for_role("client"))
        self.assertIn("published.view", self.principal(self.client_user).permissions)

    def test_member_override_beats_role_default(self):
        self.client.force_login(self.admin)
        self.client.post(reverse("teams:roles"), {"role": "client", "perm": self.without(sorted(ROLE_CEILINGS["client"]), "published.view")})
        self.set_member_perms(self.client_user, "client", sorted(ROLE_CEILINGS["client"]))
        self.assertEqual(team_profiles.for_user(self.client_user.pk)["permission_overrides"]["grants"], ["published.view"])
        self.assertIn("published.view", self.principal(self.client_user).permissions)
        self.assertNotIn("published.view", self.principal(self.other_client).permissions)
        self.client.force_login(self.admin)
        self.client.post(reverse("teams:permissions", args=[self.client_user.pk]), {"reset": "1"})
        self.assertNotIn("published.view", self.principal(self.client_user).permissions)


class ActionPermissionTests(PermissionTestBase):
    def test_reviewer_without_approve_can_still_request_changes(self):
        item = self.make_content(self.writer, self.campaign)
        self.submit(self.writer, item)
        self.set_member_perms(self.reviewer, "reviewer", self.without(sorted(ROLE_CEILINGS["reviewer"]), "approvals.approve"))
        self.client.force_login(self.reviewer)
        page = self.client.get(reverse("approvals:review", args=[item["id"]]))
        self.assertNotContains(page, 'value="approve"')
        self.assertContains(page, 'value="request_changes"')
        response = self.client.post(reverse("approvals:review", args=[item["id"]]), {"action": "approve", "version_number": 1})
        self.assertEqual(response.status_code, 403)  # direct POST still refused
        self.assertEqual(contents.get(item["id"])["status"], "pending")
        self.client.post(reverse("approvals:review", args=[item["id"]]), {"action": "request_changes", "feedback": "Fix the intro.", "version_number": 1})
        self.assertEqual(contents.get(item["id"])["status"], "needs_edit")

    def test_writer_without_create_or_publish(self):
        self.set_member_perms(self.writer, "team_member", self.without(self.writer_defaults(), "content.create", "published.create"))
        self.client.force_login(self.writer)
        self.assertNotContains(self.client.get(reverse("content:list")), 'href="/content/new/"')
        self.assertEqual(self.client.get(reverse("content:create")).status_code, 403)
        response = self.client.post(reverse("content:create"), {"brand_id": self.brand["id"], "campaign_id": self.campaign["id"],
                                                                 "title": "Blocked", "keyword": "k", "content_type": "blog", "action": "save"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(contents.count({"title": "Blocked"}), 0)
        self.assertEqual(self.client.get(reverse("publications:create")).status_code, 403)

    def test_writer_without_edit_cannot_edit_drafts(self):
        item = self.make_content(self.writer, self.campaign)
        self.set_member_perms(self.writer, "team_member", self.without(self.writer_defaults(), "content.edit"))
        self.client.force_login(self.writer)
        self.assertRedirects(self.client.get(reverse("content:edit", args=[item["id"]])), reverse("content:detail", args=[item["id"]]))

    def test_cannot_grant_beyond_role_ceiling(self):
        response = self.set_member_perms(self.client_user, "client", sorted(ROLE_CEILINGS["client"]) + ["team.manage", "content.create"])
        self.assertContains(response, "not available for the Client role")
        self.assertIsNone(team_profiles.for_user(self.client_user.pk).get("permission_overrides"))
        self.assertNotIn("team.manage", self.principal(self.client_user).permissions)
        self.client.force_login(self.client_user)
        self.assertEqual(self.client.get(reverse("teams:list")).status_code, 403)

    def test_unknown_permission_rejected(self):
        self.set_member_perms(self.writer, "team_member", self.writer_defaults() + ["content.delete_everything"])
        self.assertIsNone(team_profiles.for_user(self.writer.pk).get("permission_overrides"))


class SecurityAndWorkflowTests(PermissionTestBase):
    def test_non_admin_cannot_change_permissions(self):
        for user in (self.writer, self.reviewer, self.client_user):
            self.client.force_login(user)
            targets = [(reverse("teams:permissions", args=[user.pk]), {"role": "admin", "perm": sorted(ALL_PERMISSIONS)}),
                       (reverse("teams:roles"), {"role": "client", "perm": sorted(ROLE_CEILINGS["client"])}),
                       (reverse("teams:remove_access", args=[self.admin.pk]), {})]
            for url, data in targets:
                self.assertEqual(self.client.post(url, data).status_code, 403, (user.email, url))
        self.assertEqual(team_profiles.for_user(self.writer.pk)["role"], "team_member")
        self.assertIsNone(role_permissions.for_role("client"))

    def test_admin_cannot_change_own_permissions(self):
        self.set_member_perms(self.admin, "client", [])
        self.assertEqual(team_profiles.for_user(self.admin.pk)["role"], "admin")

    def test_last_admin_protection(self):
        from core.exceptions import ServiceError
        from teams import services

        second = self.make_user("admin2@example.com", "admin")
        # With two active admins, one can be demoted through the editor.
        self.set_member_perms(second, "team_member", self.writer_defaults())
        self.assertEqual(team_profiles.for_user(second.pk)["role"], "team_member")
        # Now self.admin is the last active admin: every path that would remove them is refused.
        acting = self.principal(self.admin)
        last = team_profiles.for_user(self.admin.pk)
        for new_role, new_status in (("client", "active"), ("admin", "inactive")):
            with self.assertRaises(ServiceError):
                services._protect_last_admin(self.admin, last, new_role, new_status)
        team_profiles.update_for_user(second.pk, {"role": "admin"})
        stranger = self.principal(second)
        for call in (lambda: services.set_member_status(stranger, self.admin.pk, "inactive"),
                     lambda: services.remove_access(stranger, self.admin.pk)):
            team_profiles.update_for_user(second.pk, {"status": "inactive"})  # second is not an active admin...
            stranger = self.principal(second)
            with self.assertRaises(Exception):                               # ...so it can't act at all
                call()
        self.assertEqual(team_profiles.for_user(self.admin.pk)["status"], "active")
        self.assertTrue(acting.is_admin)

    def test_remove_access_keeps_history(self):
        item = self.make_content(self.writer, self.campaign)
        self.submit(self.writer, item)
        self.approve(self.reviewer, item)
        self.client.force_login(self.admin)
        self.client.post(reverse("teams:remove_access", args=[self.reviewer.pk]))
        profile = team_profiles.for_user(self.reviewer.pk)
        self.assertEqual(profile["status"], "inactive")
        self.assertEqual(brand_access.brand_ids_for_user(self.reviewer.pk), [])
        self.assertTrue(User.objects.filter(pk=self.reviewer.pk).exists())
        from approvals.repositories import approvals

        self.assertEqual(approvals.count({"reviewer_user_id": self.reviewer.pk}), 1)
        reviewer_client = Client()
        self.assertFalse(reviewer_client.login(email="reviewer@example.com", password=PASSWORD))

    def test_deactivated_member_denied(self):
        self.client.force_login(self.admin)
        self.client.post(reverse("teams:set_status", args=[self.writer.pk]), {"status": "inactive"})
        self.client.force_login(self.writer)
        self.assertNotEqual(self.client.get(reverse("content:list")).status_code, 200)
        self.assertEqual(self.principal(self.writer).permissions, set())

    def test_permission_changes_are_audited(self):
        self.set_member_perms(self.writer, "team_member", self.without(self.writer_defaults(), "published.edit"))
        entry = audit_logs.find_one({"action": "member.permissions_updated", "entity_id": self.writer.pk})
        self.assertEqual(entry["actor_user_id"], self.admin.pk)
        self.assertEqual(entry["metadata"]["revoked"], ["published.edit"])
        self.client.post(reverse("teams:roles"), {"role": "reviewer", "perm": self.without(sorted(ROLE_CEILINGS["reviewer"]), "drafts.view")})
        self.assertIsNotNone(audit_logs.find_one({"action": "role.permissions_updated", "entity_id": "reviewer"}))
        self.client.post(reverse("teams:remove_access", args=[self.client_user.pk]))
        self.assertIsNotNone(audit_logs.find_one({"action": "member.access_removed", "entity_id": self.client_user.pk}))
        history = self.client.get(reverse("teams:detail", args=[self.admin.pk]))
        self.assertContains(history, "Changed a member&#x27;s role or permissions")

    def test_module_access_does_not_bypass_brand_isolation_or_self_review(self):
        other = self.make_content(self.writer2, self.other_campaign, title="Globex Item")
        self.submit(self.writer2, other)
        self.set_member_perms(self.client_user, "client", sorted(ROLE_CEILINGS["client"]))
        self.client.force_login(self.client_user)
        self.assertEqual(self.client.get(reverse("approvals:review", args=[other["id"]])).status_code, 404)
        own = self.make_content(self.writer, self.campaign)
        self.submit(self.writer, own)
        contents.col.update_one({"_id": own["_id"]}, {"$set": {"writer_user_id": self.reviewer.pk}})
        self.assertFalse(can_review(self.principal(self.reviewer), contents.get(own["id"])))

    def test_existing_publication_workflow_still_works(self):
        item = self.make_content(self.writer, self.campaign)
        self.submit(self.writer, item)
        self.approve(self.reviewer, item)
        self.client.force_login(self.writer)
        self.client.post(reverse("publications:create"), {"content_id": item["id"], "published_url": "https://x.example/p",
                                                           "published_by_user_id": self.writer.pk, "published_date": "2026-10-20"})
        self.assertEqual(publications.count({"content_id": item["_id"]}), 1)
