from pymongo.errors import DuplicateKeyError

from core.repository import Repository
from core.utils import utcnow, with_id


class TeamProfileRepository(Repository):
    collection_name = "team_profiles"

    def for_user(self, user_id):
        return with_id(self.col.find_one({"django_user_id": user_id}))

    def by_user_ids(self, user_ids):
        ids = list({u for u in user_ids if u is not None})
        if not ids:
            return {}
        return {doc["django_user_id"]: with_id(doc) for doc in self.col.find({"django_user_id": {"$in": ids}})}

    def update_for_user(self, user_id, fields):
        fields = dict(fields, updated_at=utcnow())
        return self.col.update_one({"django_user_id": user_id}, {"$set": fields}).matched_count == 1


class BrandAccessRepository(Repository):
    collection_name = "brand_access"
    timestamp_fields = ("created_at",)

    def brand_ids_for_user(self, user_id):
        return [doc["brand_id"] for doc in self.col.find({"django_user_id": user_id})]

    def user_ids_for_brand(self, brand_id):
        return [doc["django_user_id"] for doc in self.col.find({"brand_id": brand_id})]

    def replace_for_user(self, user_id, brand_ids, access_role):
        brand_ids = set(brand_ids)
        self.col.delete_many({"django_user_id": user_id, "brand_id": {"$nin": list(brand_ids)}})
        self.col.update_many({"django_user_id": user_id}, {"$set": {"access_role": access_role}})
        for brand_id in brand_ids:
            try:
                self.col.insert_one({
                    "django_user_id": user_id,
                    "brand_id": brand_id,
                    "access_role": access_role,
                    "created_at": utcnow(),
                })
            except DuplicateKeyError:
                pass


class RolePermissionRepository(Repository):
    """Admin-edited default permissions per role. No document = built-in defaults."""

    collection_name = "role_permissions"

    def for_role(self, role):
        return self.col.find_one({"role": role})

    def all_by_role(self):
        return {doc["role"]: doc for doc in self.col.find({})}

    def save(self, role, grants, user_id):
        self.col.update_one(
            {"role": role},
            {"$set": {"grants": sorted(grants), "updated_at": utcnow(), "updated_by_user_id": user_id},
             "$setOnInsert": {"created_at": utcnow()}},
            upsert=True,
        )

    def reset(self, role):
        self.col.delete_one({"role": role})


team_profiles = TeamProfileRepository()
role_permissions = RolePermissionRepository()
brand_access = BrandAccessRepository()
