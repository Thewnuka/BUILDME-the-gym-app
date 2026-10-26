from django import template
from django.conf import settings
from django.contrib.humanize.templatetags.humanize import intcomma

register = template.Library()


@register.filter
def money(value):
    """Format a number as currency, e.g. 1234.5 -> $1,234.50"""
    if value is None or value == "":
        return "—"
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return value
    whole, _, cents = f"{amount:.2f}".partition(".")
    symbol = settings.CURRENCY_SYMBOL
    sep = " " if symbol[-1:].isalpha() else ""
    return f"{symbol}{sep}{intcomma(int(whole))}.{cents}"


@register.filter
def has_perm(user, perm):
    return bool(user and user.is_authenticated and user.has_perm(perm))


@register.inclusion_tag("components/form_field.html")
def form_field(field, col=""):
    return {"field": field, "col": col}
