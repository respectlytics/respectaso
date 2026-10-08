"""
RespectASO — Native macOS App Entry Point

Opens a native WebKit window via pywebview at once, on a loading page,
while the data is prepared and the Django server starts in the background;
then the app replaces the loading page. Data is stored in
~/Library/Application Support/RespectASO/.

Closing the window hides it and RespectASO keeps running in the menu bar;
desktop/mac_integration.py holds that native side.
"""

import os
import socket
import sys
import threading
import time
from pathlib import Path

INSTANCE_LOCK = ".instance.lock"

# The open lock file of acquire_instance_lock(), held until the process ends.
_held: dict = {}


def get_base_dir():
    """Return the base directory containing the Django project.

    When running from a PyInstaller bundle, sys._MEIPASS points to the
    temporary extraction folder. Otherwise, use the repo root.
    """
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent.parent


def get_data_dir():
    """Return ~/Library/Application Support/RespectASO/, creating it if needed."""
    data_dir = Path.home() / "Library" / "Application Support" / "RespectASO"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def acquire_instance_lock(data_dir):
    """Hold <data dir>/.instance.lock for as long as this process runs, with
    this process's id written in it.

    One data folder, one running app: two copies of RespectASO (one in
    Applications and one still on the disk image, or a source run next to
    the installed app) would both run the daily refresh on one database.
    Returns the open lock file, or None when another process holds it.
    """
    import fcntl

    handle = open(data_dir / INSTANCE_LOCK, "a+")  # noqa: SIM115 (held open for the life of the process)
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    handle.truncate(0)
    handle.write(str(os.getpid()))
    handle.flush()
    return handle


def hand_over_to_running_instance(data_dir):
    """Another RespectASO already runs on this data folder: bring that very
    process's window forward through LaunchServices (the same reopen a Dock
    click sends), then stop this one. The holder is found by the process id
    in the lock file, never by bundle identifier: another copy of the app on
    another data folder must not be the one that comes forward."""
    import subprocess

    try:
        holder = int((data_dir / INSTANCE_LOCK).read_text().strip() or 0)
    except (OSError, ValueError):
        holder = 0
    try:
        from AppKit import NSRunningApplication  # type: ignore[attr-defined]
    except ImportError:
        NSRunningApplication = None
    running = None
    if NSRunningApplication is not None and holder:
        running = NSRunningApplication.runningApplicationWithProcessIdentifier_(holder)
    if running is not None and running.bundleURL() is not None:
        subprocess.run(["/usr/bin/open", "-a", running.bundleURL().path()], check=False)
    print("RespectASO is already running.", file=sys.stderr)
    sys.exit(0)


def ensure_secret_key(data_dir):
    """Generate and persist a Django SECRET_KEY on first launch."""
    key_file = data_dir / ".secret_key"
    if key_file.exists():
        return key_file.read_text().strip()

    from django.core.management.utils import get_random_secret_key

    key = get_random_secret_key()
    key_file.write_text(key)
    return key


def find_free_port():
    """Find an available TCP port on localhost in the 8000–8099 range.

    CSRF_TRUSTED_ORIGINS in core/settings.py is pre-configured for this
    range. Falls back to any available port if all 100 ports are taken.
    """
    for port in range(8000, 8100):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", port))
                return port
        except OSError:
            continue
    # Fallback: let the OS pick any available port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def run_server(port):
    """Start a threaded WSGI server for the Django application.

    Uses Python's built-in wsgiref with ThreadingMixIn so the server can
    handle multiple requests concurrently. This prevents long-running
    requests (e.g. opportunity search) from blocking page navigation.
    """
    from socketserver import ThreadingMixIn
    from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

    from core.wsgi import application

    class ThreadingWSGIServer(ThreadingMixIn, WSGIServer):
        daemon_threads = True

    class QuietHandler(WSGIRequestHandler):
        """Suppress per-request log lines."""
        def log_request(self, *args, **kwargs):
            pass

    httpd = make_server(
        "127.0.0.1", port, application,
        server_class=ThreadingWSGIServer,
        handler_class=QuietHandler,
    )
    httpd.serve_forever()


