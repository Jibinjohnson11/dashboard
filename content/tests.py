"""End-to-end content lifecycle: draft -> submit -> review -> revise -> publish."""
import threading

from django.urls import reverse

from approvals.repositories import approvals
from content.repositories import content_versions, contents
from core.exceptions import ServiceError, WorkflowConflict
from core.testing import WorkflowFixture
from publications.repositories import publications


class ContentWorkflowTests(WorkflowFixture):
    def create_via_form(self, action="save", **extra):
        data = {"brand_id": self.brand["id"], "campaign_id": self.campaign["id"], "title": "How to Choose Running Shoes",
                "keyword": "running shoes", "content_type": "article", "body_html": "<h2>Intro</h2><p>Pick shoes that fit.</p>",
                "internal_notes": "Draft one", "action": action}
        data.update(extra)
        self.client.force_login(self.writer)
        response = self.client.post(reverse("content:create"), data)
        return response, contents.find_one({"title": data["title"]})

    def edit_via_form(self, item, action="save", body="<p>Updated body text here.</p>"):
        fresh = contents.get(item["id"])
        return self.client.post(reverse("content:edit", args=[item["id"]]), {
            "title": fresh["title"], "keyword": fresh["keyword"], "content_type": fresh["content_type"],
            "body_html": body, "internal_notes": "", "edit_seq": fresh["edit_seq"], "action": action})

    def test_list_does_not_open_editor(self):
        self.client.force_login(self.writer)
        response = self.client.get(reverse("content:list"))
        self.assertContains(response, "Create New Content")
        self.assertNotContains(response, "data-editor-area")

    def test_save_draft(self):
        response, item = self.create_via_form()
        self.assertRedirects(response, reverse("content:detail", args=[item["id"]]))
        self.assertEqual(item["status"], "draft")
        self.assertEqual(item["current_version"], 1)
        self.assertEqual(item["writer_user_id"], self.writer.pk)
        self.assertEqual(item["word_count"], 5)
        version = content_versions.get_version(item["_id"], 1)
        self.assertFalse(version["locked"])
        # Editing a draft updates the same (unlocked) version.
        self.edit_via_form(item)
        self.assertEqual(contents.get(item["id"])["current_version"], 1)
        self.assertEqual(content_versions.count({"content_id": item["_id"]}), 1)

    def test_campaign_must_match_brand(self):
        response, item = self.create_via_form(brand_id=self.other_brand["id"])
        self.assertIsNone(item)
        self.assertEqual(response.status_code, 200)

    def test_submit_makes_content_read_only(self):
        _, item = self.create_via_form(action="submit")
        item = contents.get(item["id"])
        self.assertEqual(item["status"], "pending")
        self.assertTrue(content_versions.get_version(item["_id"], 1)["locked"])
        response = self.client.get(reverse("content:edit", args=[item["id"]]))
        self.assertRedirects(response, reverse("content:detail", args=[item["id"]]))
        response = self.edit_via_form(item)
        self.assertRedirects(response, reverse("content:detail", args=[item["id"]]))
        self.assertNotEqual(content_versions.get_version(item["_id"], 1)["body_html"], "<p>Updated body text here.</p>")

    def test_empty_content_cannot_be_submitted(self):
        response, item = self.create_via_form(action="submit", body_html="<p> </p>")
        self.assertEqual(contents.get(item["id"])["status"], "draft")

    def test_approve(self):
        item = self.make_content(self.writer, self.campaign)
        self.submit(self.writer, item)
        self.client.force_login(self.reviewer)
        response = self.client.post(reverse("approvals:review", args=[item["id"]]), {"action": "approve", "version_number": 1})
        self.assertRedirects(response, reverse("approvals:list"))
        item = contents.get(item["id"])
        self.assertEqual(item["status"], "approved")
        self.assertEqual(item["approved_version"], 1)
        record = approvals.find_one({"content_id": item["_id"]})
        self.assertEqual((record["action"], record["reviewer_user_id"], record["version_number"]), ("approve", self.reviewer.pk, 1))

    def test_client_can_approve_assigned_brand(self):
        item = self.make_content(self.writer, self.campaign)
        self.submit(self.writer, item)
        self.approve(self.client_user, item)
        self.assertEqual(contents.get(item["id"])["status"], "approved")

    def test_request_changes_requires_feedback_then_revise_and_resubmit(self):
        item = self.make_content(self.writer, self.campaign)
        self.submit(self.writer, item)
        self.client.force_login(self.reviewer)
        response = self.client.post(reverse("approvals:review", args=[item["id"]]), {"action": "request_changes", "feedback": "", "version_number": 1})
        self.assertContains(response, "explain the changes")
        self.assertEqual(contents.get(item["id"])["status"], "pending")
        self.client.post(reverse("approvals:review", args=[item["id"]]), {"action": "request_changes", "feedback": "Add a sizing section.", "version_number": 1})
        self.assertEqual(contents.get(item["id"])["status"], "needs_edit")

        # Writer sees feedback, edits (creates v2 because v1 is locked), resubmits.
        self.client.force_login(self.writer)
        self.assertContains(self.client.get(reverse("content:edit", args=[item["id"]])), "Add a sizing section.")
        self.edit_via_form(item, action="submit", body="<p>Now with a sizing section.</p>")
        item = contents.get(item["id"])
        self.assertEqual((item["status"], item["current_version"]), ("pending", 2))
        self.assertEqual(content_versions.get_version(item["_id"], 1)["body_html"], "<p>Great shoes for every runner.</p>")
        self.approve(self.reviewer, item)
        self.assertEqual(contents.get(item["id"])["approved_version"], 2)
        self.assertEqual(approvals.count({"content_id": item["_id"]}), 2)

    def test_reject_requires_reason_and_blocks_publishing(self):
        item = self.make_content(self.writer, self.campaign)
        self.submit(self.writer, item)
        with self.assertRaises(ServiceError):
            self.approve(self.reviewer, item, action="reject", feedback="")
        self.approve(self.reviewer, item, action="reject", feedback="Off-brand tone.")
        self.assertEqual(contents.get(item["id"])["status"], "rejected")
        self.client.force_login(self.writer)
        self.assertRedirects(self.client.get(reverse("content:edit", args=[item["id"]])), reverse("content:detail", args=[item["id"]]))
        from publications.services import publish

        with self.assertRaises(ServiceError):
            publish(self.principal(self.writer), content_id=item["id"], published_url="https://x.example/a",
                    published_by_user_id=self.writer.pk, published_date=None)
        self.assertEqual(publications.count(), 0)

    def test_reviewer_cannot_self_approve(self):
        # A reviewer who also authored the submitted version is blocked (service + HTTP + UI).
        item = self.make_content(self.writer, self.campaign)
        self.submit(self.writer, item)
        content_versions.col.update_one({"content_id": item["_id"]}, {"$set": {"created_by_user_id": self.reviewer.pk}})
        with self.assertRaises(ServiceError):
            self.approve(self.reviewer, item)
        self.client.force_login(self.reviewer)
        response = self.client.get(reverse("approvals:review", args=[item["id"]]))
        self.assertFalse(response.context["can_review"])
        self.assertContains(response, "someone else needs to review it")
        self.client.post(reverse("approvals:review", args=[item["id"]]), {"action": "approve", "version_number": 1})
        self.assertEqual(contents.get(item["id"])["status"], "pending")

    def test_team_member_cannot_review(self):
        item = self.make_content(self.writer, self.campaign)
        self.submit(self.writer, item)
        self.client.force_login(self.writer2)
        response = self.client.post(reverse("approvals:review", args=[item["id"]]), {"action": "approve", "version_number": 1})
        self.assertEqual(response.status_code, 403)

    def test_duplicate_approval_prevented(self):
        item = self.make_content(self.writer, self.campaign)
        self.submit(self.writer, item)
        self.approve(self.reviewer, item)
        with self.assertRaises(WorkflowConflict):
            self.approve(self.client_user, item)
        self.assertEqual(approvals.count({"content_id": item["_id"]}), 1)

    def test_outdated_revision_cannot_be_approved(self):
        item = self.make_content(self.writer, self.campaign)
        self.submit(self.writer, item)
        self.approve(self.reviewer, item, action="request_changes", feedback="Please expand.")
        self.client.force_login(self.writer)
        self.edit_via_form(item, action="submit", body="<p>Expanded.</p>")
        from approvals.services import decide

        with self.assertRaises(WorkflowConflict):
            decide(self.principal(self.reviewer), item["_id"], action="approve", feedback="", version_number=1)

    def test_concurrent_approvals_only_one_wins(self):
        item = self.make_content(self.writer, self.campaign)
        self.submit(self.writer, item)
        from approvals.services import decide

        results = []
        barrier = threading.Barrier(2)
        reviewers = [self.principal(self.reviewer), self.principal(self.client_user)]

        def attempt(principal, action):
            barrier.wait()
            try:
                decide(principal, item["_id"], action=action, feedback="Concurrent decision", version_number=1)
                results.append("ok")
            except WorkflowConflict:
                results.append("conflict")

        threads = [threading.Thread(target=attempt, args=(reviewers[0], "approve")),
                   threading.Thread(target=attempt, args=(reviewers[1], "reject"))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sorted(results), ["conflict", "ok"])
        self.assertEqual(approvals.count({"content_id": item["_id"]}), 1)

    def test_stale_edit_is_rejected(self):
        item = self.make_content(self.writer, self.campaign)
        from content.services import save_content

        data = {"title": "T1 title", "keyword": "k", "content_type": "blog", "body_html": "<p>a</p>"}
        save_content(self.principal(self.writer), item["_id"], data, expected_seq=0)
        with self.assertRaises(WorkflowConflict):
            save_content(self.principal(self.writer), item["_id"], dict(data, title="T2 title"), expected_seq=0)
        self.assertEqual(contents.get(item["id"])["title"], "T1 title")

    def test_approved_content_cannot_be_silently_modified(self):
        item = self.make_content(self.writer, self.campaign)
        self.submit(self.writer, item)
        self.approve(self.reviewer, item)
        self.client.force_login(self.writer)
        response = self.edit_via_form(item, body="<p>Sneaky change.</p>")
        self.assertRedirects(response, reverse("content:detail", args=[item["id"]]))
        self.assertEqual(content_versions.get_version(item["_id"], 1)["body_html"], "<p>Great shoes for every runner.</p>")

        # A revision creates v2 as a draft and clears approval: must be reviewed again.
        fresh = contents.get(item["id"])
        response = self.client.post(reverse("content:revise", args=[item["id"]]), {"edit_seq": fresh["edit_seq"]})
        self.assertRedirects(response, reverse("content:edit", args=[item["id"]]))
        revised = contents.get(item["id"])
        self.assertEqual((revised["status"], revised["current_version"], revised["approved_version"]), ("draft", 2, None))


