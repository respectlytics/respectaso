"""RespectASO's own error pages (aso/error_views.py), in both editions.

A person who opens a page that fails sees RespectASO's page for it, with a
way on, and never Django's debug page or its bare default; the app's own
scripts get the same message as JSON under ``error``. The 400 and 500 pages
must render with no request and no context, and the 500 must answer even
when its template cannot load. Both shipped editions run with DEBUG off,
since with DEBUG on none of this is ever shown.
"""

import re
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.core.exceptions import DisallowedHost, PermissionDenied, SuspiciousOperation
from django.template import loader
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import get_resolver, path

from aso import error_views

PAGE = {"HTTP_ACCEPT": "text/html,application/xhtml+xml,*/*;q=0.8", "HTTP_SEC_FETCH_MODE": "navigate"}
SCRIPT = {"HTTP_ACCEPT": "*/*", "HTTP_SEC_FETCH_MODE": "cors"}


def _crash(request):
    raise RuntimeError("boom")


def _refuse(request):
    raise PermissionDenied


def _suspicious(request):
    raise SuspiciousOperation("odd request")


# The real routes and handlers, plus three views that fail on purpose.
urlpatterns = [
    path("test-errors/crash/", _crash),
    path("test-errors/refuse/", _refuse),
    path("test-errors/suspicious/", _suspicious),
    *__import__("core.urls", fromlist=["urlpatterns"]).urlpatterns,
]
handler400 = "aso.error_views.bad_request"
handler403 = "aso.error_views.permission_denied"
handler404 = "aso.error_views.page_not_found"
handler500 = "aso.error_views.server_error"


class WiredInTest(SimpleTestCase):
    def test_the_real_urlconf_names_every_handler(self):
        resolver = get_resolver()
        for code, view in ((400, error_views.bad_request), (403, error_views.permission_denied),
                           (404, error_views.page_not_found), (500, error_views.server_error)):
            self.assertIs(resolver.resolve_error_handler(code), view, code)

    def test_the_csrf_check_uses_our_page(self):
        self.assertEqual(settings.CSRF_FAILURE_VIEW, "aso.error_views.csrf_failure")

    def test_the_mac_app_runs_with_debug_off(self):
        import os

        from desktop.main import configure_environment

        saved = {k: os.environ.get(k) for k in ("DJANGO_SETTINGS_MODULE", "DATA_DIR", "RESPECTASO_NATIVE", "DEBUG")}
        try:
            os.environ.pop("DEBUG", None)
            configure_environment(Path("/tmp/respectaso-test"))
            self.assertEqual(os.environ["DEBUG"], "False")
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_the_docker_image_runs_with_debug_off(self):
        dockerfile = Path(settings.BASE_DIR) / "Dockerfile"
        if not dockerfile.exists():
            self.skipTest("no Dockerfile in this tree")
        self.assertRegex(dockerfile.read_text(), r"(?m)^ENV DEBUG=False$")


