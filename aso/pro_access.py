"""Whether this process may use Pro features: the one rule, asked by the
free-tier ``aso`` app for its size limits (keyword search: 1,000 keywords
per search and the queue with Pro, 3 per search and one at a time without),
by the Pro tabs, by ``licensing.decorators.pro_required`` and by the Rival
Tracker's daily check.

The DEBUG bypass first, then the stored license through
``get_license_info``. Expired, refunded and revoked licenses are not valid,
so they count as free. ``license_expired()`` tells a lapsed license from
none, for the previews that offer renewal instead of a first purchase.

Ships in the free-tier ``aso`` app, so ``licensing`` is only imported inside
the function, after checking that the app is installed at all
(``aso_pro/tests/test_public_sync.py`` enforces this).
"""

import functools

from django.apps import apps as django_apps
from django.conf import settings
from django.http import JsonResponse

PRO_REQUIRED_MESSAGE = "This is part of RespectASO Pro. Add your license key in Settings → License."


def has_pro_license() -> bool:
    """True when this process may use Pro features: a valid license, or the
    DEBUG bypass."""
    if getattr(settings, "DEBUG_SKIP_LICENSE", False):
        return True
    if not django_apps.is_installed("licensing"):
        return False
    from licensing.decorators import get_license_info

    info = get_license_info()
    return info is not None and info.is_valid


def license_expired() -> bool:
    """True when a license is stored and it has run out: the Pro previews
    then offer renewal instead of a first purchase."""
    if not django_apps.is_installed("licensing"):
        return False
    from licensing.decorators import get_license_info

    info = get_license_info()
    return info is not None and info.is_expired


def pro_required_json(view_func):
    """JSON endpoints only: 403 with the standard message unless
    ``has_pro_license()``. Page views keep using ``licensing.decorators
    .pro_required``, which redirects instead."""

    @functools.wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not has_pro_license():
            return JsonResponse({"error": PRO_REQUIRED_MESSAGE}, status=403)
        return view_func(request, *args, **kwargs)

    return wrapper
