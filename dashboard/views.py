import logging

from django.contrib.auth.decorators import login_not_required
from django.db import connection
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache

from core.mongo import ping
from core.permissions import module_required
from dashboard import services

logger = logging.getLogger("seo.health")


@module_required("dashboard")
def home(request):
    principal = request.principal
    stats = services.summary(principal)
    return render(request, "dashboard/home.html", {
        "stats": stats,
        "charts": services.charts(principal, stats["status_counts"]),
        "recent": services.recent_activity(principal),
    })


@module_required("dashboard")
def search(request):
    q = request.GET.get("q", "").strip()
    results = services.global_search(request.principal, q) if q else None
    total = sum(len(v) for v in results.values()) if results else 0
    return render(request, "dashboard/search.html", {"q": q, "results": results, "total": total})


@login_not_required
@never_cache
def healthz(request):
    checks = {}
    try:
        connection.ensure_connection()
        checks["database"] = "ok"
    except Exception:
        logger.exception("health.database_failed")
        checks["database"] = "error"
    try:
        ping()
        checks["mongodb"] = "ok"
    except Exception:
        logger.exception("health.mongodb_failed")
        checks["mongodb"] = "error"
    healthy = all(v == "ok" for v in checks.values())
    return JsonResponse({"status": "ok" if healthy else "degraded", "checks": checks}, status=200 if healthy else 503)
