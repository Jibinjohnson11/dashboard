from django.contrib.auth import get_user_model
from django.urls import reverse

from campaigns.repositories import campaigns
from core.testing import WorkflowFixture
from teams.repositories import brand_access, team_profiles

User = get_user_model()


class TeamTests(WorkflowFixture):
    def test_invite_with_assignments(self):
        self.client.force_login(self.admin)
        self.client.post(reverse("teams:create"), {
            "full_name": "Nina New", "email": "nina@example.com", "role": "team_member", "status": "active",
            "brand_ids": [self.brand["id"]], "campaign_ids": [self.campaign["id"]]})
        user = User.objects.get(email="nina@example.com")
        self.assertIn(self.brand["_id"], brand_access.brand_ids_for_user(user.pk))
        self.assertIn(user.pk, campaigns.get(self.campaign["id"])["assigned_user_ids"])

    def test_client_requires_brand(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("teams:create"), {"full_name": "C Lient", "email": "c2@example.com", "role": "client", "status": "active"})
        self.assertIn("brand_ids", response.context["form"].errors)

    def test_duplicate_email(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("teams:create"), {"full_name": "Dup", "email": "Writer@example.com", "role": "team_member", "status": "active"})
        self.assertContains(response, "already exists")

    def test_remove_assignment_and_change_role(self):
        self.client.force_login(self.admin)
        self.client.post(reverse("teams:edit", args=[self.writer.pk]), {
            "full_name": "Wendy Writer", "email": "writer@example.com", "role": "reviewer", "status": "active", "brand_ids": [self.brand["id"]]})
        self.assertEqual(team_profiles.for_user(self.writer.pk)["role"], "reviewer")
        self.assertNotIn(self.writer.pk, campaigns.get(self.campaign["id"])["assigned_user_ids"])

    def test_deactivate_preserves_history(self):
        item = self.make_content(self.writer, self.campaign)
        self.client.force_login(self.admin)
        self.client.post(reverse("teams:set_status", args=[self.writer.pk]), {"status": "inactive"})
        self.writer.refresh_from_db()
        self.assertFalse(self.writer.is_active)
        self.assertEqual(team_profiles.for_user(self.writer.pk)["status"], "inactive")
        from content.repositories import contents

        self.assertEqual(contents.get(item["id"])["writer_user_id"], self.writer.pk)
        detail = self.client.get(reverse("content:detail", args=[item["id"]]))
        self.assertContains(detail, "Wendy Writer")

    def test_profile_shows_counts_and_history(self):
        item = self.make_content(self.writer, self.campaign)
        self.submit(self.writer, item)
        self.client.force_login(self.admin)
        response = self.client.get(reverse("teams:detail", args=[self.writer.pk]))
        self.assertEqual(response.context["stats"]["pending"], 1)
        self.assertContains(response, "Submitted content for approval")

    def test_orphaned_auth_user_appears_as_pending(self):
        orphan = User.objects.create_user(email="orphan@example.com", password="x", full_name="Orphan User", is_active=True)
        self.client.force_login(self.admin)
        response = self.client.get(reverse("teams:list"), {"status": "pending"})
        self.assertContains(response, "Orphan User")
        self.assertEqual(team_profiles.for_user(orphan.pk)["status"], "pending")
        # The orphan still has no access until an admin activates it.
        self.client.force_login(orphan)
        self.assertRedirects(self.client.get(reverse("dashboard:home")), reverse("accounts:pending"))
