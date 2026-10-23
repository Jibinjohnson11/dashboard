from django.urls import reverse

from campaigns.repositories import campaign_assignments, campaigns
from core.testing import WorkflowFixture


class CampaignTests(WorkflowFixture):
    def form_data(self, **extra):
        data = {"name": "Summer Push", "brand_id": self.brand["id"], "primary_keyword": "summer shoes",
                "secondary_keywords": "sandals, Sandals\nflip flops", "target_url": "https://acme.example/summer",
                "start_date": "2026-06-01", "end_date": "2026-08-31", "status": "active", "assigned_user_ids": [self.writer.pk]}
        data.update(extra)
        return data

    def test_create_with_brand_and_team(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("campaigns:create"), self.form_data())
        campaign = campaigns.find_one({"name": "Summer Push"})
        self.assertRedirects(response, reverse("campaigns:detail", args=[campaign["id"]]))
        self.assertEqual(campaign["brand_id"], self.brand["_id"])
        self.assertEqual(campaign["secondary_keywords"], ["sandals", "flip flops"])
        self.assertEqual(campaign["assigned_user_ids"], [self.writer.pk])
        self.assertEqual(campaign_assignments.user_ids_for(campaign["_id"]), [self.writer.pk])

    def test_update_and_reassign(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("campaigns:edit", args=[self.campaign["id"]]), self.form_data(
            name="Acme Launch v2", brand_id=self.other_brand["id"], assigned_user_ids=[self.writer2.pk], status="paused"))
        self.assertRedirects(response, reverse("campaigns:detail", args=[self.campaign["id"]]))
        updated = campaigns.get(self.campaign["id"])
        self.assertEqual(updated["name"], "Acme Launch v2")
        self.assertEqual(updated["brand_id"], self.brand["_id"])  # brand is locked
        self.assertEqual(updated["assigned_user_ids"], [self.writer2.pk])
        self.assertEqual(campaign_assignments.user_ids_for(self.campaign["_id"]), [self.writer2.pk])
        self.assertEqual(updated["status"], "paused")

    def test_invalid_brand_reference_rejected(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("campaigns:create"), self.form_data(brand_id="0123456789abcdef01234567"))
        self.assertIn("brand_id", response.context["form"].errors)
        self.assertIsNone(campaigns.find_one({"name": "Summer Push"}))

    def test_archived_brand_rejected(self):
        from brands.services import archive_brand

        archive_brand(self.principal(self.admin), self.brand["id"])
        self.client.force_login(self.admin)
        response = self.client.post(reverse("campaigns:create"), self.form_data())
        self.assertIn("brand_id", response.context["form"].errors)

    def test_only_active_team_members_can_be_assigned(self):
        from campaigns.services import create_campaign
        from core.exceptions import ServiceError

        for bad_user in (self.client_user, self.reviewer):
            with self.assertRaises(ServiceError):
                create_campaign(self.principal(self.admin), dict(self.form_data(assigned_user_ids=[bad_user.pk]), start_date=None, end_date=None,
                                                                    secondary_keywords=[], name=f"x{bad_user.pk}"))

    def test_end_date_before_start_rejected(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("campaigns:create"), self.form_data(end_date="2026-01-01"))
        self.assertIn("end_date", response.context["form"].errors)

    def test_filters(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("campaigns:list"), {"brand": self.other_brand["id"]})
        names = [c["name"] for c in response.context["page"].items]
        self.assertEqual(names, ["Globex Growth"])
        response = self.client.get(reverse("campaigns:list"), {"status": "paused"})
        self.assertEqual(response.context["page"].total, 0)
        response = self.client.get(reverse("campaigns:list"), {"q": "running"})
        self.assertEqual(response.context["page"].total, 2)

    def test_archive_campaign(self):
        self.client.force_login(self.admin)
        self.client.post(reverse("campaigns:archive", args=[self.campaign["id"]]))
        self.assertEqual(campaigns.get(self.campaign["id"])["status"], "archived")

    def test_detail_groups_content_and_copy_targets(self):
        item = self.make_content(self.writer, self.campaign)
        self.client.force_login(self.writer)
        response = self.client.get(reverse("campaigns:detail", args=[self.campaign["id"]]))
        self.assertEqual(len(response.context["groups"]["draft"]), 1)
        self.assertContains(response, 'data-copy="running shoes"')
        self.assertContains(response, 'data-copy="https://acme.example/shoes"')
        self.assertEqual(response.context["groups"]["draft"][0]["id"], item["id"])
