"""The Mac app's native services as Django code sees them (aso/desktop_bridge.py).

Without the Mac app (Docker, runserver, the MCP server, this test suite)
every answer is safe: nothing shown, nothing sent, nothing raised. With it,
the backend's answers pass through, and a backend that fails or answers
nonsense still never reaches a page as an exception. The rules that turn a
macOS status into what the app says are tested here on any machine, and so
is every sentence the Mac app shows natively.
"""

from pathlib import Path

from django.test import SimpleTestCase

from aso import desktop_bridge
from aso.copy_rules import dash_punctuation_in


class FakeBackend:
    """Answers like the Mac app would; records what it was asked."""

    def __init__(self, *, permission="granted", state="disabled", after_set="enabled"):
        self.permission = permission
        self.state = state
        self.after_set = after_set
        self.sent = []
        self.requested = 0
        self.set_calls = []
        self.opened = 0

    def send_notification(self, title, body):
        self.sent.append((title, body))
        return self.permission == "granted"

    def notification_permission(self):
        return self.permission

    def request_notification_permission(self):
        self.requested += 1

    def login_item_state(self):
        return self.state

    def set_login_item(self, enabled):
        self.set_calls.append(enabled)
        self.state = self.after_set
        return self.state

    def open_login_items_settings(self):
        self.opened += 1


class BrokenBackend:
    def __getattr__(self, name):
        def fail(*args):
            raise RuntimeError(f"{name} failed")
        return fail


class NonsenseBackend:
    def notification_permission(self):
        return "maybe"

    def login_item_state(self):
        return "sort of"

    def set_login_item(self, enabled):
        return 42


class WithoutTheMacAppTest(SimpleTestCase):
    def setUp(self):
        desktop_bridge.unregister_backend()

    def test_every_answer_is_safe(self):
        self.assertFalse(desktop_bridge.is_desktop_app())
        self.assertFalse(desktop_bridge.send_notification("Title", "Body"))
        self.assertEqual(desktop_bridge.notification_permission(), "unavailable")
        self.assertIsNone(desktop_bridge.request_notification_permission())
        self.assertEqual(desktop_bridge.login_item_state(), "unavailable")
        self.assertEqual(desktop_bridge.set_login_item(True), "unavailable")
        self.assertIsNone(desktop_bridge.open_login_items_settings())


class WithTheMacAppTest(SimpleTestCase):
    def tearDown(self):
        desktop_bridge.unregister_backend()

    def test_the_backend_answers_pass_through(self):
        backend = FakeBackend(permission="granted", state="disabled", after_set="enabled")
        desktop_bridge.register_backend(backend)
        self.assertTrue(desktop_bridge.is_desktop_app())
        self.assertTrue(desktop_bridge.send_notification("Title", "Body"))
        self.assertEqual(backend.sent, [("Title", "Body")])
        self.assertEqual(desktop_bridge.notification_permission(), "granted")
        desktop_bridge.request_notification_permission()
        self.assertEqual(backend.requested, 1)
        self.assertEqual(desktop_bridge.login_item_state(), "disabled")
        self.assertEqual(desktop_bridge.set_login_item(True), "enabled")
        self.assertEqual(backend.set_calls, [True])
        desktop_bridge.open_login_items_settings()
        self.assertEqual(backend.opened, 1)

    def test_no_permission_means_nothing_sent(self):
        desktop_bridge.register_backend(FakeBackend(permission="denied"))
        self.assertFalse(desktop_bridge.send_notification("Title", "Body"))

    def test_a_failing_backend_never_raises(self):
        desktop_bridge.register_backend(BrokenBackend())
        with self.assertLogs("aso.desktop_bridge", level="ERROR"):
            self.assertFalse(desktop_bridge.send_notification("Title", "Body"))
        with self.assertLogs("aso.desktop_bridge", level="ERROR"):
            self.assertEqual(desktop_bridge.notification_permission(), "unavailable")
        with self.assertLogs("aso.desktop_bridge", level="ERROR"):
            self.assertEqual(desktop_bridge.set_login_item(True), "unavailable")
        with self.assertLogs("aso.desktop_bridge", level="ERROR"):
            desktop_bridge.open_login_items_settings()

    def test_an_unknown_answer_reads_as_unavailable(self):
        desktop_bridge.register_backend(NonsenseBackend())
        self.assertEqual(desktop_bridge.notification_permission(), "unavailable")
        self.assertEqual(desktop_bridge.login_item_state(), "unavailable")
        self.assertEqual(desktop_bridge.set_login_item(False), "unavailable")