@override_settings(ROOT_URLCONF="aso.tests.test_error_pages", DEBUG=False)
class PagesTest(TestCase):
    def setUp(self):
        self.client = Client(raise_request_exception=False)

    def assertOurPage(self, response, status, heading):
        self.assertEqual(response.status_code, status)
        self.assertIn("text/html", response["Content-Type"])
        html = response.content.decode()
        self.assertIn(heading, html)
        self.assertIn('name="robots" content="noindex"', html)
        self.assertIn('href="/"', html)
        for leak in ("Traceback", "DEBUG = True", "Django", "URLconf", "boom"):
            self.assertNotIn(leak, html)
        return html

    def test_404_in_the_app_shell(self):
        html = self.assertOurPage(self.client.get("/no-such-page/", **PAGE), 404, "This page doesn't exist")
        self.assertIn("ASO Simulator", html)            # the navigation is there to carry on from
        self.assertIn("Go back", html)

    def test_404_for_a_script_is_json(self):
        response = self.client.get("/no-such-page/", **SCRIPT)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"error": error_views.NOT_FOUND})

    def test_500_is_standalone_and_logged(self):
        with self.assertLogs("django.request", "ERROR") as logs:
            html = self.assertOurPage(self.client.get("/test-errors/crash/", **PAGE), 500, "Something went wrong")
        self.assertIn("RuntimeError: boom", "\n".join(logs.output))   # the log keeps what the page hides
        self.assertIn("<style>", html)
        self.assertNotIn("/static/", html)
        self.assertIn("mailto:respectaso@loheden.com", html)

    def test_500_for_a_script_is_json(self):
        with self.assertLogs("django.request", "ERROR"):
            response = self.client.get("/test-errors/crash/", **SCRIPT)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {"error": error_views.SERVER_ERROR})

    def test_500_answers_even_when_its_template_cannot_load(self):
        with patch("aso.error_views.loader.get_template", side_effect=OSError("disk")), \
                self.assertLogs("django.request", "ERROR"), self.assertLogs("aso.error_views", "ERROR"):
            html = self.assertOurPage(self.client.get("/test-errors/crash/", **PAGE), 500, "Something went wrong")
        self.assertIn(error_views.SERVER_ERROR, html)

    def test_403_in_the_app_shell(self):
        with self.assertLogs("django.request", "WARNING"):
            self.assertOurPage(self.client.get("/test-errors/refuse/", **PAGE), 403, "RespectASO refused this request")

    def test_a_request_that_fails_the_csrf_check(self):
        client = Client(enforce_csrf_checks=True, raise_request_exception=False)
        with self.assertLogs("django.security.csrf", "WARNING"):
            response = client.post("/whats-new/seen/", **PAGE)
        self.assertOurPage(response, 403, "This request couldn't be confirmed")
        with self.assertLogs("django.security.csrf", "WARNING"):
            response = client.post("/whats-new/seen/", **SCRIPT)
        self.assertEqual(response.json(), {"error": error_views.NOT_CONFIRMED})

    def test_400_is_standalone(self):
        with self.assertLogs("django.security", "ERROR"):
            html = self.assertOurPage(self.client.get("/test-errors/suspicious/", **PAGE), 400,
                                      "RespectASO couldn't read this request")
        self.assertNotIn("respectaso.private", html)

    def test_an_unknown_address_says_which_ones_work(self):
        with self.assertLogs("django.security", "ERROR"):
            html = self.assertOurPage(self.client.get("/", HTTP_HOST="192.168.1.20", **PAGE), 400,
                                      "RespectASO couldn't read this request")
        self.assertIn("localhost, 127.0.0.1 and respectaso.private", html)


class StandaloneTemplatesTest(SimpleTestCase):
    """Django renders the 400 and 500 pages with no request and no context."""

    def test_they_render_with_nothing(self):
        for name, heading in (("500.html", "Something went wrong"),
                              ("400.html", "RespectASO couldn't read this request")):
            html = loader.get_template(name).render()
            self.assertIn(heading, html, name)
            self.assertIn('href="/"', html, name)

    def test_they_depend_on_nothing(self):
        base = Path(settings.BASE_DIR) / "aso" / "templates"
        for name in ("error_standalone.html", "400.html", "500.html"):
            src = re.sub(r"{%\s*comment\s*%}.*?{%\s*endcomment\s*%}", "",
                         (base / name).read_text(encoding="utf-8"), flags=re.S)
            for forbidden in ("{% url", "{% static", "{% load", "{% include", "aso/base.html", "request.", "<link"):
                self.assertNotIn(forbidden, src, f"{name}: {forbidden}")


class DisallowedHostContextTest(SimpleTestCase):
    def test_the_handler_names_the_addresses_only_for_an_unknown_host(self):
        from django.test import RequestFactory

        request = RequestFactory().get("/", **PAGE)
        self.assertIn(b"respectaso.private",
                      error_views.bad_request(request, DisallowedHost("x")).content)
        self.assertNotIn(b"respectaso.private",
                         error_views.bad_request(request, SuspiciousOperation("x")).content)
