"""Brand logo uploads: validated by file signature, stored with random names, served privately.

SVG is deliberately not accepted: SVG files can contain scripts.
"""
import logging
import uuid

from django.conf import settings
from django.core.files.storage import FileSystemStorage

logger = logging.getLogger("seo.brands")

ALLOWED_LABEL = "PNG, JPG, WebP or GIF"
LOGO_DIR = "brand_logos"


def storage():
    return FileSystemStorage(location=settings.MEDIA_ROOT)


def detect_image_type(head):
    """Return (extension, content type) from the file's magic bytes, or None."""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png", "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpg", "image/jpeg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "gif", "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp", "image/webp"
    return None


def inspect_upload(upload):
    """Validate an uploaded file. Returns (extension, content type) or raises ValueError."""
    if upload.size > settings.BRAND_LOGO_MAX_BYTES:
        raise ValueError(f"The logo must be {settings.BRAND_LOGO_MAX_BYTES // (1024 * 1024)} MB or smaller.")
    if upload.size == 0:
        raise ValueError("The uploaded file is empty.")
    upload.seek(0)
    head = upload.read(16)
    upload.seek(0)
    kind = detect_image_type(head)
    if kind is None:
        raise ValueError(f"Upload a {ALLOWED_LABEL} image.")
    return kind


def save_logo(upload):
    """Store a validated upload under a random name. Returns the stored metadata."""
    extension, content_type = inspect_upload(upload)
    name = storage().save(f"{LOGO_DIR}/{uuid.uuid4().hex}.{extension}", upload)
    return {"logo_path": name, "logo_content_type": content_type}


def delete_logo(path):
    if not path:
        return
    try:
        storage().delete(path)
    except OSError:
        logger.warning("brands.logo_delete_failed", extra={"path": path})


def open_logo(path):
    return storage().open(path, "rb")
