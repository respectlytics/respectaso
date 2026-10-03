"""The Mac app's native side: closing to the menu bar, the menu bar menu,
opening at login, catching up after sleep, and notifications.

desktop/main.py imports this module inside main(), once Django is set up,
and only in the Mac app: it needs AppKit, which the Docker image and the
public test suite do not have. Django code reaches it only through
aso/desktop_bridge.py, where the rules and every sentence live and are
tested; this module is the Cocoa glue that calls them.

Proven on macOS 26.6 with pywebview 5.4 and PyObjC 12.2.1 (spike of
2026-09-30, docs/development/ALWAYS_RUNNING_PLAN.md section 2): pywebview
builds its app delegate from cocoa.BrowserView.AppDelegate, so a subclass
swapped in before webview.start() receives every launch, reopen and quit.
"""

from __future__ import annotations

import logging
import os
import platform
import threading
import time
import uuid

import AppKit
import Foundation
import objc
from PyObjCTools import AppHelper
from webview.platforms import cocoa

from aso import desktop_bridge, scheduler, ui_state

logger = logging.getLogger(__name__)

BUNDLE_ID = "com.respectlytics.respectaso"
MENU_BAR_SYMBOL = "chart.line.uptrend.xyaxis"
WAKE_SETTLE_SECONDS = 60
LOGIN_QUESTION_DELAY_SECONDS = 2.0
MAIN_THREAD_TIMEOUT_SECONDS = 10.0
PERMISSION_TIMEOUT_SECONDS = 3.0
NOTIFICATION_SELFTEST_ENV = "RESPECTASO_NOTIFICATION_SELFTEST"

_K_AE_OPEN_APPLICATION = 0x6F617070  # 'oapp'
_KEY_AE_PROP_DATA = 0x70726F70  # 'prop'
_KEY_AE_LAUNCHED_AS_LOGIN_ITEM = 0x6C676974  # 'lgit'

# The one MacApp of this process, set by install().
_current: dict = {"app": None, "notification_delegate_class": None}


def _app() -> MacApp | None:
    return _current["app"]


def _guarded(fn, *args):
    """Run a piece of Cocoa glue; a failure is logged and never reaches AppKit."""
    try:
        return fn(*args)
    except Exception:
        logger.exception("Mac app step %s failed", getattr(fn, "__name__", fn))
        return None


def _on_main_thread(fn):
    """Run fn on the main thread and return its result, from any thread."""
    if Foundation.NSThread.isMainThread():
        return fn()
    done = threading.Event()
    box: dict = {}

    def run():
        try:
            box["value"] = fn()
        except Exception as error:  # noqa: BLE001 (handed back to the calling thread below)
            box["error"] = error
        finally:
            done.set()

    AppHelper.callAfter(run)
    if not done.wait(MAIN_THREAD_TIMEOUT_SECONDS):
        raise TimeoutError("the main thread did not answer")
    if "error" in box:
        raise box["error"]
    return box.get("value")


def _alert(title: str, text: str, buttons: list[str]) -> int:
    """A native alert in front of every window; the index of the clicked button."""
    alert = AppKit.NSAlert.alloc().init()
    alert.setMessageText_(title)
    alert.setInformativeText_(text)
    for label in buttons:
        alert.addButtonWithTitle_(label)
    AppKit.NSApp.activateIgnoringOtherApps_(True)
    return int(alert.runModal()) - AppKit.NSAlertFirstButtonReturn


def _launched_as_login_item() -> bool:
    """Whether macOS opened the app at login. Only valid while
    applicationDidFinishLaunching_ runs: the launch Apple event carries the
    login marker then (the same check as LaunchAtLogin-Modern)."""
    event = Foundation.NSAppleEventManager.sharedAppleEventManager().currentAppleEvent()
    if event is None or event.eventID() != _K_AE_OPEN_APPLICATION:
        return False
    prop = event.paramDescriptorForKeyword_(_KEY_AE_PROP_DATA)
    return prop is not None and prop.enumCodeValue() == _KEY_AE_LAUNCHED_AS_LOGIN_ITEM


