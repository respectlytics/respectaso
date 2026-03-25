import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")

_django_app = get_asgi_application()
_mcp_asgi_app = None


def _get_mcp_app():
    global _mcp_asgi_app
    if _mcp_asgi_app is None:
        from aso.mcp_server import mcp
        _mcp_asgi_app = mcp.streamable_http_app()
    return _mcp_asgi_app


async def application(scope, receive, send):
    if scope["type"] == "http" and scope.get("path", "").startswith("/mcp"):
        await _get_mcp_app()(scope, receive, send)
    else:
        await _django_app(scope, receive, send)
