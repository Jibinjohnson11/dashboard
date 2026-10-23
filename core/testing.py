"""Shared test helpers. Every test gets a fresh in-memory MongoDB (never a real server)."""
from datetime import date

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase

from core import mongo
from core.permissions import ACTIVE, Principal
from teams.repositories import brand_access, team_profiles

User = get_user_model()
PASSWORD = "Str0ng-Test-Passw0rd!"


class MongoTestCase(TestCase):
    def setUp(self):
        super().setUp()
        assert settings.MONGODB_USE_MOCK, "Tests must never run against a real MongoDB."
        mongo.reset_client()
        self.db = mongo.get_db()
        cache.clear()

    def tearDown(self):
        mongo.reset_client()
        super().tearDown()

    # ---- users -------------------------------------------------------------

    def make_user(self, email, role, *, status=ACTIVE, name=None, password=PASSWORD, brands=()):
        user = User.objects.create_user(
            email=email, password=password, full_name=name or email.split("@")[0].title(), is_active=status in ("active", "invited")
        )
        team_profiles.insert({"django_user_id": user.pk, "display_name": user.full_name, "email": user.email, "role": role, "status": status})
        if brands:
            brand_access.replace_for_user(user.pk, {b["_id"] for b in brands}, role)
        return user

    def principal(self, user):
        user.refresh_from_db()
        return Principal(user, team_profiles.for_user(user.pk))

    def login(self, user):
        self.client.force_login(user)

    # ---- business data -----------------------------------------------------

    def make_brand(self, admin, name="Acme", **extra):
        from brands.services import create_brand

        data = {"name": name, "website": "https://acme.example", "industry": "Retail", "contact_name": "", "contact_email": "",
                "contact_phone": "", "description": "", "status": "active"}
        data.update(extra)
        return create_brand(self.principal(admin), data)

    def make_campaign(self, admin, brand, name="Launch", members=(), **extra):
        from campaigns.services import create_campaign

        data = {"brand_id": brand["id"], "name": name, "primary_keyword": "running shoes", "secondary_keywords": ["trail"],
                "target_url": "https://acme.example/shoes", "target_location": "", "description": "",
                "start_date": date(2026, 1, 1), "end_date": None, "assigned_user_ids": [u.pk for u in members], "status": "active"}
        data.update(extra)
        return create_campaign(self.principal(admin), data)

    def make_content(self, writer, campaign, title="Best Running Shoes", body="<p>Great shoes for every runner.</p>"):
        from content.services import create_content

        return create_content(self.principal(writer), {
            "brand_id": str(campaign["brand_id"]),
            "campaign_id": campaign["id"],
            "title": title,
            "keyword": "running shoes",
            "content_type": "blog",
            "body_html": body,
            "internal_notes": "",
        })

    def submit(self, writer, item):
        from content.services import get_content, submit_content

        fresh = get_content(self.principal(writer), item["_id"])
        return submit_content(self.principal(writer), item["_id"], expected_seq=fresh["edit_seq"])

    def approve(self, reviewer, item, action="approve", feedback="Looks good overall."):
        from approvals.services import decide
        from content.repositories import contents

        fresh = contents.get(item["_id"])
        return decide(self.principal(reviewer), item["_id"], action=action, feedback=feedback, version_number=fresh["current_version"])


class WorkflowFixture(MongoTestCase):
    """Admin, two writers, a reviewer and two clients across two brands."""

    def setUp(self):
        super().setUp()
        self.admin = self.make_user("admin@example.com", "admin", name="Ada Admin")
        self.brand = self.make_brand(self.admin, "Acme")
        self.other_brand = self.make_brand(self.admin, "Globex")
        self.writer = self.make_user("writer@example.com", "team_member", name="Wendy Writer")
        self.writer2 = self.make_user("writer2@example.com", "team_member", name="Walt Writer")
        self.reviewer = self.make_user("reviewer@example.com", "reviewer", brands=[self.brand], name="Rita Reviewer")
        self.client_user = self.make_user("client@example.com", "client", brands=[self.brand], name="Cleo Client")
        self.other_client = self.make_user("other-client@example.com", "client", brands=[self.other_brand], name="Otto Client")
        self.campaign = self.make_campaign(self.admin, self.brand, "Acme Launch", members=[self.writer])
        self.other_campaign = self.make_campaign(self.admin, self.other_brand, "Globex Growth", members=[self.writer2])
