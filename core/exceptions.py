from django.core.exceptions import PermissionDenied
from django.http import Http404


class NotFound(Http404):
    """Record does not exist or is not visible to the current user (avoids leaking existence)."""


class Forbidden(PermissionDenied):
    """Record is visible but the requested action is not permitted."""


class ServiceError(Exception):
    """A business rule was violated. The message is safe to show to the user."""


class WorkflowConflict(ServiceError):
    """A state transition failed because the record changed concurrently or is in the wrong state."""
