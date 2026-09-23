"""How strong an app is: ONE yardstick for the competitors and for your app.

Difficulty measures how strong the apps already ranking for a keyword are.
The opportunity score needs to know how strong YOUR app is, to say where it
would land among them. Both are measured here, with the same factor curves
and the same weights, so no tab or part of the tool judges strength in its
own way:

    factor          curve              in difficulty   per app
    rating volume   volume_score()     30%             yes
    momentum        momentum_score()   10%             yes (ratings per year)
    rating          rating_score()     10%             yes (average stars)
    age             age_score()        10%             yes (years on the store)
    title match     title_score()      10%             yes
    dominant players                   20%             field only
    publisher diversity                10%             field only

DifficultyCalculator aggregates the per-app curves over the field (median
volume and momentum, rating-weighted average stars, mean age, the share of
titles matching). AppProfile.strength() applies the same curves to one app,
renormalized over the five factors a single app has.

Every curve is continuous: the calibration points are joined by log or
linear interpolation and each ends exactly at its clamp value, so no input
change, however small, makes a score jump.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone


def _utcnow() -> datetime:
    """The one clock every strength calculation reads, so tests can fix it."""
    return datetime.now(timezone.utc)


def _log_bands(value: float, bands: list[tuple[float, float]]) -> float:
    """Log interpolation between calibration points, linear from 0 to the
    first point, and the last point's value at and beyond it."""
    if value <= 0:
        return 0.0
    last_threshold, last_score = bands[-1]
    if value >= last_threshold:
        return float(last_score)
    for i, (threshold, score) in enumerate(bands):
        if value < threshold:
            if i == 0:
                return (value / threshold) * score
            prev_threshold, prev_score = bands[i - 1]
            ratio = math.log(value / prev_threshold) / math.log(threshold / prev_threshold)
            return prev_score + ratio * (score - prev_score)
    return float(last_score)


def _linear_bands(value: float, bands: list[tuple[float, float]]) -> float:
    """Linear interpolation between calibration points, clamped at the ends."""
    if value <= bands[0][0]:
        return float(bands[0][1])
    if value >= bands[-1][0]:
        return float(bands[-1][1])
    for i in range(1, len(bands)):
        threshold, score = bands[i]
        if value < threshold:
            prev_threshold, prev_score = bands[i - 1]
            ratio = (value - prev_threshold) / (threshold - prev_threshold)
            return prev_score + ratio * (score - prev_score)
    return float(bands[-1][1])


# ── the factor curves ──────────────────────────────────────────────────────

# Rating count to 0-100: 50 -> 5 (ghost town), 500 -> 30 (indie-friendly),
# 2,000 -> 50, 10,000 -> 78 (hard), 100,000 -> 95 (dominated), 1,000,000 -> 100.
VOLUME_BANDS = [
    (50, 5), (200, 15), (500, 30), (2_000, 50), (5_000, 65),
    (10_000, 78), (25_000, 88), (100_000, 95), (1_000_000, 100),
]

# Ratings per year to 0-100: 10 -> 5 (dead), 200 -> 30 (slow), 1,000 -> 50
# (indie), 5,000 -> 70, 20,000 -> 85, 50,000 -> 95 (top charts), 500,000 -> 100.
MOMENTUM_BANDS = [
    (10, 5), (50, 15), (200, 30), (1_000, 50), (5_000, 70),
    (20_000, 85), (50_000, 95), (500_000, 100),
]

# Average stars to 0-100: 3.0 -> 20, 4.0 -> 50, 4.3 -> 70, 4.5 -> 85, 5.0 -> 100.
RATING_BANDS = [
    (0.0, 0), (3.0, 20), (3.5, 35), (4.0, 50), (4.3, 70), (4.5, 85), (5.0, 100),
]

# Years on the store to 0-100: half a year -> 10, 2 -> 35, 3 -> 50, 5 -> 70,
# 8 -> 85, 10 -> 100.
AGE_BANDS = [
    (0.5, 10), (1.0, 20), (2.0, 35), (3.0, 50), (5.0, 70), (8.0, 85), (10.0, 100),
]

# A new app's ratings per year are measured over at least half a year, so
# ten ratings in the first week do not read as 520 a year.
MIN_MOMENTUM_YEARS = 0.5


