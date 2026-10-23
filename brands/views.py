from django.contrib import messages
from django.http import FileResponse, Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from brands import logos, services
from brands.forms import BrandForm
from core.exceptions import ServiceError
from core.permissions import module_required, require
from core.utils import page_number


@module_required("brands")
def brand_list(request):
    q = request.GET.get("q", "")
    status = request.GET.get("status", "")
    if status not in dict(services.FILTER_STATUS_CHOICES):
        status = ""
    page = services.list_brands(request.principal, q=q, status=status, page=page_number(request))
    return render(request, "brands/list.html", {
        "page": page,
        "q": q,
        "status": status,
        "status_choices": services.FILTER_STATUS_CHOICES,
    })


@module_required("brands")
@require_http_methods(["GET", "POST"])
def brand_create(request):
    require(request.principal.is_admin)
    form = BrandForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        try:
            brand = services.create_brand(request.principal, form.cleaned_data)
        except ServiceError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, f"Brand “{brand['name']}” created.")
            return redirect("brands:detail", brand_id=brand["id"])
    return render(request, "brands/form.html", {"form": form, "is_edit": False})


@module_required("brands")
def brand_detail(request, brand_id):
    brand = services.get_brand(request.principal, brand_id)
    return render(request, "brands/detail.html", {"brand": brand, **services.brand_detail(request.principal, brand)})


@module_required("brands")
@require_http_methods(["GET", "POST"])
def brand_edit(request, brand_id):
    require(request.principal.is_admin)
    brand = services.get_brand(request.principal, brand_id)
    form = BrandForm(request.POST or None, request.FILES or None, initial=brand)
    if request.method == "POST" and form.is_valid():
        try:
            services.update_brand(request.principal, brand_id, form.cleaned_data)
        except ServiceError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "Brand updated.")
            return redirect("brands:detail", brand_id=brand_id)
    return render(request, "brands/form.html", {"form": form, "brand": brand, "is_edit": True})


@module_required("brands")
@require_POST
def brand_archive(request, brand_id):
    try:
        services.archive_brand(request.principal, brand_id)
        messages.success(request, "Brand archived. Its campaigns and content history are preserved.")
    except ServiceError as exc:
        messages.error(request, str(exc))
    return redirect("brands:detail", brand_id=brand_id)


@module_required("brands")
@require_POST
def brand_restore(request, brand_id):
    try:
        services.restore_brand(request.principal, brand_id)
        messages.success(request, "Brand restored.")
    except ServiceError as exc:
        messages.error(request, str(exc))
    return redirect("brands:detail", brand_id=brand_id)


@module_required("brands")
def brand_logo(request, brand_id):
    """Serve a brand's logo only to users who may see the brand (no public media URLs)."""
    brand = services.get_brand(request.principal, brand_id)
    if not brand.get("logo_path"):
        raise Http404("No logo.")
    try:
        handle = logos.open_logo(brand["logo_path"])
    except (FileNotFoundError, OSError):
        raise Http404("Logo file missing.")
    response = FileResponse(handle, content_type=brand.get("logo_content_type") or "application/octet-stream")
    response["Content-Disposition"] = "inline"
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = "default-src 'none'; sandbox"
    response["Cache-Control"] = "private, max-age=3600"
    return response
