from django.shortcuts import render


def permission_denied(request, exception=None):
    message = str(exception) if exception and str(exception) else "You do not have permission to view this page."
    return render(request, "errors/error.html", {"code": 403, "title": "Access denied", "message": message}, status=403)


def page_not_found(request, exception=None):
    return render(
        request,
        "errors/error.html",
        {"code": 404, "title": "Page not found", "message": "The page or record you requested does not exist or is not available to you."},
        status=404,
    )


def server_error(request):
    return render(
        request,
        "errors/error.html",
        {"code": 500, "title": "Something went wrong", "message": "An unexpected error occurred. The team has been notified."},
        status=500,
    )


def rate_limited(request, message):
    return render(request, "errors/error.html", {"code": 429, "title": "Too many attempts", "message": message}, status=429)
