import segno
from django.utils.safestring import mark_safe


def qr_svg(payload, scale=6):
    """Inline SVG for ``payload``. The payload holds only a random token."""
    qr = segno.make(payload, error="m")
    return mark_safe(qr.svg_inline(scale=scale, dark="#141821", light="#ffffff", border=2))
