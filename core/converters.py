class ObjectIdConverter:
    """URL converter that only matches 24-character hex ObjectIds (anything else is a 404)."""

    regex = "[0-9a-fA-F]{24}"

    def to_python(self, value):
        return value.lower()

    def to_url(self, value):
        return str(value)
