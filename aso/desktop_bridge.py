"""The Mac app's native services, for Django code that runs in every edition.

The Mac app registers a backend at startup: desktop/main.py hands over the
object desktop/mac_integration.py builds. Docker, runserver, the MCP server
and the test suite never register one, so every function here has a safe
answer without it: nothing is shown, nothing is sent, and nothing raises.

The rules behind what the Mac app says and does live here as plain Python,
so they are tested on any machine: which login item state a macOS status
means, whether to ask the login question, and the words of every native
alert and menu item. desktop/mac_integration.py is only the Cocoa glue that
calls them.

Ships in the free-tier aso app: it must not import AppKit, aso_pro or
licensing.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

PERMISSIONS = ("granted", "denied", "not_determined", "unavailable")

LOGIN_ITEM_STATES = (
    "enabled",  # macOS opens RespectASO at login
    "disabled",  # it does not, and turning it on is possible
    "requires_approval",  # registered, waiting for the user in System Settings
    "not_in_applications",  # running from the disk image, Downloads or elsewhere
    "needs_newer_macos",  # macOS 12 has no SMAppService
    "unavailable",  # not the installed app: a source run, Docker, the tests
)

MIN_LOGIN_ITEM_MACOS = 13

# SMAppService.Status raw values. 0 (not registered) and 3 (not found) both
# mean "off": an app that never registered reads 3 on macOS 26 (spike of
# 2026-09-30), and registering from there works.
_SM_ENABLED = 1
_SM_REQUIRES_APPROVAL = 2

# UNAuthorizationStatus raw values.
_UN_NOT_DETERMINED = 0
_UN_DENIED = 1
_UN_GRANTED = (2, 3, 4)  # authorized, provisional, ephemeral

# Every sentence the Mac app shows natively: its alerts and its menu bar
# menu. Kept here so the no-dash rule reads them on any machine
# (aso/tests/test_desktop_bridge.py).
COPY = {
    "first_close_title": "RespectASO is still running in the menu bar",
    "first_close_text": (
        "Closing the window does not stop it, so your tracked keywords keep "
        "refreshing once a day. Click the chart icon in the menu bar to open "
        "RespectASO again or to quit it."
    ),
    "first_close_button": "OK",
    "login_question_title": "Open RespectASO when you log in?",
    "login_question_text": (
        "Your tracked keywords refresh once a day while RespectASO runs. "
        "At login it opens quietly in the menu bar; change this any time in "
        "Settings → Mac App."
    ),
    "login_question_yes": "Open at Login",
    "login_question_no": "Not Now",
    "approval_title": "Approve RespectASO in System Settings",
    "approval_text": (
        "macOS asks you to confirm new login items. In the window that opens, "
        "turn on RespectASO under Open at Login."
    ),
    "approval_open": "Open Login Items",
    "approval_later": "Later",
    "failed_title": "RespectASO could not be added to your login items",
    "failed_text": (
        "Try again in Settings → Mac App. You can also add it yourself in "
        "System Settings → General → Login Items."
    ),
    "failed_button": "OK",
    "menu_open": "Open RespectASO",
    "menu_refresh": "Update All Tracked Keywords",
    "menu_quit": "Quit RespectASO",
    "selftest_title": "RespectASO notifications work",
    "selftest_body": "This is the test notification you asked for.",
}

_backend = {"current": None}


def register_backend(backend) -> None:
    """Called once by desktop/main.py, before the window opens."""
    _backend["current"] = backend


def unregister_backend() -> None:
    """For tests: back to the answers of a process without the Mac app."""
    _backend["current"] = None


def is_desktop_app() -> bool:
    """True only inside the running Mac app."""
    return _backend["current"] is not None


def _call(method: str, default, *args):
    backend = _backend["current"]
    if backend is None:
        return default
    try:
        return getattr(backend, method)(*args)
    except Exception:
        logger.exception("The Mac app could not %s", method.replace("_", " "))
        return default


def send_notification(title: str, body: str) -> bool:
    """Show a macOS notification. True when macOS took it; False without the
    Mac app or without the user's permission."""
    return bool(_call("send_notification", False, title, body))


def notification_permission() -> str:
    """One of PERMISSIONS: "unavailable" without the Mac app."""
    value = _call("notification_permission", "unavailable")
    return value if value in PERMISSIONS else "unavailable"


def request_notification_permission() -> None:
    """Ask macOS to show its permission question (once per install; after
    that macOS answers from the user's choice without asking)."""
    _call("request_notification_permission", None)


def login_item_state() -> str:
    """One of LOGIN_ITEM_STATES, read from macOS every time, never stored."""
    value = _call("login_item_state", "unavailable")
    return value if value in LOGIN_ITEM_STATES else "unavailable"


def set_login_item(enabled: bool) -> str:
    """Turn opening at login on or off; returns the state macOS reports after."""
    value = _call("set_login_item", "unavailable", bool(enabled))
    return value if value in LOGIN_ITEM_STATES else "unavailable"


def open_login_items_settings() -> None:
    """Open System Settings at General → Login Items."""
    _call("open_login_items_settings", None)


# ---- the rules, pure -------------------------------------------------------


def permission_from(status: int) -> str:
    """The PERMISSIONS value for a UNAuthorizationStatus raw value."""
    if status == _UN_NOT_DETERMINED:
        return "not_determined"
    if status == _UN_DENIED:
        return "denied"
    if status in _UN_GRANTED:
        return "granted"
    return "unavailable"


def in_applications_folder(bundle_path: str, home: Path | None = None) -> bool:
    """Whether the app runs from /Applications or ~/Applications (or a folder
    inside either): macOS should only open an app at login from a place it
    stays, never from a disk image, Downloads or a translocated copy."""
    folders = (Path("/Applications"), (home or Path.home()) / "Applications")
    return any(parent in folders for parent in Path(bundle_path).parents)


def login_item_state_from(*, bundled: bool, macos_major: int, bundle_path: str,
                          status: int | None) -> str:
    """The LOGIN_ITEM_STATES value for what the running app knows about itself."""
    if not bundled:
        return "unavailable"
    if macos_major < MIN_LOGIN_ITEM_MACOS:
        return "needs_newer_macos"
    if not in_applications_folder(bundle_path):
        return "not_in_applications"
    if status == _SM_ENABLED:
        return "enabled"
    if status == _SM_REQUIRES_APPROVAL:
        return "requires_approval"
    return "disabled"


def login_question_decision(state: str) -> str:
    """What the first launch does with the login question for a state.

    "ask": show it (and never again after an answer). "settled": RespectASO
    already opens at login, or waits for approval, so the question is marked
    answered without showing it. "not_yet": it cannot be answered from here
    (a copy outside Applications, an old macOS, a source run), so it waits
    for a launch where it can.
    """
    if state == "disabled":
        return "ask"
    if state in ("enabled", "requires_approval"):
        return "settled"
    return "not_yet"
