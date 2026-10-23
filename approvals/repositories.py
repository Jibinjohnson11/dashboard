from pymongo import DESCENDING

from core.repository import Repository


class ApprovalRepository(Repository):
    collection_name = "content_approvals"
    timestamp_fields = ()

    def for_content(self, content_id):
        return self.find({"content_id": content_id}, sort=[("reviewed_at", DESCENDING)])


approvals = ApprovalRepository()
