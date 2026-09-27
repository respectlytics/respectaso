"""The pages RespectASO shows when a request fails: 400, 403 (and its variant
for a request that fails the CSRF check), 404 and 500.

A person who opened a page gets a page in RespectASO's own look; the app's
own scripts, which fetch data in the background, get the same message as
JSON under ``error``, the key every other endpoint uses, so they can show
it instead of a page of markup.

The 400 and 500 pages are standalone (templates/400.html, templates/500.html
through error_standalone.html): Django renders them without a request, and
a 500 can come from the very thing a normal page needs (the database, a
context processor, the stylesheet), so they use no context, no URL
reversing and no static files. The 403 and 404 pages sit inside the app's
shell, so the navigation is there to carry on from.

Wired in core/urls.py (handler400 to handler500) and core/settings.py
(CSRF_FAILURE_VIEW), in both editions; aso/tests/test_error_pages.py holds
them to it.
"""

import logging

from django.core.exceptions import DisallowedHost
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.template import loader

logger = logging.getLogger(__name__)

NOT_FOUND = "Not found. It may have been deleted."
FORBIDDEN = "RespectASO refused this request."
NOT_CONFIRMED = "RespectASO couldn't confirm this request. Reload the page and try again."
BAD_REQUEST = "RespectASO couldn't read this request."
SERVER_ERROR = ("RespectASO hit an unexpected error. Try again, and if it keeps "
                "happening, restart the app.")

# The last line of defence, when even the standalone template cannot load.
_BARE_500 = (
    "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
    "<meta name=\"robots\" content=\"noindex\"><title>Something went wrong</title></head>"
    "<body style=\"background:#0f172a;color:#f8fafc;font-family:system-ui,sans-serif;padding:48px\">"
    "<h1>Something went wrong</h1><p>" + SERVER_ERROR + "</p>"
    "<p><a href=\"/\" style=\"color:#c084fc\">Back to the Dashboard</a></p></body></html>"
)


def wants_page(request) -> bool:
    """True when a person opened this address, False when a script fetched
    it. Browsers say which in Sec-Fetch-Mode; without it, a page request is
    the one that asks for HTML."""
    try:
        mode = request.headers.get("Sec-Fetch-Mode")
        if mode:
            return mode == "navigate"
        return "text/html" in request.headers.get("Accept", "")
    except Exception:  # a malformed request still gets an answer
        return True


def bad_request(request, exception=None):
    if not wants_page(request):
        return JsonResponse({"error": BAD_REQUEST}, status=400)
    context = {"disallowed_host": isinstance(exception, DisallowedHost)}
    try:
        return HttpResponse(loader.get_template("400.html").render(context), status=400)
    except Exception:
        logger.exception("The 400 page could not be rendered")
        return HttpResponse(BAD_REQUEST, status=400, content_type="text/plain; charset=utf-8")


def permission_denied(request, exception=None):
    if not wants_page(request):
        return JsonResponse({"error": FORBIDDEN}, status=403)
    return render(request, "403.html", status=403)


def csrf_failure(request, reason=""):
    """A form or a fetch whose CSRF token did not match."""
    if not wants_page(request):
        return JsonResponse({"error": NOT_CONFIRMED}, status=403)
    return render(request, "403_csrf.html", status=403)


def page_not_found(request, exception=None):
    if not wants_page(request):
        return JsonResponse({"error": NOT_FOUND}, status=404)
    return render(request, "404.html", status=404)


def server_error(request=None):
    """Never fails: JSON for a script, the standalone page for a person, and
    a page written out in full if even that cannot load."""
    if request is not None and not wants_page(request):
        return JsonResponse({"error": SERVER_ERROR}, status=500)
    try:
        return HttpResponse(loader.get_template("500.html").render(), status=500)
    except Exception:
        logger.exception("The 500 page could not be rendered")
        return HttpResponse(_BARE_500, status=500)
