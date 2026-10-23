import shutil
import tempfile
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse

from brands.repositories import brands
from core.testing import WorkflowFixture

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


class BrandLogoTests(WorkflowFixture):
    def setUp(self):
        super().setUp()
        self.media = tempfile.mkdtemp()
        self.override = override_settings(MEDIA_ROOT=self.media)
        self.override.enable()

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.media, ignore_errors=True)
        super().tearDown()

    def upload(self, name="logo.png", data=PNG, content_type="image/png"):
        return SimpleUploadedFile(name, data, content_type=content_type)

    def files(self):
        return sorted(p.name for p in Path(self.media).rglob("*") if p.is_file())

    def test_create_brand_with_logo_and_serve_it(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("brands:create"), {"name": "Logo Co", "status": "active", "logo": self.upload()})
        brand = brands.find_one({"name": "Logo Co"})
        self.assertRedirects(response, reverse("brands:detail", args=[brand["id"]]))
        self.assertEqual(brand["logo_content_type"], "image/png")
        self.assertTrue(brand["logo_path"].startswith("brand_logos/") and brand["logo_path"].endswith(".png"))
        self.assertNotIn("logo.png", brand["logo_path"])  # random name, not the user's filename
        served = self.client.get(reverse("brands:logo", args=[brand["id"]]))
        self.assertEqual(served.status_code, 200)
        self.assertEqual(served["Content-Type"], "image/png")
        self.assertEqual(served["X-Content-Type-Options"], "nosniff")
        self.assertEqual(b"".join(served.streaming_content), PNG)
        self.assertContains(self.client.get(reverse("brands:list")), reverse("brands:logo", args=[brand["id"]]))

    def test_replace_and_remove_logo_cleans_up_files(self):
        self.client.force_login(self.admin)
        self.client.post(reverse("brands:edit", args=[self.brand["id"]]), {"name": "Acme", "status": "active", "logo": self.upload()})
        first = brands.get(self.brand["id"])["logo_path"]
        self.client.post(reverse("brands:edit", args=[self.brand["id"]]),
                         {"name": "Acme", "status": "active", "logo": self.upload("new.jpg", JPEG, "image/jpeg")})
        second = brands.get(self.brand["id"])
        self.assertEqual(second["logo_content_type"], "image/jpeg")
        self.assertEqual(self.files(), [Path(second["logo_path"]).name])  # old file deleted
        self.client.post(reverse("brands:edit", args=[self.brand["id"]]), {"name": "Acme", "status": "active", "remove_logo": "on"})
        self.assertIsNone(brands.get(self.brand["id"])["logo_path"])
        self.assertEqual(self.files(), [])
        self.assertEqual(self.client.get(reverse("brands:logo", args=[self.brand["id"]])).status_code, 404)

    def test_editing_other_fields_keeps_logo(self):
        self.client.force_login(self.admin)
        self.client.post(reverse("brands:edit", args=[self.brand["id"]]), {"name": "Acme", "status": "active", "logo": self.upload()})
        path = brands.get(self.brand["id"])["logo_path"]
        self.client.post(reverse("brands:edit", args=[self.brand["id"]]), {"name": "Acme Corp", "status": "active"})
        self.assertEqual(brands.get(self.brand["id"])["logo_path"], path)

    def test_rejects_non_images_svg_and_large_files(self):
        self.client.force_login(self.admin)
        bad = [
            self.upload("evil.svg", b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>', "image/svg+xml"),
            self.upload("fake.png", b"<html><script>alert(1)</script></html>", "image/png"),  # lies about its type
            self.upload("empty.png", b"", "image/png"),
        ]
        for upload in bad:
            response = self.client.post(reverse("brands:create"), {"name": f"Bad {upload.name}", "status": "active", "logo": upload})
            self.assertIn("logo", response.context["form"].errors, upload.name)
        with override_settings(BRAND_LOGO_MAX_BYTES=32):
            response = self.client.post(reverse("brands:create"), {"name": "Big", "status": "active", "logo": self.upload()})
            self.assertIn("MB or smaller", str(response.context["form"].errors["logo"]))
        self.assertEqual(self.files(), [])
        self.assertEqual(brands.count({"name": {"$regex": "^(Bad|Big)"}}), 0)

    def test_logo_respects_brand_access(self):
        self.client.force_login(self.admin)
        self.client.post(reverse("brands:edit", args=[self.other_brand["id"]]), {"name": "Globex", "status": "active", "logo": self.upload()})
        self.client.force_login(self.client_user)  # only assigned to Acme
        self.assertEqual(self.client.get(reverse("brands:logo", args=[self.other_brand["id"]])).status_code, 404)
        self.client.force_login(self.other_client)
        self.assertEqual(self.client.get(reverse("brands:logo", args=[self.other_brand["id"]])).status_code, 200)
        self.client.logout()
        self.assertEqual(self.client.get(reverse("brands:logo", args=[self.other_brand["id"]])).status_code, 302)

    def test_non_admin_cannot_upload(self):
        self.client.force_login(self.writer)
        response = self.client.post(reverse("brands:edit", args=[self.brand["id"]]), {"name": "Acme", "status": "active", "logo": self.upload()})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.files(), [])
