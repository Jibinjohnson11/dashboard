"""Review permissions: admins may review anything (including their own work); others may not self-review."""
from django.test import Client
from django.urls import reverse

from approvals.repositories import approvals
from approvals.services import can_review, decide
from content.repositories import content_versions, contents
from core.exceptions import ServiceError, WorkflowConflict
from core.testing import WorkflowFixture
from teams.repositories import team_profiles


class ApprovalPermissionTests(WorkflowFixture):
    def pending(self, writer, campaign=None, title="Review Me"):
        item = self.make_content(writer, campaign or self.campaign, title=title)
        self.submit(writer, item)
        return contents.get(item["id"])

    def post(self, user, item, action, feedback="", version=None):
        self.client.force_login(user)
        return self.client.post(reverse("approvals:review", args=[item["id"]]), {
            "action": action, "feedback": feedback, "version_number": version or item["current_version"]})

    # --- 1-3: admin reviewing their own content over HTTP ---

    def test_admin_sees_actions_on_own_content(self):
        item = self.pending(self.admin)
        self.client.force_login(self.admin)
        response = self.client.get(reverse("approvals:review", args=[item["id"]]))
        self.assertTrue(response.context["can_review"])
        self.assertContains(response, 'value="approve"')
        self.assertContains(response, 'value="reject"')
        self.assertContains(response, 'value="request_changes"')
        self.assertNotContains(response, "someone else needs to review it")
        listing = self.client.get(reverse("approvals:list"))
        self.assertContains(listing, ">Review</a>")

    def test_admin_can_approve_own_content(self):
        item = self.pending(self.admin)
        response = self.post(self.admin, item, "approve")
        self.assertRedirects(response, reverse("approvals:list"))
        fresh = contents.get(item["id"])
        self.assertEqual((fresh["status"], fresh["approved_version"]), ("approved", 1))

    def test_admin_can_reject_own_content_with_reason(self):
        item = self.pending(self.admin)
        self.assertEqual(self.post(self.admin, item, "reject", feedback="").status_code, 200)  # feedback still required
        self.assertEqual(contents.get(item["id"])["status"], "pending")
        self.post(self.admin, item, "reject", feedback="Not on brief.")
        self.assertEqual(contents.get(item["id"])["status"], "rejected")

    def test_admin_can_request_changes_on_own_content(self):
        item = self.pending(self.admin)
        self.post(self.admin, item, "request_changes", feedback="Add an FAQ section.")
        self.assertEqual(contents.get(item["id"])["status"], "needs_edit")
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("content:edit", args=[item["id"]])), "Add an FAQ section.")
        self.assertEqual(content_versions.count({"content_id": item["_id"]}), 1)  # revision history intact

    # --- 4, 6, 7: reviewing others' content ---

    def test_admin_can_review_other_users_content(self):
        item = self.pending(self.writer)
        self.post(self.admin, item, "approve")
        self.assertEqual(contents.get(item["id"])["status"], "approved")

    def test_reviewer_can_review_authorised_content(self):
        item = self.pending(self.writer)
        self.post(self.reviewer, item, "approve")
        self.assertEqual(contents.get(item["id"])["status"], "approved")

    def test_client_review_permissions_unchanged(self):
        item = self.pending(self.writer)
        other = self.pending(self.writer2, self.other_campaign, title="Other brand")
        self.assertEqual(self.post(self.client_user, other, "approve").status_code, 404)
        self.assertEqual(contents.get(other["id"])["status"], "pending")
        self.post(self.client_user, item, "request_changes", feedback="Please adjust tone.")
        self.assertEqual(contents.get(item["id"])["status"], "needs_edit")

    # --- 5: reviewers and clients still blocked from self-review ---

    def test_reviewer_cannot_review_own_content(self):
        item = self.pending(self.writer)
        contents.col.update_one({"_id": item["_id"]}, {"$set": {"writer_user_id": self.reviewer.pk}})
        item = contents.get(item["id"])
        self.assertFalse(can_review(self.principal(self.reviewer), item))
        self.client.force_login(self.reviewer)
        page = self.client.get(reverse("approvals:review", args=[item["id"]]))
        self.assertContains(page, "someone else needs to review it")
        self.assertNotContains(page, 'value="approve"')
        self.post(self.reviewer, item, "approve")
        self.assertEqual(contents.get(item["id"])["status"], "pending")
        with self.assertRaises(ServiceError):
            decide(self.principal(self.reviewer), item["_id"], action="approve", feedback="", version_number=1)

    def test_client_cannot_review_own_content(self):
        item = self.pending(self.writer)
        content_versions.col.update_one({"content_id": item["_id"]}, {"$set": {"created_by_user_id": self.client_user.pk}})
        self.assertFalse(can_review(self.principal(self.client_user), item))
        with self.assertRaises(ServiceError):
            decide(self.principal(self.client_user), item["_id"], action="approve", feedback="", version_number=1)

    # --- 8: inactive users ---

    def test_inactive_admin_and_reviewer_cannot_review(self):
        item = self.pending(self.writer)
        for user in (self.admin, self.reviewer):
            team_profiles.update_for_user(user.pk, {"status": "inactive"})
            self.assertFalse(can_review(self.principal(user), item))
            response = self.post(user, item, "approve")
            self.assertRedirects(response, reverse("accounts:pending"))
        self.assertEqual(contents.get(item["id"])["status"], "pending")

    # --- 9: unauthorised direct POSTs ---

    def test_unauthorised_posts_rejected(self):
        item = self.pending(self.writer)
        self.assertEqual(self.post(self.writer2, item, "approve").status_code, 403)   # team member: no module access
        self.client.logout()
        anon = self.client.post(reverse("approvals:review", args=[item["id"]]), {"action": "approve", "version_number": 1})
        self.assertEqual(anon.status_code, 302)                                       # login required
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.admin)
        no_token = csrf_client.post(reverse("approvals:review", args=[item["id"]]), {"action": "approve", "version_number": 1})
        self.assertEqual(no_token.status_code, 403)                                    # CSRF still enforced
        self.assertEqual(contents.get(item["id"])["status"], "pending")
        self.assertEqual(approvals.count({"content_id": item["_id"]}), 0)

    # --- 10: non-pending content ---

    def test_non_pending_content_cannot_be_reviewed_again(self):
        item = self.pending(self.admin)
        self.post(self.admin, item, "approve")
        approved = contents.get(item["id"])
        self.assertFalse(can_review(self.principal(self.admin), approved))
        self.client.force_login(self.admin)
        self.assertNotContains(self.client.get(reverse("approvals:review", args=[item["id"]])), 'value="reject"')
        with self.assertRaises(WorkflowConflict):
            decide(self.principal(self.admin), item["_id"], action="reject", feedback="Changed my mind", version_number=1)
        draft = self.make_content(self.admin, self.campaign, title="Never submitted")
        with self.assertRaises(WorkflowConflict):
            decide(self.principal(self.admin), draft["_id"], action="approve", feedback="", version_number=1)
        self.assertEqual(contents.get(item["id"])["status"], "approved")
        self.assertEqual(approvals.count({"content_id": item["_id"]}), 1)

    # --- 11: history records the decision-maker ---

    def test_history_records_admin_as_reviewer(self):
        item = self.pending(self.admin)
        self.post(self.admin, item, "request_changes", feedback="Tighten the intro.")
        record = approvals.find_one({"content_id": item["_id"]})
        self.assertEqual(
            (record["reviewer_user_id"], record["reviewer_role"], record["action"], record["feedback"], record["version_number"]),
            (self.admin.pk, "admin", "request_changes", "Tighten the intro.", 1),
        )
        self.assertEqual(contents.get(item["id"])["last_reviewed_by_user_id"], self.admin.pk)

    # --- 12: revision conflicts still enforced for admins ---

    def test_admin_cannot_approve_outdated_revision(self):
        item = self.pending(self.admin)
        self.post(self.admin, item, "request_changes", feedback="Expand section two.")
        from content.services import save_content

        fresh = contents.get(item["id"])
        save_content(self.principal(self.admin), item["_id"],
                     {"title": fresh["title"], "keyword": "k", "content_type": "blog", "body_html": "<p>Expanded.</p>"},
                     expected_seq=fresh["edit_seq"], submit=True)
        response = self.post(self.admin, item, "approve", version=1)
        self.assertRedirects(response, reverse("approvals:review", args=[item["id"]]))
        self.assertEqual(contents.get(item["id"])["status"], "pending")
        with self.assertRaises(WorkflowConflict):
            decide(self.principal(self.admin), item["_id"], action="approve", feedback="", version_number=1)
        self.post(self.admin, item, "approve", version=2)
        self.assertEqual(contents.get(item["id"])["approved_version"], 2)
