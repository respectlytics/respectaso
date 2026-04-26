"""
RespectASO — Native macOS App Entry Point

Launches the Django server in a background thread and opens a native
WebKit window via pywebview. Data is stored in
~/Library/Application Support/RespectASO/.
"""

import os
import sys
import socket
import threading
import time
from pathlib import Path


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
    from wsgiref.simple_server import WSGIServer, WSGIRequestHandler, make_server
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


def main():
    base_dir = get_base_dir()
    data_dir = get_data_dir()

    # Configure Django environment
    os.environ["DJANGO_SETTINGS_MODULE"] = "core.settings"
    os.environ["DATA_DIR"] = str(data_dir)
    os.environ["RESPECTASO_NATIVE"] = "1"

    # Ensure the project root is on sys.path so Django can find modules
    sys.path.insert(0, str(base_dir))

    # Generate / load SECRET_KEY before Django setup
    secret_key = ensure_secret_key(data_dir)
    os.environ["SECRET_KEY"] = secret_key

    # Ensure SSL certificate verification works inside PyInstaller bundle
    import certifi
    os.environ["SSL_CERT_FILE"] = certifi.where()

    # Setup Django
    import django

    django.setup()

    # Run migrations
    from django.core.management import call_command

    call_command("migrate", "--no-input", verbosity=0)
    call_command("collectstatic", "--no-input", verbosity=0)

    # Pick a free port and start Gunicorn in a daemon thread
    port = find_free_port()
    server_thread = threading.Thread(target=run_server, args=(port,), daemon=True)
    server_thread.start()

    if not wait_for_server(port):
        print("ERROR: Server failed to start within 30 seconds.", file=sys.stderr)
        sys.exit(1)

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
            except Exception:
                return False

    api = Api()
    window = webview.create_window(
        "RespectASO",
        f"http://127.0.0.1:{port}",
        width=1280,
        height=860,
        min_size=(900, 600),
        maximized=True,
        js_api=api,
    )
    webview.start()


if __name__ == "__main__":
    main()