class RespectASOAppDelegate(cocoa.BrowserView.AppDelegate):
    """pywebview's app delegate, plus what the Mac app needs from macOS."""

    def applicationDidFinishLaunching_(self, notification):
        app = _app()
        if app is not None:
            _guarded(app.did_finish_launching)

    def applicationShouldTerminate_(self, sender):
        # Every real quit passes here: ⌘Q, the menu bar's Quit, the Dock's
        # Quit, logout and shutdown. Marking it first lets the window close
        # instead of hiding (MacApp.on_closing), so nothing blocks a logout.
        app = _app()
        if app is not None:
            app.quitting = True
        return Foundation.YES

    def applicationShouldHandleReopen_hasVisibleWindows_(self, sender, has_visible_windows):
        # A Dock click, a Finder or Spotlight open, or `open -a` while running.
        app = _app()
        if app is not None:
            _guarded(app.show_window)
        return Foundation.NO


class RespectASOMenuTarget(AppKit.NSObject):
    """Receives the menu bar menu's actions and the wake notification."""

    def openWindow_(self, sender):
        _guarded(_app().show_window)

    def refreshNow_(self, sender):
        _guarded(_app().refresh_now)

    def quitApp_(self, sender):
        AppKit.NSApp.terminate_(None)

    def menuNeedsUpdate_(self, menu):
        _guarded(_app().update_menu)

    def didWake_(self, notification):
        _guarded(_app().did_wake)


def _new_notification_delegate():
    """A UNUserNotificationCenter delegate: banners also while RespectASO is in
    front, and a click on one brings the window forward. The class is made on
    first use because UserNotifications is only imported inside the app bundle."""
    import UserNotifications as UN

    if _current["notification_delegate_class"] is None:

        class RespectASONotificationDelegate(
            AppKit.NSObject, protocols=[objc.protocolNamed("UNUserNotificationCenterDelegate")]
        ):
            def userNotificationCenter_willPresentNotification_withCompletionHandler_(
                self, center, notification, handler
            ):
                handler(UN.UNNotificationPresentationOptionBanner | UN.UNNotificationPresentationOptionList)

            def userNotificationCenter_didReceiveNotificationResponse_withCompletionHandler_(
                self, center, response, handler
            ):
                app = _app()
                if app is not None:
                    AppHelper.callAfter(_guarded, app.show_window)
                handler()

        _current["notification_delegate_class"] = RespectASONotificationDelegate
    return _current["notification_delegate_class"].alloc().init()


