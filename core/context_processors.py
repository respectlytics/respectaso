from django.conf import settings

from aso.desktop_bridge import is_desktop_app


def version(request):
    """Expose VERSION, IS_NATIVE_APP, DESKTOP_APP and INSTALLED_APPS to all templates.

    IS_NATIVE_APP says the process was started as the Mac app; DESKTOP_APP
    says its native side is running (aso/desktop_bridge.py), which is what
    decides whether Settings → Mac App exists.
    """
    return {
        "VERSION": settings.VERSION,
        "IS_NATIVE_APP": getattr(settings, "IS_NATIVE_APP", False),
        "DESKTOP_APP": is_desktop_app(),
        "INSTALLED_APPS": settings.INSTALLED_APPS,
        "STYLESHEET_VERSION": _stylesheet_version(),
    }


def _stylesheet_version() -> str:
    """In development, the stylesheet's last change, added to its address so a
    browser never keeps an old copy beside new pages (2026-10-02). The Mac app
    and Docker serve it under a hashed name and need nothing."""
    if not settings.DEBUG:
        return ""
    try:
        return str(int((settings.BASE_DIR / "static" / "css" / "tailwind.css").stat().st_mtime))
    except OSError:
        return ""
