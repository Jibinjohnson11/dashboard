from django.urls import reverse

from brands.repositories import brands
from core.testing import WorkflowFixture


class BrandCrudTests(WorkflowFixture):
    def test_create_read_update_archive(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("brands:create"), {
            "name": "  Initech  ", "website": "initech.example", "industry": "Software", "contact_email": "Boss@Initech.example",
            "contact_phone": "+1 555 0100", "status": "active"})
        brand = brands.find_one({"name": "Initech"})
        self.assertRedirects(response, reverse("brands:detail", args=[brand["id"]]))
        self.assertEqual(brand["website"], "https://initech.example")
        self.assertEqual(brand["contact_email"], "boss@initech.example")

        detail = self.client.get(reverse("brands:detail", args=[brand["id"]]))
        self.assertContains(detail, "Initech")

        self.client.post(reverse("brands:edit", args=[brand["id"]]), {"name": "Initech Corp", "status": "inactive"})
        updated = brands.get(brand["id"])
        self.assertEqual((updated["name"], updated["status"]), ("Initech Corp", "inactive"))

        self.client.post(reverse("brands:archive", args=[brand["id"]]))
        archived = brands.get(brand["id"])
        self.assertEqual(archived["status"], "archived")
        self.assertIsNotNone(archived["archived_at"])
        self.assertNotContains(self.client.get(reverse("brands:list")), "Initech Corp")
        self.assertContains(self.client.get(reverse("brands:list"), {"status": "archived"}), "Initech Corp")

        self.client.post(reverse("brands:restore", args=[brand["id"]]))
        self.assertEqual(brands.get(brand["id"])["status"], "inactive")

    def test_archive_keeps_campaigns_and_content(self):
        item = self.make_content(self.writer, self.campaign)
        self.client.force_login(self.admin)
        self.client.post(reverse("brands:archive", args=[self.brand["id"]]))
        from campaigns.repositories import campaigns
        from content.repositories import contents

        self.assertIsNotNone(campaigns.get(self.campaign["id"]))
        self.assertIsNotNone(contents.get(item["id"]))

    def test_duplicate_name_rejected(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("brands:create"), {"name": "acme", "status": "active"})
        self.assertContains(response, "already exists")
        self.assertEqual(brands.count({"name_lower": "acme"}), 1)

    def test_validation(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("brands:create"), {"name": "", "website": "javascript:alert(1)", "contact_email": "nope", "status": "deleted"})
        form = response.context["form"]
        self.assertEqual(set(form.errors), {"name", "website", "contact_email", "status"})

    def test_list_search_and_pagination(self):
        for i in range(20):
            self.make_brand(self.admin, f"Brand {i:02d}")
        self.client.force_login(self.admin)
        page1 = self.client.get(reverse("brands:list"))
        self.assertEqual(page1.context["page"].total, 22)
        self.assertEqual(len(page1.context["page"].items), 15)
        page2 = self.client.get(reverse("brands:list"), {"page": 2})
        self.assertEqual(len(page2.context["page"].items), 7)
        found = self.client.get(reverse("brands:list"), {"q": "brand 1"})
        self.assertEqual(found.context["page"].total, 10)

    def test_brand_detail_shows_related_data(self):
        item = self.make_content(self.writer, self.campaign)
        self.submit(self.writer, item)
        self.client.force_login(self.admin)
        response = self.client.get(reverse("brands:detail", args=[self.brand["id"]]))
        self.assertEqual(response.context["content_total"], 1)
        self.assertEqual(response.context["pending_total"], 1)
        self.assertContains(response, "Acme Launch")
        self.assertContains(response, "Wendy Writer")
