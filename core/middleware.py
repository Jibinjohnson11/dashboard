import logging

from django.utils.functional import SimpleLazyObject

from core.permissions import Principal

logger = logging.getLogger("seo.middleware")


def _load_principal(request):
    from teams.services import profile_for_user

    if not request.user.is_authenticated:
        return None
    return Principal(request.user, profile_for_user(request.user))


class PrincipalMiddleware:
    """Attach ``request.principal`` (user + MongoDB team profile), resolved lazily once per request."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.principal = SimpleLazyObject(lambda: _load_principal(request))
        return self.get_response(request)


class SecurityHeadersMiddleware:
    """Headers not covered by Django's SecurityMiddleware, plus no-store for authenticated pages."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=(), usb=()")
        response.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        if request.user.is_authenticated and not request.path.startswith("/static/"):
            response.setdefault("Cache-Control", "no-store")
        return response
