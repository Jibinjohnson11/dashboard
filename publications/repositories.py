from core.repository import Repository


class PublicationRepository(Repository):
    collection_name = "publications"

    def daily_counts(self, query, since):
        rows = self.aggregate([
            {"$match": dict(query, published_at={"$gte": since})},
            {"$group": {"_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$published_at"}}, "n": {"$sum": 1}}},
        ])
        return {row["_id"]: row["n"] for row in rows}


publications = PublicationRepository()
