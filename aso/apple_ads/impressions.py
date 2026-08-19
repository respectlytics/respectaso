"""Weekly impression-share sync for tracked apps (Apple Ads Platform API v1).

Apple only reports impression share for search terms where the app's own
ads served (and suppresses terms under 10 impressions), so most installs
get zero rows - the UI renders the section only when data exists, and a
zero-row sync is a normal, successful outcome.

Runs inside the dataset sync worker (sync._run_impressions), one request
per tracked app covering the last 4 completed weeks. Failure-isolated:
problems here never mark the dataset sync as failed.
"""

import logging

from django.utils import timezone

from . import api, storage
from .api import AppleAdsAuthError, AppleAdsError

logger = logging.getLogger(__name__)

WEEKS_PER_QUERY = 4            # API maximum for WEEKLY_SUN_SAT.
WEEKS_RETAINED = 65


def run_weekly(credentials, ad_account_id, *, spend_request, pace) -> None:
    """Sync impression share for every tracked app, once per published week.

    Args:
        spend_request: callable() -> bool; checks the shared Layer 4
            budget and records the request. False = budget exhausted.
        pace: callable() for the shared Layer 1 courtesy delay.

    Raises:
        AppleAdsAuthError: credential rejection (handled by the caller -
            it is never impression-share-specific).
    """
    from ..models import App

    week = api.latest_available_week()
    state = dict(storage.load_apple_settings()["apple_ads"]["impression_share"])
    if state.get("last_week") == week.isoformat():
        return

    apps = list(App.objects.exclude(track_id__isnull=True))
    if not apps:
        _save_state(state, status="completed", error="",
                    last_week=week.isoformat())
        return

    window_start = api.weeks_back(week, WEEKS_PER_QUERY - 1)
    wrote_rows = False
    errors = []
    for index, app in enumerate(apps):
        if not spend_request():
            _save_state(state, status="partial",
                        error="Request budget reached - resumes next sync.")
            return
        if index:
            pace()
        try:
            rows, _total = api.query_impression_share(
                credentials, ad_account_id,
                promoted_object_id=str(app.track_id),
                week_start=window_start, weeks=WEEKS_PER_QUERY,
            )
        except AppleAdsAuthError:
            raise
        except AppleAdsError as e:
            logger.warning("Impression share failed for %s: %s", app.name, e)
            errors.append(f"{app.name}: {e}")
            continue
        wrote_rows = _store_rows(app, rows) or wrote_rows

    _prune()
    _save_state(
        state,
        status="completed" if not errors else "partial",
        error="; ".join(errors),
        last_week=week.isoformat(),
        has_data=state.get("has_data", False) or wrote_rows,
        last_sync_at=timezone.now().isoformat(),
    )


def _store_rows(app, rows) -> bool:
    from ..models import AppleImpressionShare

    wrote = False
    for row in rows:
        term = row.get("searchTerm")
        country = row.get("countryOrRegion")
        week_raw = row.get("week")
        low = row.get("lowImpressionShare")
        high = row.get("highImpressionShare")
        if (
            not isinstance(term, str) or not term.strip()
            or not isinstance(country, str) or not country
            or not isinstance(week_raw, str)
            or not isinstance(low, (int, float)) or not 0 <= low <= 1
            or not isinstance(high, (int, float)) or not 0 <= high <= 1
        ):
            continue
        try:
            week = timezone.datetime.fromisoformat(week_raw).date()
        except ValueError:
            continue
        rank = row.get("rank")
        tier = row.get("searchPopularity1to5")
        AppleImpressionShare.objects.update_or_create(
            app=app,
            country=country.lower(),
            search_term=term.lower().strip()[:200],
            week=week,
            defaults={
                "low_share": float(low),
                "high_share": float(high),
                "rank": rank if isinstance(rank, int) else None,
                "popularity_tier": tier if isinstance(tier, int) else None,
            },
        )
        wrote = True
    return wrote


def _prune() -> None:
    from ..models import AppleImpressionShare

    cutoff = api.weeks_back(api.latest_available_week(), WEEKS_RETAINED)
    AppleImpressionShare.objects.filter(week__lt=cutoff).delete()


def _save_state(state: dict, **updates) -> None:
    state = {**state, **updates}
    storage.save_apple_settings(apple_ads={"impression_share": state})
