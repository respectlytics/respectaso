"""What the App Store shows about each of your apps, per storefront, kept fresh.

The opportunity score is for the app a keyword is tracked for, measured on the
same yardstick as the competitors (aso/strength.py): its rating count, its
average rating, its momentum (ratings per year) and its age. Apple counts
ratings per storefront, so the profile is per storefront too: an app with
12,000 ratings in the United States may have 40 in Bulgaria.

This module is the only reader and writer of ``App.store_profiles``, shaped
``{code: {"count": int, "average": float | None, "released": iso8601,
"checked_at": iso8601}}``:

    cached_profile()        read, no network; every read-time score uses it,
                            so all rows of one app in one storefront agree
    profile_in_storefront() refresh through one iTunes lookup when the value
                            is older than PROFILE_MAX_AGE; called by the
                            scoring pipeline whenever it scores for an app
    profile_for_track()     the same for a bare track id, for the Simulator,
                            which can run for an app you do not track
    backfill_from_history() once, at upgrade, from stored competitor lists

Free-tier module: no aso_pro, licensing or llm_providers import.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from .strength import AppProfile

logger = logging.getLogger(__name__)

PROFILE_MAX_AGE = timedelta(hours=24)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(stamp: str | None) -> datetime | None:
    if not stamp:
        return None
    try:
        return datetime.fromisoformat(stamp)
    except ValueError:
        return None


def _entry(app, country: str) -> dict:
    return (getattr(app, "store_profiles", None) or {}).get((country or "").lower()) or {}


def cached_profile(app, country: str) -> AppProfile | None:
    """The app's profile in one storefront, as last read.

    None for no app. An app never read in this storefront gets a profile
    marked unknown, so the score treats it as a brand new app and the
    sentence names the app and says its ratings have not been read.
    """
    if app is None:
        return None
    entry = _entry(app, country)
    if entry.get("count") is None:
        return AppProfile(name=app.name, known=False)
    return AppProfile(
        name=app.name,
        ratings=int(entry["count"]),
        average=entry.get("average"),
        released=entry.get("released"),
    )


def _is_fresh(app, country: str) -> bool:
    checked = _parse(_entry(app, country).get("checked_at"))
    return checked is not None and _now() - checked < PROFILE_MAX_AGE


def _store(app, country: str, found: dict, checked_at: datetime | None = None) -> bool:
    """Write one storefront's profile. True when anything the score reads
    changed."""
    from .models import App

    code = (country or "").lower()
    data = dict(app.store_profiles or {})
    previous = data.get(code) or {}
    data[code] = {
        "count": int(found["count"]),
        "average": found.get("average"),
        "released": found.get("released"),
        "checked_at": (checked_at or _now()).isoformat(),
    }
    app.store_profiles = data
    # An update, not save(): the app can be deleted while a search for it is
    # still running, and a missing row must not fail that search.
    if not App.objects.filter(pk=app.pk).update(store_profiles=data):
        return False
    return any(previous.get(k) != data[code][k] for k in ("count", "average", "released"))


def _lookup(track_id: int, country: str, itunes_service) -> dict | None:
    try:
        found = itunes_service.lookup_by_id(int(track_id), country=country)
    except Exception as exc:  # A failed lookup never fails a scoring run.
        logger.warning("Profile lookup failed for %s in %s: %s", track_id, country, exc)
        return None
    if not isinstance(found, dict):
        return None
    count = found.get("userRatingCount") or 0
    if not isinstance(count, (int, float)):
        return None
    average = found.get("averageUserRating")
    released = found.get("releaseDate")
    name = found.get("trackName")
    return {
        "count": int(count),
        "average": float(average) if isinstance(average, (int, float)) else None,
        "released": released if isinstance(released, str) else None,
        "name": name if isinstance(name, str) else None,
    }


def profile_in_storefront(app, country: str, *, itunes_service) -> AppProfile | None:
    """The app's profile in one storefront, refreshed at most daily.

    A failed lookup keeps the last known profile. When what the score reads
    changes, the stored labels of that app's rows in that storefront are
    recomputed, so the insight filter and the badges follow at once.
    """
    if app is None:
        return None
    if not getattr(app, "track_id", None) or _is_fresh(app, country):
        return cached_profile(app, country)
    found = _lookup(app.track_id, country, itunes_service)
    if found is None:
        return cached_profile(app, country)
    if _store(app, country, found):
        reclassify(app, country)
    return cached_profile(app, country)


def profile_for_track(track_id, country: str, *, itunes_service) -> AppProfile | None:
    """A profile for a bare track id: the tracked app's cache when it is one
    of yours, otherwise a direct lookup that is not stored."""
    if not track_id:
        return None
    from .models import App

    app = App.objects.filter(track_id=int(track_id)).first()
    if app is not None:
        return profile_in_storefront(app, country, itunes_service=itunes_service)
    found = _lookup(int(track_id), country, itunes_service)
    if found is None:
        return None
    return AppProfile(name=found["name"], ratings=found["count"],
                      average=found["average"], released=found["released"])


def reclassify(app, country: str) -> int:
    """Recompute the stored labels of one app's rows in one storefront."""
    from .models import SearchResult
    from .scoring import classify_keyword

    changed = []
    rows = SearchResult.objects.filter(
        keyword__app=app, country=(country or "").lower(),
    ).select_related("keyword__app")
    for row in rows.iterator(chunk_size=200):
        label = classify_keyword(
            row.effective_popularity or 0, row.difficulty_score, row.country,
            app=row.app_profile, app_rank=row.app_rank, keyword=row.keyword_text,
        )
        if label != row.classification:
            row.classification = label
            changed.append(row)
    if changed:
        SearchResult.objects.bulk_update(changed, ["classification"])
    return len(changed)


def backfill_from_history() -> int:
    """Seed the cache once from what was already observed.

    For every tracked app and storefront with stored rows, the newest row
    whose competitor list contains the app gives its rating count, average
    and release date, dated to that row, so the profile is used at once and
    refreshed at the next scoring. Storefronts where the app never appeared
    among the top 25 stay unread, and score as a brand new app until the
    first lookup, which the screen says.
    """
    from .models import App, SearchResult

    written = 0
    for app in App.objects.exclude(track_id__isnull=True):
        known = dict(app.store_profiles or {})
        found: dict[str, dict] = {}
        rows = (
            SearchResult.objects.filter(keyword__app=app)
            .order_by("-searched_at")
            .only("country", "searched_at", "competitors_data")
        )
        for row in rows.iterator(chunk_size=200):
            code = row.country
            if code in found or code in known:
                continue
            for competitor in row.competitors_data or []:
                if competitor.get("trackId") == app.track_id:
                    found[code] = {
                        "count": int(competitor.get("userRatingCount") or 0),
                        "average": competitor.get("averageUserRating"),
                        "released": competitor.get("releaseDate"),
                        "checked_at": row.searched_at.isoformat(),
                    }
                    break
        if found:
            known.update(found)
            App.objects.filter(pk=app.pk).update(store_profiles=known)
            written += len(found)
    return written
