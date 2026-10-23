"""Base repository: the only layer that talks to MongoDB collections directly."""
from pymongo import DESCENDING, ReturnDocument

from core.exceptions import NotFound
from core.mongo import get_db
from core.utils import Page, default_page_size, parse_oid, utcnow, with_id


class Repository:
    collection_name = None

    @property
    def col(self):
        return get_db()[self.collection_name]

    def get(self, oid, extra_filter=None):
        query = {"_id": parse_oid(oid, not_found=True)}
        if extra_filter:
            query.update(extra_filter)
        return with_id(self.col.find_one(query))

    def get_or_404(self, oid, extra_filter=None):
        doc = self.get(oid, extra_filter)
        if doc is None:
            raise NotFound("Record not found.")
        return doc

    def find_one(self, query):
        return with_id(self.col.find_one(query))

    def find(self, query=None, sort=None, limit=0, skip=0, projection=None):
        cursor = self.col.find(query or {}, projection)
        if sort:
            cursor = cursor.sort(sort)
        if skip:
            cursor = cursor.skip(skip)
        if limit:
            cursor = cursor.limit(limit)
        return [with_id(doc) for doc in cursor]

    def find_page(self, query, page=1, per_page=None, sort=None):
        per_page = per_page or default_page_size()
        total = self.col.count_documents(query)
        result = Page([], page, per_page, total)
        result.items = self.find(
            query,
            sort=sort or [("created_at", DESCENDING)],
            skip=(result.number - 1) * per_page,
            limit=per_page,
        )
        return result

    def count(self, query=None):
        return self.col.count_documents(query or {})

    def by_ids(self, ids):
        """Return {ObjectId: doc} for a batch of ids (used to hydrate related records)."""
        ids = list({i for i in ids if i is not None})
        if not ids:
            return {}
        return {doc["_id"]: doc for doc in self.find({"_id": {"$in": ids}})}

    def insert(self, doc):
        now = utcnow()
        doc.setdefault("created_at", now)
        if "updated_at" in self.timestamp_fields:
            doc.setdefault("updated_at", now)
        result = self.col.insert_one(doc)
        doc["_id"] = result.inserted_id
        return with_id(doc)

    timestamp_fields = ("created_at", "updated_at")

    def update(self, oid, fields, extra_filter=None):
        query = {"_id": parse_oid(oid, not_found=True)}
        if extra_filter:
            query.update(extra_filter)
        fields = dict(fields)
        if "updated_at" in self.timestamp_fields:
            fields["updated_at"] = utcnow()
        return self.col.update_one(query, {"$set": fields}).matched_count == 1

    def transition(self, query, update):
        """Atomically update one document only if ``query`` still matches. Returns the new doc or None."""
        return with_id(self.col.find_one_and_update(query, update, return_document=ReturnDocument.AFTER))

    def delete(self, oid):
        return self.col.delete_one({"_id": parse_oid(oid, not_found=True)}).deleted_count == 1

    def aggregate(self, pipeline):
        return list(self.col.aggregate(pipeline))