class RulesTest(SimpleTestCase):
    def test_permission_from_every_macos_status(self):
        cases = {0: "not_determined", 1: "denied", 2: "granted", 3: "granted", 4: "granted", 99: "unavailable"}
        for status, expected in cases.items():
            with self.subTest(status=status):
                self.assertEqual(desktop_bridge.permission_from(status), expected)

    def test_applications_folder(self):
        home = Path("/Users/someone")
        self.assertTrue(desktop_bridge.in_applications_folder("/Applications/RespectASO.app", home))
        self.assertTrue(desktop_bridge.in_applications_folder("/Applications/Tools/RespectASO.app", home))
        self.assertTrue(desktop_bridge.in_applications_folder("/Users/someone/Applications/RespectASO.app", home))
        self.assertFalse(desktop_bridge.in_applications_folder("/Volumes/RespectASO/RespectASO.app", home))
        self.assertFalse(desktop_bridge.in_applications_folder("/Users/someone/Downloads/RespectASO.app", home))
        self.assertFalse(desktop_bridge.in_applications_folder(
            "/private/var/folders/xy/T/AppTranslocation/ABC/d/RespectASO.app", home))
        self.assertFalse(desktop_bridge.in_applications_folder("/Applications Old/RespectASO.app", home))

    def test_login_item_state_from(self):
        app = "/Applications/RespectASO.app"
        cases = [
            ({"bundled": False, "macos_major": 26, "bundle_path": app, "status": 1}, "unavailable"),
            ({"bundled": True, "macos_major": 12, "bundle_path": app, "status": None}, "needs_newer_macos"),
            ({"bundled": True, "macos_major": 26, "bundle_path": "/Volumes/R/RespectASO.app", "status": 1},
             "not_in_applications"),
            ({"bundled": True, "macos_major": 26, "bundle_path": app, "status": 1}, "enabled"),
            ({"bundled": True, "macos_major": 26, "bundle_path": app, "status": 2}, "requires_approval"),
            ({"bundled": True, "macos_major": 26, "bundle_path": app, "status": 0}, "disabled"),
            ({"bundled": True, "macos_major": 26, "bundle_path": app, "status": 3}, "disabled"),
            ({"bundled": True, "macos_major": 13, "bundle_path": app, "status": 0}, "disabled"),
        ]
        for kwargs, expected in cases:
            with self.subTest(**kwargs):
                self.assertEqual(desktop_bridge.login_item_state_from(**kwargs), expected)

    def test_login_question_decision(self):
        cases = {
            "disabled": "ask",
            "enabled": "settled",
            "requires_approval": "settled",
            "not_in_applications": "not_yet",
            "needs_newer_macos": "not_yet",
            "unavailable": "not_yet",
        }
        self.assertEqual(set(cases), set(desktop_bridge.LOGIN_ITEM_STATES))
        for state, expected in cases.items():
            with self.subTest(state=state):
                self.assertEqual(desktop_bridge.login_question_decision(state), expected)


class NativeCopyTest(SimpleTestCase):
    def test_no_dash_in_anything_the_mac_app_shows(self):
        for key, text in desktop_bridge.COPY.items():
            with self.subTest(key=key):
                self.assertEqual(dash_punctuation_in(text), "", text)
