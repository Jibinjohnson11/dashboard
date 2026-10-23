from pymongo import DESCENDING

from core.repository import Repository
from core.utils import with_id


class ContentRepository(Repository):
    collection_name = "contents"

    def status_counts(self, query):
        rows = self.aggregate([{"$match": query}, {"$group": {"_id": "$status", "n": {"$sum": 1}}}])
        return {row["_id"]: row["n"] for row in rows}

    def status_counts_by(self, field, query):
        rows = self.aggregate([
            {"$match": query},
            {"$group": {"_id": {"key": f"${field}", "status": "$status"}, "n": {"$sum": 1}}},
        ])
        result = {}
        for row in rows:
            result.setdefault(row["_id"]["key"], {})[row["_id"]["status"]] = row["n"]
        return result


class ContentVersionRepository(Repository):
    collection_name = "content_versions"
    timestamp_fields = ("created_at",)

    def get_version(self, content_id, version_number):
        return with_id(self.col.find_one({"content_id": content_id, "version_number": version_number}))

    def for_content(self, content_id):
        return self.find({"content_id": content_id}, sort=[("version_number", DESCENDING)])


contents = ContentRepository()
content_versions = ContentVersionRepository()