def wait_for_server(port, timeout=30):
    """Block until the Django server is accepting connections."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def configure_environment(data_dir):
    """The environment Django starts in inside the Mac app. DEBUG is off, so a
    user sees RespectASO's own error pages (aso/error_views.py), never
    Django's debug page with its traceback; running from source with
    DEBUG=True set still turns it on."""
    os.environ["DJANGO_SETTINGS_MODULE"] = "core.settings"
    os.environ["DATA_DIR"] = str(data_dir)
    os.environ["RESPECTASO_NATIVE"] = "1"
    os.environ.setdefault("DEBUG", "False")


# The loading page's background and the app's (Tailwind bg-slate-900).
STARTUP_BACKGROUND = "#0F172A"


def startup_page(state, error=""):
    """The window's own page while the data is prepared ("starting"), or when
    that failed ("failed", with what went wrong for the email to us):
    aso/templates/loading_standalone.html, with the typeface and the logo
    inline, since no server runs yet. Its sentences live in
    aso/desktop_bridge.py COPY."""
    import base64
    import platform

    from django.conf import settings
    from django.template.loader import render_to_string

    from aso.desktop_bridge import COPY, start_failed_details, start_failed_mail
    from aso.links import CONTACT_EMAIL

    static = Path(settings.BASE_DIR) / "static"

    def inline(name):
        return base64.b64encode((static / name).read_bytes()).decode("ascii")

    details = start_failed_details(settings.VERSION, platform.mac_ver()[0] or "unknown", error)
    log = log_file()
    home = str(Path.home())
    log_path = str(log) if log else ""
    if log_path.startswith(home + "/"):
        log_path = "~" + log_path[len(home):]

    return render_to_string("loading_standalone.html", {
        "state": state,
        "copy": COPY,
        "font_regular": inline("fonts/schibsted-grotesk/SchibstedGrotesk-Regular.woff2"),
        "font_medium": inline("fonts/schibsted-grotesk/SchibstedGrotesk-Medium.woff2"),
        "font_semibold": inline("fonts/schibsted-grotesk/SchibstedGrotesk-SemiBold.woff2"),
        "logo": inline("images/respectaso-logo-64.png"),
        "contact": CONTACT_EMAIL,
        "details": details,
        "log_path": log_path,
        "mail": start_failed_mail(details),
    })


def log_file():
    """The app's log file (core/settings.py LOGGING), or None in a process
    that does not write one."""
    from django.conf import settings

    handler = settings.LOGGING.get("handlers", {}).get("file") or {}
    return Path(handler["filename"]) if handler.get("filename") else None


def show_log_in_finder():
    """The failed start page's Show in Finder button: Finder opens the log's
    folder with the file selected, ready to drag into the email, or just the
    folder when there is no log yet. True when Finder was asked."""
    import subprocess

    from django.conf import settings

    log = log_file()
    if log is not None and log.exists():
        command = ["/usr/bin/open", "-R", str(log)]
    else:
        command = ["/usr/bin/open", str(log.parent if log is not None else settings.DATA_DIR)]
    return subprocess.run(command, check=False).returncode == 0


def prepare_and_serve():
    """Everything the first page waits for, in order. Returns the server's
    port; raises when anything fails or the server does not answer in time.

    Migrate first (aso/disk_space.py converts an older database there, which
    takes seconds on a large one), then the history upgrade and the run queue,
    which need every column, then the server."""
    from django.core.management import call_command

    call_command("migrate", "--no-input", verbosity=0)
    call_command("collectstatic", "--no-input", verbosity=0)

    # Re-score stored history when a version marker bumped, now that every
    # column it reads exists (aso/apps.py leaves this to the Mac app).
    from aso.popularity import start_history_upgrade

    start_history_upgrade()

    # Resume the run queue now that the schema is up to date: a keyword search
    # that was executing when the app was last closed continues from the first
    # keyword that was not finished, AI runs that were executing are marked
    # interrupted (with a Retry offer), and anything still queued starts again.
    # Both editions: the Free build has keyword searches too.
    from aso.run_queue import resume_after_startup

    resume_after_startup()

    port = find_free_port()
    threading.Thread(target=run_server, args=(port,), daemon=True).start()
    if not wait_for_server(port):
        raise RuntimeError("The server did not start within 30 seconds.")
    return port


def start_app(window):
    """Behind the loading page (webview.start runs it on its own thread):
    prepare the data and the server, then open the app in the window. If
    that fails the window says so, the menu bar says so, and the reason is
    in the log; until it is done the menu bar never reads the database
    (aso/desktop_bridge.py startup_state)."""
    import logging

    from aso import desktop_bridge

    logger = logging.getLogger("desktop.main")
    try:
        port = prepare_and_serve()
    except Exception as e:
        logger.exception("RespectASO could not start")
        desktop_bridge.set_startup_state(desktop_bridge.FAILED)
        what = f"{type(e).__name__}: {e}".splitlines()[0][:300]
        window.load_html(startup_page("failed", error=what))
        return
    desktop_bridge.set_startup_state(desktop_bridge.READY)
    window.load_url(f"http://127.0.0.1:{port}")


def main():
    base_dir = get_base_dir()
    data_dir = get_data_dir()

    # Before anything touches the database: a second copy hands over to the
    # running one and stops.
    lock = acquire_instance_lock(data_dir)
    if lock is None:
        hand_over_to_running_instance(data_dir)
    _held["instance_lock"] = lock

    configure_environment(data_dir)

    # Ensure the project root is on sys.path so Django can find modules
    sys.path.insert(0, str(base_dir))

    # Generate / load SECRET_KEY before Django setup
    secret_key = ensure_secret_key(data_dir)
    os.environ["SECRET_KEY"] = secret_key

    # Ensure SSL certificate verification works inside PyInstaller bundle
    import certifi
    os.environ["SSL_CERT_FILE"] = certifi.where()

    # Set up Django: quick. The slow work (migrations, the one-time database
    # conversion after an update, the history upgrade, the server) runs in
    # start_app, behind the window's loading page, so the window opens at once.
    import django

    django.setup()

    from aso import desktop_bridge

    desktop_bridge.set_startup_state(desktop_bridge.STARTING)

    # Set the macOS dock icon (only works outside a PyInstaller bundle)
    icon_path = base_dir / "desktop" / "assets" / "RespectASO.iconset" / "icon_512x512.png"
    if icon_path.exists():
        try:
            from AppKit import NSApplication, NSImage  # type: ignore[attr-defined]

            icon = NSImage.alloc().initWithContentsOfFile_(str(icon_path))
            if icon:
                NSApplication.sharedApplication().setApplicationIconImage_(icon)
        except ImportError:
            pass  # AppKit not available — icon will be set by the .app bundle

    # Open native WebKit window
    import webview

    class Api:
        """Exposed to JavaScript as window.pywebview.api."""

        def save_file(self, filename, content):
            """Show a native Save dialog and write content to the chosen path."""
            result = window.create_file_dialog(  # type: ignore[union-attr]
                webview.SAVE_DIALOG,  # type: ignore[arg-type]
                directory=str(Path.home() / "Downloads"),
                save_filename=filename,
            )
            if result:
                save_path = result if isinstance(result, str) else result[0]
                Path(save_path).write_text(content, encoding="utf-8")
                return save_path
            return None

        def open_external(self, url):
            """Open a URL (http(s)://, mailto:, etc.) in the default system handler.

            Pywebview's WebKit view blocks mailto: and may swallow target=_blank
            links. This delegates to Python's webbrowser module, which on macOS
            uses LaunchServices to route the URL to the right app (browser,
            Mail.app, etc.).
            """
            import webbrowser

            if not isinstance(url, str) or not url:
                return False
            try:
                webbrowser.open(url)
                return True
            except Exception:  # noqa: BLE001 (the page gets False and shows its own message)
                return False

        def show_log(self):
            """The failed start page's Show log file button."""
            return show_log_in_finder()

        def copy_to_clipboard(self, text):
            """Copy text to the system clipboard.

            Pywebview's WebKit view blocks both the modern Clipboard API and the
            legacy document.execCommand('copy') on http://localhost (non-secure
            context), so JS-side copy fails silently. This native bridge uses
            macOS NSPasteboard via AppKit, which always works.
            """
            if not isinstance(text, str):
                return False
            try:
                from AppKit import (  # type: ignore[import-not-found]
                    NSPasteboard,
                    NSPasteboardTypeString,
                )

                pb = NSPasteboard.generalPasteboard()
                pb.clearContents()
                pb.setString_forType_(text, NSPasteboardTypeString)
                return True
            except Exception:  # noqa: BLE001 (the page gets False and shows its own message)
                return False

    api = Api()
    window = webview.create_window(
        "RespectASO",
        html=startup_page("starting"),
        width=1280,
        height=860,
        min_size=(900, 600),
        maximized=True,
        # Shown by desktop/mac_integration.py once macOS has launched the app,
        # unless macOS opened it at login: then it stays in the menu bar.
        hidden=True,
        # The loading page's colour and the app's, so the window never
        # flashes white between them.
        background_color=STARTUP_BACKGROUND,
        js_api=api,
    )

    # Close to the menu bar, the menu bar menu, opening at login, catching up
    # after sleep, and notifications. Imported here and not at the top: the
    # test suite imports this file on Linux, where AppKit does not exist.
    from desktop import mac_integration

    mac_integration.install(window)
    # start_app runs on its own thread while the window shows its loading page.
    webview.start(start_app, (window,))


if __name__ == "__main__":
    main()
