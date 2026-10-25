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
    import time

    principal = request.principal
    start = time.perf_counter()

    stats = services.summary(principal)
    t1 = time.perf_counter()

    charts = services.charts(principal, stats["status_counts"])
    t2 = time.perf_counter()

    recent = services.recent_activity(principal)
    t3 = time.perf_counter()

    logger.warning(
        "DASHBOARD_PERFORMANCE summary=%.3fs charts=%.3fs recent=%.3fs total=%.3fs",
        t1 - start,
        t2 - t1,
        t3 - t2,
        t3 - start,
    )

    return render(request, "dashboard/home.html", {
        "stats": stats,
        "charts": charts,
        "recent": recent,
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
