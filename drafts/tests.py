"""Content Drafts: filtered view of reviewed content (approved / rejected / needs_edit)."""
from django.urls import reverse

from approvals.repositories import approvals
from content.repositories import contents
from core.testing import WorkflowFixture
from publications.repositories import publications


class ContentDraftsTests(WorkflowFixture):
    def setUp(self):
        super().setUp()
        self.approved = self.reviewed("Approved Piece", "approve", reviewer=self.client_user)
        self.rejected = self.reviewed("Rejected Piece", "reject", feedback="Off-brand tone.")
        self.needs_edit = self.reviewed("Needs Edit Piece", "request_changes", feedback="Client wants a pricing table.",
                                        reviewer=self.client_user)
        self.pending = self.make_content(self.writer, self.campaign, title="Pending Piece")
        self.submit(self.writer, self.pending)
        self.draft = self.make_content(self.writer, self.campaign, title="Unsubmitted Piece")

    def reviewed(self, title, action, feedback="Looks good overall.", reviewer=None):
        item = self.make_content(self.writer, self.campaign, title=title)
        self.submit(self.writer, item)
        self.approve(reviewer or self.reviewer, item, action=action, feedback=feedback)
        return contents.get(item["id"])

    def titles(self, user, **params):
        self.client.force_login(user)
        response = self.client.get(reverse("drafts:list"), params)
        self.assertEqual(response.status_code, 200)
        return {c["title"] for c in response.context["page"].items}

    def test_reviewed_content_appears_automatically_and_pending_is_excluded(self):
        self.assertEqual(self.titles(self.admin), {"Approved Piece", "Rejected Piece", "Needs Edit Piece"})
        self.client.force_login(self.admin)
        response = self.client.get(reverse("drafts:list"))
        self.assertEqual(response.context["counts"], {"approved": 1, "rejected": 1, "needs_edit": 1, "total": 3})
        self.assertContains(response, "Cleo Client")   # reviewed-by column
        self.assertContains(response, "Rita Reviewer")

    def test_status_tabs(self):
        self.assertEqual(self.titles(self.admin, status="approved"), {"Approved Piece"})
        self.assertEqual(self.titles(self.admin, status="rejected"), {"Rejected Piece"})
        self.assertEqual(self.titles(self.admin, status="needs_edit"), {"Needs Edit Piece"})
        self.assertEqual(self.titles(self.admin, status="pending"), {"Approved Piece", "Rejected Piece", "Needs Edit Piece"})  # invalid -> all

    def test_search_and_filters(self):
        self.assertEqual(self.titles(self.admin, q="rejected"), {"Rejected Piece"})
        self.assertEqual(self.titles(self.admin, q="running shoes"), {"Approved Piece", "Rejected Piece", "Needs Edit Piece"})
        self.assertEqual(self.titles(self.admin, brand=self.other_brand["id"]), set())
        self.assertEqual(self.titles(self.admin, campaign=self.campaign["id"]), {"Approved Piece", "Rejected Piece", "Needs Edit Piece"})
        self.assertEqual(self.titles(self.admin, type="article"), set())
        self.assertEqual(len(self.titles(self.admin, writer=self.writer.pk)), 3)
        self.assertEqual(self.titles(self.admin, writer=self.writer2.pk), set())
        self.assertEqual(self.titles(self.admin, writer="abc"), {"Approved Piece", "Rejected Piece", "Needs Edit Piece"})
        today = self.approved["last_reviewed_at"].date()
        self.assertEqual(len(self.titles(self.admin, date_from=today.isoformat(), date_to=today.isoformat())), 3)
        self.assertEqual(self.titles(self.admin, date_to="2000-01-01"), set())

    def test_resubmitted_content_leaves_drafts_but_keeps_history(self):
        self.client.force_login(self.writer)
        fresh = contents.get(self.needs_edit["id"])
        self.client.post(reverse("content:edit", args=[fresh["id"]]), {
            "title": fresh["title"], "keyword": fresh["keyword"], "content_type": fresh["content_type"],
            "body_html": "<p>Now with a pricing table.</p>", "edit_seq": fresh["edit_seq"], "action": "submit"})
        self.assertEqual(contents.get(fresh["id"])["status"], "pending")
        self.assertNotIn("Needs Edit Piece", self.titles(self.admin))
        self.assertEqual(approvals.count({"content_id": fresh["_id"], "action": "request_changes"}), 1)
        # Direct URL now sends the reviewer to the approval page instead.
        self.client.force_login(self.reviewer)
        response = self.client.get(reverse("drafts:detail", args=[fresh["id"]]))
        self.assertRedirects(response, reverse("approvals:review", args=[fresh["id"]]))
        review = self.client.get(reverse("approvals:review", args=[fresh["id"]]))
        self.assertContains(review, "Client wants a pricing table.")

    def test_detail_shows_content_feedback_and_history(self):
        self.client.force_login(self.writer)
        response = self.client.get(reverse("drafts:detail", args=[self.needs_edit["id"]]))
        self.assertContains(response, "Great shoes for every runner.")
        self.assertContains(response, "Client wants a pricing table.")
        self.assertContains(response, "Changes requested</strong> by Cleo Client")
        self.assertTrue(response.context["can_edit"])
        self.assertContains(response, reverse("content:edit", args=[self.needs_edit["id"]]))

    def test_rejected_is_read_only_and_approved_can_be_published(self):
        self.client.force_login(self.writer)
        rejected = self.client.get(reverse("drafts:detail", args=[self.rejected["id"]]))
        self.assertFalse(rejected.context["can_edit"])
        self.assertFalse(rejected.context["can_publish"])
        self.assertContains(rejected, "read-only")
        approved = self.client.get(reverse("drafts:detail", args=[self.approved["id"]]))
        self.assertTrue(approved.context["can_publish"])
        response = self.client.post(reverse("publications:create"), {
            "content_id": self.approved["id"], "published_url": "https://news.example/a", "published_by_user_id": self.writer.pk,
            "published_date": "2026-10-20"})
        self.assertEqual(publications.count({"content_id": self.approved["_id"]}), 1, response.content[:300])
        self.assertNotIn("Approved Piece", self.titles(self.admin))  # published -> lives in Published Content

    def test_no_records_are_copied(self):
        before = {name: self.db[name].count_documents({}) for name in self.db.list_collection_names()}
        self.titles(self.admin)
        self.client.get(reverse("drafts:detail", args=[self.approved["id"]]))
        after = {name: self.db[name].count_documents({}) for name in self.db.list_collection_names()}
        self.assertEqual(before, after)

    def test_permissions_and_brand_isolation(self):
        other = self.make_content(self.writer2, self.other_campaign, title="Globex Reviewed")
        self.submit(self.writer2, other)
        self.approve(self.admin, other)
        self.assertNotIn("Globex Reviewed", self.titles(self.client_user))
        self.assertEqual(self.titles(self.client_user), {"Approved Piece", "Rejected Piece", "Needs Edit Piece"})
        self.assertEqual(self.titles(self.other_client), {"Globex Reviewed"})
        self.assertEqual(self.titles(self.writer2), {"Globex Reviewed"})
        self.client.force_login(self.client_user)
        self.assertEqual(self.client.get(reverse("drafts:detail", args=[other["id"]])).status_code, 404)
        self.assertEqual(self.client.get("/drafts/0123456789abcdef01234567/").status_code, 404)
        self.assertEqual(self.client.get("/drafts/not-an-id/").status_code, 404)
        detail = self.client.get(reverse("drafts:detail", args=[self.needs_edit["id"]]))
        self.assertFalse(detail.context["can_edit"])        # clients never edit
        self.assertFalse(detail.context["show_internal_notes"])
        self.client.logout()
        self.assertEqual(self.client.get(reverse("drafts:list")).status_code, 302)

    def test_sidebar_position(self):
        self.client.force_login(self.admin)
        nav = [item["key"] for item in self.client.get(reverse("drafts:list")).context["nav_items"]]
        self.assertEqual(nav[nav.index("approvals") + 1], "drafts")
        self.assertEqual(nav[nav.index("drafts") + 1], "published")
