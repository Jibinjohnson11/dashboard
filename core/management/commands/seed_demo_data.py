"""Development-only demo data, created through the real services so every relationship is valid."""
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from approvals import services as approval_services
from brands import services as brand_services
from campaigns import services as campaign_services
from content import services as content_services
from core.mongo import COLLECTIONS, ensure_indexes, get_db
from core.permissions import ACTIVE, ADMIN, CLIENT, REVIEWER, TEAM_MEMBER, Principal
from publications import services as publication_services
from teams.repositories import brand_access, team_profiles

User = get_user_model()

BRANDS = [
    {"name": "Northwind Outdoor", "website": "https://northwind-outdoor.example", "industry": "Retail", "contact_name": "Maya Ortiz", "contact_email": "maya@northwind-outdoor.example"},
    {"name": "Brightpath Dental", "website": "https://brightpath-dental.example", "industry": "Healthcare", "contact_name": "Sam Lee", "contact_email": "sam@brightpath-dental.example"},
    {"name": "Lumen Analytics", "website": "https://lumen-analytics.example", "industry": "SaaS", "contact_name": "Priya Nair", "contact_email": "priya@lumen-analytics.example"},
]
CAMPAIGNS = [
    (0, "Spring Hiking Gear", "best hiking boots", ["waterproof hiking boots", "trail shoes"], "https://northwind-outdoor.example/boots"),
    (0, "Camping Essentials", "camping checklist", ["family camping gear"], "https://northwind-outdoor.example/camping"),
    (1, "Local Dentist SEO", "dentist near me", ["teeth whitening", "emergency dentist"], "https://brightpath-dental.example/"),
    (2, "Analytics Thought Leadership", "product analytics guide", ["saas metrics", "cohort analysis"], "https://lumen-analytics.example/guides"),
    (2, "Integration Directory Listings", "analytics integrations", ["data warehouse connectors"], "https://lumen-analytics.example/integrations"),
]
MEMBERS = [
    ("Alex Writer", "alex.writer@demo.example", TEAM_MEMBER),
    ("Jordan Writer", "jordan.writer@demo.example", TEAM_MEMBER),
    ("Riley Reviewer", "riley.reviewer@demo.example", REVIEWER),
    ("Casey Client", "casey.client@demo.example", CLIENT),
]
TYPES = ["blog", "article", "guest_post", "social_bookmarking", "directory_submission"]


def body(title, keyword):
    return (
        f"<h2>{title}</h2><p>This is demo content targeting <strong>{keyword}</strong>. "
        "It explains the topic clearly, answers common questions and links to useful resources.</p>"
        "<ul><li>Key point one</li><li>Key point two</li><li>Key point three</li></ul>"
        '<p>Learn more at <a href="https://example.com">example.com</a>.</p>'
    )


class Command(BaseCommand):
    help = "Seed demo brands, campaigns, users and content (development only)."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="Delete existing MongoDB business data and demo users first.")
        parser.add_argument("--password", help="Use this password for all demo users instead of random ones.")

    def handle(self, *args, **options):
        if settings.IS_PRODUCTION or not settings.DEBUG:
            raise CommandError("seed_demo_data only runs with DJANGO_ENV=development and DJANGO_DEBUG=true.")
        db = get_db()
        if options["reset"]:
            for name in COLLECTIONS:
                db[name].delete_many({})
            User.objects.filter(email__endswith="@demo.example").delete()
        elif db.brands.count_documents({}):
            raise CommandError("MongoDB already contains brands. Re-run with --reset to replace all data with demo data.")
        ensure_indexes()

        passwords = {}

        def make_user(name, email, role):
            password = options["password"] or secrets.token_urlsafe(12)
            user = User.objects.create_user(email=email, password=password, full_name=name, is_active=True, is_superuser=role == ADMIN, is_staff=role == ADMIN)
            team_profiles.insert({"django_user_id": user.pk, "display_name": name, "email": email, "role": role, "status": ACTIVE})
            passwords[email] = password
            return Principal(user, team_profiles.for_user(user.pk))

        admin = make_user("Dana Admin", "admin@demo.example", ADMIN)
        alex, jordan, riley, casey = [make_user(*m) for m in MEMBERS]

        brands = [brand_services.create_brand(admin, dict(b, contact_phone="", description=f"Demo brand: {b['name']}.", status="active")) for b in BRANDS]
        campaigns = []
        for i, (brand_index, name, keyword, secondary, url) in enumerate(CAMPAIGNS):
            writers = [alex.user_id] if i % 2 == 0 else [jordan.user_id]
            if i == 3:
                writers.append(alex.user_id)
            campaigns.append(campaign_services.create_campaign(admin, {
                "brand_id": brands[brand_index]["id"],
                "name": name,
                "primary_keyword": keyword,
                "secondary_keywords": secondary,
                "target_url": url,
                "target_location": "United States",
                "description": f"Demo campaign for {brands[brand_index]['name']}.",
                "start_date": timezone.localdate() - timedelta(days=45),
                "end_date": None,
                "assigned_user_ids": writers,
                "status": "active",
            }))
        # Reviewer sees every brand; the client only sees Lumen Analytics.
        brand_access.replace_for_user(riley.user_id, {b["_id"] for b in brands}, REVIEWER)
        brand_access.replace_for_user(casey.user_id, {brands[2]["_id"]}, CLIENT)
        riley, casey = (Principal(p.user, p.profile) for p in (riley, casey))  # refresh cached scopes

        def writer_for(campaign):
            uid = campaign["assigned_user_ids"][0]
            return Principal(User.objects.get(pk=uid), team_profiles.for_user(uid))

        def new_content(n, campaign):
            writer = writer_for(campaign)
            title = f"{campaign['primary_keyword'].title()}: Guide #{n}"
            item = content_services.create_content(writer, {
                "brand_id": str(campaign["brand_id"]),
                "campaign_id": campaign["id"],
                "title": title,
                "keyword": campaign["primary_keyword"],
                "content_type": TYPES[n % len(TYPES)],
                "body_html": body(title, campaign["primary_keyword"]),
                "internal_notes": "Seeded demo content.",
            })
            return writer, item

        def submit(writer, item):
            return content_services.submit_content(writer, item["_id"], expected_seq=item["edit_seq"])

        def approve(item, reviewer=None):
            reviewer = reviewer or (casey if item["brand_id"] == brands[2]["_id"] else riley)
            approval_services.decide(reviewer, item["_id"], action="approve", feedback="Looks great.", version_number=item["current_version"])

        n = 0
        counts = {"draft": 10, "pending": 5, "approved": 5, "published": 5}
        for state, total in counts.items():
            for _ in range(total):
                campaign = campaigns[n % len(campaigns)]
                n += 1
                writer, item = new_content(n, campaign)
                if state == "draft":
                    continue
                item = submit(writer, item)
                if state == "pending":
                    continue
                approve(item)
                if state == "published":
                    publication_services.publish(
                        writer,
                        content_id=item["id"],
                        published_url=f"https://publisher-{n}.example/articles/{item['id']}",
                        published_by_user_id=writer.user_id,
                        published_date=timezone.localdate() - timedelta(days=n % 14),
                        notes="Seeded publication.",
                    )

        self.stdout.write(self.style.SUCCESS("Demo data created: 3 brands, 5 campaigns, 5 users, 25 content items."))
        self.stdout.write("Demo accounts (shown once; development only):")
        for email, password in passwords.items():
            self.stdout.write(f"  {email:32} {password}")
