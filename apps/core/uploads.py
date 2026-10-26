"""Server-side validation for uploaded files (checks the content, not just the name)."""
from pathlib import Path

from django import forms
from django.core.files.uploadedfile import UploadedFile
from PIL import Image

IMAGE_FORMATS = {"JPEG", "PNG", "WEBP"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
MAX_IMAGE_PIXELS = 25_000_000


def validate_upload(file, max_bytes, allow_pdf=False):
    """Accept JPEG/PNG/WebP images (and optionally PDF) up to ``max_bytes``."""
    if not isinstance(file, UploadedFile):
        return file  # unchanged existing file, or cleared
    if file.size > max_bytes:
        raise forms.ValidationError(f"The file is too large. The maximum size is {max_bytes // 1024} KB.")
    ext = Path(file.name or "").suffix.lower()
    allowed = IMAGE_EXTENSIONS | ({".pdf"} if allow_pdf else set())
    kinds = "a PDF or a JPEG, PNG or WebP image" if allow_pdf else "a JPEG, PNG or WebP image"
    if ext not in allowed:
        raise forms.ValidationError(f"Upload {kinds}.")
    file.seek(0)
    if ext == ".pdf":
        if file.read(5) != b"%PDF-":
            raise forms.ValidationError("This file is not a valid PDF.")
    else:
        try:
            with Image.open(file) as img:
                fmt, (w, h) = img.format, img.size
                img.verify()
        except Exception:
            raise forms.ValidationError(f"Upload {kinds}.")
        if fmt not in IMAGE_FORMATS:
            raise forms.ValidationError(f"Upload {kinds}.")
        if w * h > MAX_IMAGE_PIXELS:
            raise forms.ValidationError("The image dimensions are too large.")
    file.seek(0)
    return file
