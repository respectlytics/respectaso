"""Install and update shows the steps of the edition it runs in
(aso/templates/aso/setup.html, docs/development/PRO_AND_CLICKABLE_TEXT_PLAN.md 5).

The Mac app (RESPECTASO_NATIVE=1 or the bundled app) shows how to install and
update the Mac app; Docker shows how to update the container and how to move
to the Mac app. Neither shows the other's commands, which would only make a
reader wonder whether they need them. Both builds serve the page from the
Help menu's Install and update.
"""

from django.test import TestCase, override_settings
from django.urls import reverse

DOCKER_UPDATE = ("git pull", "docker compose down", "docker compose build --no-cache", "docker compose up -d")


class InstallAndUpdateTest(TestCase):
    def _page(self):
        return self.client.get(reverse("aso:setup")).content.decode()

    @override_settings(IS_NATIVE_APP=False)
    def test_docker_shows_how_to_update_the_container(self):
        html = self._page()
        self.assertIn("Updating to a New Version (Docker)", html)
        for command in DOCKER_UPDATE:
            self.assertIn(command, html)
        self.assertIn("Migrating from Docker to Native App", html)

    @override_settings(IS_NATIVE_APP=True)
    def test_the_mac_app_shows_how_to_update_the_app(self):
        html = self._page()
        self.assertIn("Updating to a New Version", html)
        self.assertIn("Relaunch RespectASO", html)
        self.assertNotIn("docker compose", html)

    def test_the_help_menu_opens_the_same_page_in_the_pro_build(self):
        from django.apps import apps as django_apps

        if not django_apps.is_installed("aso_pro"):
            self.skipTest("the free edition has one route")
        with override_settings(IS_NATIVE_APP=False):
            html = self.client.get(reverse("aso_pro:settings_setup")).content.decode()
        self.assertIn("docker compose build --no-cache", html)