def volume_score(ratings: float) -> float:
    return _log_bands(ratings or 0, VOLUME_BANDS)


def momentum_score(ratings_per_year: float) -> float:
    return _log_bands(ratings_per_year or 0, MOMENTUM_BANDS)


def rating_score(average_stars: float) -> float:
    if not average_stars or average_stars <= 0:
        return 0.0
    return _linear_bands(average_stars, RATING_BANDS)


def age_score(years: float) -> float:
    if years is None or years <= 0:
        return 0.0
    return _log_free_age(years)


def _log_free_age(years: float) -> float:
    """Linear between the age points, from 0 at zero years."""
    first_threshold, first_score = AGE_BANDS[0]
    if years < first_threshold:
        return (years / first_threshold) * first_score
    return _linear_bands(years, AGE_BANDS)


def title_score(evidence: float) -> float:
    """How strongly a title targets the keyword, 0-100, from the graded title
    evidence (exact phrase 1.0, all words high, partial overlap low)."""
    return 100.0 * max(0.0, min(1.0, evidence or 0.0))


def years_since(released, now: datetime | None = None) -> float | None:
    """Years on the store from an ISO date string or a datetime."""
    if not released:
        return None
    if isinstance(released, str):
        try:
            released = datetime.fromisoformat(released.replace("Z", "+00:00"))
        except ValueError:
            return None
    if released.tzinfo is None:
        released = released.replace(tzinfo=timezone.utc)
    return max(0.0, ((now or _utcnow()) - released).days / 365.25)


def ratings_per_year(ratings: float, years: float | None) -> float:
    if not ratings or ratings <= 0 or years is None:
        return 0.0
    return ratings / max(MIN_MOMENTUM_YEARS, years)


# ── the weights ────────────────────────────────────────────────────────────

WEIGHTS = {
    "volume": 0.30,
    "momentum": 0.10,
    "dominance": 0.20,
    "rating": 0.10,
    "age": 0.10,
    "publishers": 0.10,
    "title": 0.10,
}
PER_APP_FACTORS = ("volume", "momentum", "rating", "age", "title")
_PER_APP_TOTAL = sum(WEIGHTS[f] for f in PER_APP_FACTORS)


# ── one app ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class AppProfile:
    """What the App Store shows about one app in one storefront.

    ``ratings`` and ``average`` are that storefront's own figures; Apple
    counts them per country. ``released`` is the app's first release date.
    None fields read as a brand new app's.
    """

    name: str | None = None
    ratings: int = 0
    average: float | None = None
    released: str | None = None
    # False when the app is named but its store data has not been read in
    # this storefront yet: it then scores as a brand new app, and the
    # sentences say so rather than pretend.
    known: bool = True

    def components(self, *, targeted: bool = True, title_evidence: float | None = None,
                   now: datetime | None = None) -> dict[str, float]:
        """The five per-app factors, each 0-100, on the difficulty curves.

        ``targeted`` takes the title factor as 100: the opportunity score asks
        what a keyword is worth if you go after it, which means putting it in
        your title. Pass ``title_evidence`` instead to score a title as it is.
        """
        years = years_since(self.released, now) if self.known else None
        ratings = (self.ratings or 0) if self.known else 0
        evidence = 1.0 if targeted else (title_evidence or 0.0)
        return {
            "volume": volume_score(ratings),
            "momentum": momentum_score(ratings_per_year(ratings, years)),
            "rating": rating_score(self.average or 0) if ratings > 0 else 0.0,
            "age": age_score(years) if years is not None else 0.0,
            "title": title_score(evidence),
        }

    def strength(self, **kwargs) -> float:
        """0-100 on the same scale as difficulty's per-app factors."""
        parts = self.components(**kwargs)
        return sum(WEIGHTS[f] * parts[f] for f in PER_APP_FACTORS) / _PER_APP_TOTAL

    @classmethod
    def from_result(cls, app: dict) -> "AppProfile":
        """From an iTunes search or lookup result."""
        return cls(
            name=app.get("trackName"),
            ratings=int(app.get("userRatingCount") or 0),
            average=app.get("averageUserRating"),
            released=app.get("releaseDate"),
        )


NEW_APP = AppProfile()
