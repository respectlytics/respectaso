"""How the app introduces itself on every call to respectaso.com.

Lives in the free-tier ``aso`` app because both editions call
respectaso.com (the daily update check, aso/update_check.py); the Pro
licence client (licensing/api_client.py) imports it from here.
"""


def user_agent() -> str:
    """``RespectASO/<version> (+https://respectaso.com)``.

    urllib's default ``Python-urllib/3.x`` is rejected outright by the
    Cloudflare browser-integrity check in front of respectaso.com (HTTP 403,
    error 1010), which turned every call into a silent fail-open: license
    refreshes never arrived and the app could not tell that from a customer
    having no newer key. A named agent is also plain good manners for an
    API client.
    """
    try:
        from django.conf import settings

        version = settings.VERSION
    except Exception:  # noqa: BLE001 (settings not configured on bootstrap paths; still identify)
        version = "unknown"
    return f"RespectASO/{version} (+https://respectaso.com)"