class MacApp:
    """The one per process: the window's close behaviour, the menu bar menu,
    and the answers aso/desktop_bridge.py gets from macOS (it is the bridge's
    backend)."""

    def __init__(self, window):
        self.window = window
        self.quitting = False
        self.launched_at_login = False
        bundle = Foundation.NSBundle.mainBundle()
        # Only the installed app bundle has an identifier. Touching
        # UNUserNotificationCenter without one aborts the whole process
        # (uncaught NSException, spike of 2026-09-30), so every notification
        # path checks this first.
        self.bundled = bundle.bundleIdentifier() == BUNDLE_ID
        self.bundle_path = str(bundle.bundlePath())
        self.macos_major = int(platform.mac_ver()[0].split(".")[0] or 0)
        self._target = RespectASOMenuTarget.alloc().init()
        self._status_item = None
        self._status_line = None
        self._refresh_item = None
        self._notification_delegate = None

    # ---- launch ------------------------------------------------------------

    def did_finish_launching(self):
        self.launched_at_login = _launched_as_login_item()
        logger.info("Mac app started. Launched at login: %s", self.launched_at_login)
        if self.bundled:
            self._notification_delegate = _new_notification_delegate()
            self._center().setDelegate_(self._notification_delegate)
        self._install_status_item()
        AppKit.NSWorkspace.sharedWorkspace().notificationCenter().addObserver_selector_name_object_(
            self._target, "didWake:", AppKit.NSWorkspaceDidWakeNotification, None
        )
        if self.launched_at_login:
            AppKit.NSApp.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
        else:
            self.show_window()
            AppHelper.callLater(LOGIN_QUESTION_DELAY_SECONDS, _guarded, self.maybe_ask_login_item)
        if os.environ.get(NOTIFICATION_SELFTEST_ENV) == "1":
            threading.Thread(target=_guarded, args=(self._notification_selftest,),
                             daemon=True, name="notification-selftest").start()

    # ---- the window --------------------------------------------------------

    def on_closing(self):
        """pywebview's closing event, on the main thread. True lets the window
        close, which only a real quit does; False keeps the window and hides it."""
        if self.quitting:
            return True
        try:
            if ui_state.is_dismissed(ui_state.FIRST_CLOSE_NOTICE):
                self.hide_window()
            else:
                ui_state.dismiss(ui_state.FIRST_CLOSE_NOTICE)
                AppHelper.callAfter(_guarded, self._first_close_notice)
        except Exception:
            logger.exception("Closing to the menu bar failed; hiding the window")
            self.window.native.orderOut_(None)
        return False

    def _first_close_notice(self):
        copy = desktop_bridge.COPY
        _alert(copy["first_close_title"], copy["first_close_text"], [copy["first_close_button"]])
        self.hide_window()

    def hide_window(self):
        self.window.native.orderOut_(None)
        AppKit.NSApp.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)

    def show_window(self):
        AppKit.NSApp.setActivationPolicy_(AppKit.NSApplicationActivationPolicyRegular)
        self.window.show()

    # ---- the menu bar menu -------------------------------------------------

    def _install_status_item(self):
        copy = desktop_bridge.COPY
        item = AppKit.NSStatusBar.systemStatusBar().statusItemWithLength_(AppKit.NSVariableStatusItemLength)
        image = AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_(MENU_BAR_SYMBOL, "RespectASO")
        image.setTemplate_(True)
        item.button().setImage_(image)
        item.button().setToolTip_("RespectASO")

        menu = AppKit.NSMenu.alloc().init()
        menu.setAutoenablesItems_(False)
        menu.setDelegate_(self._target)
        menu.addItemWithTitle_action_keyEquivalent_(copy["menu_open"], "openWindow:", "").setTarget_(self._target)
        menu.addItem_(AppKit.NSMenuItem.separatorItem())
        self._status_line = menu.addItemWithTitle_action_keyEquivalent_("", None, "")
        self._status_line.setEnabled_(False)
        self._refresh_item = menu.addItemWithTitle_action_keyEquivalent_(copy["menu_refresh"], "refreshNow:", "")
        self._refresh_item.setTarget_(self._target)
        menu.addItem_(AppKit.NSMenuItem.separatorItem())
        menu.addItemWithTitle_action_keyEquivalent_(copy["menu_quit"], "quitApp:", "").setTarget_(self._target)
        item.setMenu_(menu)
        self._status_item = item
        self.update_menu()

    def update_menu(self):
        """Called by macOS each time the menu opens, so it is always current."""
        line, can_refresh, refresh_title = scheduler.menu_status()
        self._status_line.setTitle_(line)
        self._refresh_item.setTitle_(refresh_title)
        self._refresh_item.setEnabled_(can_refresh)

    def refresh_now(self):
        outcome, message = scheduler.start_bulk_refresh(scheduler.bulk_refresh_pairs())
        logger.info("Menu bar refresh: %s. %s", outcome, message)

    # ---- sleep -------------------------------------------------------------

    def did_wake(self):
        # The scheduler's hourly wait does not count time asleep, so a missed
        # daily refresh could wait up to an hour after the lid opens. Nudge it
        # once the network has had a minute to come back.
        logger.info("Mac woke: checking for a due refresh in %s seconds.", WAKE_SETTLE_SECONDS)
        timer = threading.Timer(WAKE_SETTLE_SECONDS, scheduler.nudge)
        timer.daemon = True
        timer.start()

    # ---- opening at login (the bridge's backend) ---------------------------

    def _service(self):
        import ServiceManagement

        return ServiceManagement.SMAppService.mainAppService()

    def login_item_state(self) -> str:
        status = None
        if self.bundled and self.macos_major >= desktop_bridge.MIN_LOGIN_ITEM_MACOS:
            status = int(_on_main_thread(lambda: self._service().status()))
        return desktop_bridge.login_item_state_from(
            bundled=self.bundled, macos_major=self.macos_major,
            bundle_path=self.bundle_path, status=status,
        )

    def set_login_item(self, enabled: bool) -> str:
        state = self.login_item_state()
        if state not in ("enabled", "disabled", "requires_approval"):
            return state

        def change():
            service = self._service()
            if enabled:
                return service.registerAndReturnError_(None)
            return service.unregisterAndReturnError_(None)

        ok, error = _on_main_thread(change)
        if not ok:
            logger.warning("Could not %s the login item: %s", "add" if enabled else "remove", error)
        new_state = self.login_item_state()
        logger.info("Open at login is now: %s", new_state)
        return new_state

    def open_login_items_settings(self) -> None:
        import ServiceManagement

        _on_main_thread(ServiceManagement.SMAppService.openSystemSettingsLoginItems)

    def maybe_ask_login_item(self):
        """The one-time question on a normal launch (never at login)."""
        if ui_state.is_dismissed(ui_state.LOGIN_ITEM_QUESTION):
            return
        decision = desktop_bridge.login_question_decision(self.login_item_state())
        if decision == "not_yet":
            return
        ui_state.dismiss(ui_state.LOGIN_ITEM_QUESTION)
        if decision == "settled":
            return
        copy = desktop_bridge.COPY
        answer = _alert(copy["login_question_title"], copy["login_question_text"],
                        [copy["login_question_yes"], copy["login_question_no"]])
        logger.info("Login question answered: %s", "open at login" if answer == 0 else "not now")
        if answer != 0:
            return
        state = self.set_login_item(True)
        if state == "requires_approval":
            clicked = _alert(copy["approval_title"], copy["approval_text"],
                             [copy["approval_open"], copy["approval_later"]])
            if clicked == 0:
                self.open_login_items_settings()
        elif state != "enabled":
            _alert(copy["failed_title"], copy["failed_text"], [copy["failed_button"]])

    # ---- notifications (the bridge's backend) ------------------------------

    def _center(self):
        import UserNotifications as UN

        return UN.UNUserNotificationCenter.currentNotificationCenter()

    def notification_permission(self) -> str:
        if not self.bundled:
            return "unavailable"
        done = threading.Event()
        box: dict = {}

        def answered(settings):
            box["status"] = int(settings.authorizationStatus())
            done.set()

        self._center().getNotificationSettingsWithCompletionHandler_(answered)
        if not done.wait(PERMISSION_TIMEOUT_SECONDS):
            return "unavailable"
        return desktop_bridge.permission_from(box["status"])

    def request_notification_permission(self) -> None:
        if not self.bundled:
            return
        import UserNotifications as UN

        def answered(granted, error):
            logger.info("Notification permission answered: granted=%s", bool(granted))

        self._center().requestAuthorizationWithOptions_completionHandler_(UN.UNAuthorizationOptionAlert, answered)

    def send_notification(self, title: str, body: str) -> bool:
        if self.notification_permission() != "granted":
            return False
        import UserNotifications as UN

        content = UN.UNMutableNotificationContent.alloc().init()
        content.setTitle_(title)
        content.setBody_(body)
        request = UN.UNNotificationRequest.requestWithIdentifier_content_trigger_(str(uuid.uuid4()), content, None)

        def delivered(error):
            if error is not None:
                logger.warning("macOS did not show a notification: %s", error)

        self._center().addNotificationRequest_withCompletionHandler_(request, delivered)
        return True

    def _notification_selftest(self):
        """The packaged app's manual check (ALWAYS_RUNNING_PLAN.md, step M10):
        ask for permission when macOS has not asked yet, then send one
        notification, and log the outcome to respectaso.log."""
        if self.notification_permission() == "not_determined":
            self.request_notification_permission()
            deadline = time.monotonic() + 120
            while self.notification_permission() == "not_determined" and time.monotonic() < deadline:
                time.sleep(1)
        copy = desktop_bridge.COPY
        sent = self.send_notification(copy["selftest_title"], copy["selftest_body"])
        logger.info("Notification self test: permission=%s, sent=%s", self.notification_permission(), sent)


def install(window) -> MacApp:
    """Wire the Mac app into pywebview and macOS. Call once, after
    webview.create_window() and before webview.start(): pywebview builds its
    app delegate from cocoa.BrowserView.AppDelegate when the window is made."""
    app = MacApp(window)
    _current["app"] = app
    cocoa.BrowserView.AppDelegate = RespectASOAppDelegate
    window.events.closing += app.on_closing
    desktop_bridge.register_backend(app)
    return app
