"""
App resolver tool — map a user-provided app title to a local app_id.

Use this when a user asks for app-specific keyword history but only knows
their app name/title.
"""

from mcp_server.src import mcp
from mcp_server.src.django_bootstrap import bootstrap_django


def _serialize_app(app) -> dict:
    """Return compact app details for user-facing disambiguation."""
    return {
        "app_id": app.id,
        "name": app.name,
        "bundle_id": app.bundle_id or "",
        "track_id": app.track_id,
        "seller_name": app.seller_name or "",
    }


@mcp.tool(
    description=(
        """
        Use this tool when you need to get the app_id of an app from its name or title.
        """
    )
)
def get_app_id_from_name(app_name: str, limit: int = 5) -> dict:
    """
    Resolve a local app_id from an app title/name.

    This helps the assistant turn a user-provided app title into the app_id
    needed by app-filtered resources like saved keywords.
    """

    bootstrap_django()

    from aso.models import App

    query = app_name or ""
    normalized_query = " ".join(query.strip().split())

    try:
        lim = max(1, min(int(limit), 10))
    except (TypeError, ValueError):
        lim = 5

    if not normalized_query:
        return {
            "status": "not_found",
            "query": query,
            "normalized_query": normalized_query,
            "resolved_app_id": None,
            "matches": [],
            "message": "Provide the app name you want to filter by.",
            "match_strategy": "none",
        }

    exact_qs = App.objects.filter(name__iexact=normalized_query).order_by(
        "-created_at", "id"
    )
    exact_count = exact_qs.count()

    # Only auto-resolve when one exact title match exists.
    if exact_count == 1:
        app = exact_qs.first()
        return {
            "status": "resolved",
            "query": query,
            "normalized_query": normalized_query,
            "resolved_app_id": app.id,
            "matches": [_serialize_app(app)],
            "message": "Found one exact app title match.",
            "match_strategy": "exact_name",
        }

    # Do not auto-pick between duplicate exact titles; return choices instead.
    if exact_count > 1:
        matches = [_serialize_app(app) for app in exact_qs[:lim]]
        return {
            "status": "ambiguous",
            "query": query,
            "normalized_query": normalized_query,
            "resolved_app_id": None,
            "matches": matches,
            "message": (
                "More than one app has this exact title. Ask the user to choose one "
                "before filtering saved keywords."
            ),
            "match_strategy": "exact_name_multiple",
        }

    partial_qs = App.objects.filter(name__icontains=normalized_query).order_by(
        "name", "id"
    )
    partial_matches = [_serialize_app(app) for app in partial_qs[:lim]]

    if partial_matches:
        message = (
            "No exact app title match found. Ask the user to pick one candidate "
            "or provide a more specific title."
        )
        strategy = "partial_candidates"
    else:
        message = "No matching apps found. Ask the user for the exact app title."
        strategy = "none"

    return {
        "status": "not_found",
        "query": query,
        "normalized_query": normalized_query,
        "resolved_app_id": None,
        "matches": partial_matches,
        "message": message,
        "match_strategy": strategy,
    }
