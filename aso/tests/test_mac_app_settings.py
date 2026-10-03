"""Settings → Mac App (aso/settings_views.py settings_mac_app_view).

The page exists only inside the running Mac app. It always shows the login
item state macOS reports, never a stored guess, and each button does what it
says: turning on, turning off, opening System Settings. Every sentence obeys
the no-dash rule.
"""

from django.test import TestCase
from django.urls import reverse

from aso import desktop_bridge
from aso.tests.surfaces import visible_text
from aso.tests.test_desktop_bridge import FakeBackend
from aso.tests.test_no_dashes import _line_dashes

URL = "aso:settings_mac_app"

STATE_TEXT = {
    "enabled": ("On.", ["login_off"]),
    "disabled": ("Off.", ["login_on"]),
    "requires_approval": ("Waiting for your approval.", ["open_login_items", "login_off"]),
    "not_in_applications": ("Not from here.", []),
    "needs_newer_macos": ("Needs macOS 13 or later.", []),
    "unavailable": ("Only in the installed app.", []),
}


class MacAppPageTest(TestCase):
    def tearDown(self):
        desktop_bridge.unregister_backend()

    def test_the_page_does_not_exist_without_the_mac_app(self):
        desktop_bridge.unregister_backend()
        self.assertEqual(self.client.get(reverse(URL)).status_code, 404)

    def test_every_state_shows_its_own_sentence_and_buttons(self):
        self.assertEqual(set(STATE_TEXT), set(desktop_bridge.LOGIN_ITEM_STATES))
        for state, (lead, actions) in STATE_TEXT.items():
            with self.subTest(state=state):
                desktop_bridge.register_backend(FakeBackend(state=state))
                response = self.client.get(reverse(URL))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, f'data-state="{state}"')
                self.assertContains(response, lead)
                for action in ("login_on", "login_off", "open_login_items"):
                    shown = f'value="{action}"' in response.content.decode()
                    self.assertEqual(shown, action in actions, action)
                self.assertEqual(_line_dashes(visible_text(response.content.decode())), [])

    def test_turning_on(self):
        backend = FakeBackend(state="disabled", after_set="enabled")
        desktop_bridge.register_backend(backend)
        response = self.client.post(reverse(URL), {"action": "login_on"})
        self.assertEqual(backend.set_calls, [True])
        self.assertContains(response, "RespectASO will open in the menu bar when you log in.")
        self.assertContains(response, 'data-state="enabled"')

    def test_turning_on_that_needs_approval(self):
        desktop_bridge.register_backend(FakeBackend(state="disabled", after_set="requires_approval"))
        response = self.client.post(reverse(URL), {"action": "login_on"})
        self.assertContains(response, "One more step: approve RespectASO in System Settings")
        self.assertContains(response, 'value="open_login_items"')

    def test_turning_on_that_fails(self):
        desktop_bridge.register_backend(FakeBackend(state="disabled", after_set="disabled"))
        response = self.client.post(reverse(URL), {"action": "login_on"})
        self.assertContains(response, "macOS did not add RespectASO to your login items.")

    def test_turning_off(self):
        backend = FakeBackend(state="enabled", after_set="disabled")
        desktop_bridge.register_backend(backend)
        response = self.client.post(reverse(URL), {"action": "login_off"})
        self.assertEqual(backend.set_calls, [False])
        self.assertContains(response, "RespectASO no longer opens at login.")

    def test_turning_off_that_fails(self):
        desktop_bridge.register_backend(FakeBackend(state="enabled", after_set="enabled"))
        response = self.client.post(reverse(URL), {"action": "login_off"})
        self.assertContains(response, "macOS did not remove RespectASO from your login items.")

    def test_opening_login_items(self):
        backend = FakeBackend(state="requires_approval")
        desktop_bridge.register_backend(backend)
        response = self.client.post(reverse(URL), {"action": "open_login_items"})
        self.assertEqual(backend.opened, 1)
        self.assertContains(response, "System Settings is open at Login Items.")

    def test_the_mac_app_tab_appears_only_in_the_mac_app(self):
        desktop_bridge.register_backend(FakeBackend())
        self.assertContains(self.client.get(reverse("aso:settings_popularity")), "Mac App</a>")
        desktop_bridge.unregister_backend()
        self.assertNotContains(self.client.get(reverse("aso:settings_popularity")), "Mac App</a>")
