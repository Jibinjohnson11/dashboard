from core.repository import Repository


class BrandRepository(Repository):
    collection_name = "brands"


brands = BrandRepository()