class PublishingTests(WorkflowFixture):
    def approved_item(self):
        item = self.make_content(self.writer, self.campaign)
        self.submit(self.writer, item)
        self.approve(self.reviewer, item)
        return contents.get(item["id"])

    def publish_form(self, item, **extra):
        data = {"content_id": item["id"], "published_url": "https://news.example/best-shoes", "published_by_user_id": self.writer.pk,
                "published_date": "2026-10-20", "notes": "Guest post"}
        data.update(extra)
        return self.client.post(reverse("publications:create"), data)

    def test_publish_approved_content(self):
        item = self.approved_item()
        self.client.force_login(self.writer)
        response = self.publish_form(item)
        publication = publications.find_one({"content_id": item["_id"]})
        self.assertRedirects(response, reverse("publications:detail", args=[publication["id"]]))
        self.assertEqual(publication["approved_version_id"], item["approved_version_id"])
        self.assertEqual(publication["approved_version"], 1)
        self.assertEqual(publication["recorded_by_user_id"], self.writer.pk)
        self.assertEqual(publication["published_at"].date().isoformat(), "2026-10-20")
        self.assertEqual(contents.get(item["id"])["status"], "published")

    def test_cannot_publish_unapproved_content(self):
        item = self.make_content(self.writer, self.campaign)
        self.client.force_login(self.writer)
        for state in ("draft", "pending"):
            if state == "pending":
                self.submit(self.writer, item)
            response = self.publish_form(item)
            self.assertEqual(response.status_code, 200)
            self.assertIn("content_id", response.context["form"].errors)  # not even offered as a choice
        from publications.services import publish

        with self.assertRaises(ServiceError):
            publish(self.principal(self.writer), content_id=item["id"], published_url="https://x.example/b",
                    published_by_user_id=self.writer.pk, published_date=None)
        self.assertEqual(publications.count(), 0)

    def test_duplicate_publication_prevented(self):
        item = self.approved_item()
        self.client.force_login(self.writer)
        self.publish_form(item)
        from publications.services import publish

        with self.assertRaises(WorkflowConflict):
            publish(self.principal(self.writer), content_id=item["id"], published_url="https://other.example/x",
                    published_by_user_id=self.writer.pk, published_date=None)
        self.assertEqual(publications.count({"content_id": item["_id"]}), 1)

    def test_duplicate_url_rejected(self):
        first = self.approved_item()
        item2 = self.make_content(self.writer, self.campaign, title="Second article")
        self.submit(self.writer, item2)
        self.approve(self.reviewer, item2)
        self.client.force_login(self.writer)
        self.publish_form(first)
        response = self.publish_form(contents.get(item2["id"]))
        self.assertContains(response, "already recorded")
        self.assertEqual(contents.get(item2["id"])["status"], "approved")

    def test_future_date_and_bad_url_rejected(self):
        item = self.approved_item()
        self.client.force_login(self.writer)
        response = self.publish_form(item, published_date="2099-01-01")
        self.assertContains(response, "cannot be in the future")
        response = self.publish_form(item, published_url="javascript:alert(1)")
        self.assertIn("published_url", response.context["form"].errors)
        self.assertEqual(publications.count(), 0)

    def test_reviewer_and_client_cannot_record_publications(self):
        item = self.approved_item()
        for user in (self.reviewer, self.client_user):
            self.client.force_login(user)
            self.assertEqual(self.publish_form(item).status_code, 403)

    def test_edit_metadata_is_audited(self):
        item = self.approved_item()
        self.client.force_login(self.writer)
        self.publish_form(item)
        publication = publications.find_one({"content_id": item["_id"]})
        response = self.client.post(reverse("publications:edit", args=[publication["id"]]), {
            "published_url": "https://news.example/best-shoes-updated", "published_by_user_id": self.writer.pk,
            "published_date": "2026-10-21", "notes": "Moved URL"})
        self.assertRedirects(response, reverse("publications:detail", args=[publication["id"]]))
        self.assertEqual(publications.get(publication["id"])["published_url"], "https://news.example/best-shoes-updated")
        detail = self.client.get(reverse("publications:detail", args=[publication["id"]]))
        self.assertContains(detail, "Updated publication details")
        # Another team member without involvement cannot edit.
        self.client.force_login(self.writer2)
        self.assertEqual(self.client.get(reverse("publications:edit", args=[publication["id"]])).status_code, 404)

    def test_list_filters_and_csv_export(self):
        item = self.approved_item()
        self.client.force_login(self.writer)
        self.publish_form(item)
        self.client.force_login(self.admin)
        listing = self.client.get(reverse("publications:list"), {"brand": self.brand["id"], "date_from": "2026-10-01", "date_to": "2026-10-31"})
        self.assertEqual(listing.context["page"].total, 1)
        self.assertEqual(self.client.get(reverse("publications:list"), {"brand": self.other_brand["id"]}).context["page"].total, 0)
        self.assertEqual(self.client.get(reverse("publications:list"), {"date_to": "2026-10-19"}).context["page"].total, 0)
        export = self.client.get(reverse("publications:export"))
        self.assertEqual(export["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn("https://news.example/best-shoes", export.content.decode())

    def test_dashboard_counts_reflect_data(self):
        item = self.approved_item()
        self.client.force_login(self.writer)
        self.publish_form(item)
        self.make_content(self.writer, self.campaign, title="Just a draft")
        self.client.force_login(self.admin)
        stats = self.client.get(reverse("dashboard:home")).context["stats"]
        self.assertEqual(stats["published"], 1)
        self.assertEqual(stats["draft"], 1)
        self.assertEqual(stats["pending"], 0)
        self.assertEqual(stats["brands"], 2)
        self.assertEqual(stats["active_campaigns"], 2)
