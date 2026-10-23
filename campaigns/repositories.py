from pymongo.errors import DuplicateKeyError

from core.repository import Repository
from core.utils import utcnow


class CampaignRepository(Repository):
    collection_name = "campaigns"

    def active_counts_by_brand(self, brand_ids):
        rows = self.aggregate([
            {"$match": {"brand_id": {"$in": list(brand_ids)}, "status": "active"}},
            {"$group": {"_id": "$brand_id", "n": {"$sum": 1}}},
        ])
        return {row["_id"]: row["n"] for row in rows}


class CampaignAssignmentRepository(Repository):
    collection_name = "campaign_assignments"
    timestamp_fields = ("created_at",)

    def user_ids_for(self, campaign_id):
        return [doc["django_user_id"] for doc in self.col.find({"campaign_id": campaign_id})]

    def campaign_ids_for_user(self, user_id):
        return [doc["campaign_id"] for doc in self.col.find({"django_user_id": user_id})]

    def replace_for_campaign(self, campaign_id, user_ids):
        user_ids = set(user_ids)
        self.col.delete_many({"campaign_id": campaign_id, "django_user_id": {"$nin": list(user_ids)}})
        for user_id in user_ids:
            try:
                self.col.insert_one({"campaign_id": campaign_id, "django_user_id": user_id, "created_at": utcnow()})
            except DuplicateKeyError:
                pass

    def replace_for_user(self, user_id, campaign_ids):
        campaign_ids = set(campaign_ids)
        self.col.delete_many({"django_user_id": user_id, "campaign_id": {"$nin": list(campaign_ids)}})
        for campaign_id in campaign_ids:
            try:
                self.col.insert_one({"campaign_id": campaign_id, "django_user_id": user_id, "created_at": utcnow()})
            except DuplicateKeyError:
                pass


campaigns = CampaignRepository()
campaign_assignments = CampaignAssignmentRepository()
