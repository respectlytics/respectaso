"""A fixed set of competitor fields, used to prove a refactor moves no
difficulty. The fields are built deterministically from a small table, and
release dates are relative to a fixed day so the ages never drift."""

from datetime import datetime, timedelta, timezone

FIXED_NOW = datetime(2026, 9, 22, tzinfo=timezone.utc)

# keyword, per-app (ratings, stars, age in days, keyword in title), publishers
FIELDS = [
    ("focus timer", [(250_000, 4.8, 3000, True), (40_000, 4.6, 2000, True), (9_000, 4.4, 900, False),
                     (1_200, 4.1, 400, True), (300, 3.9, 200, False), (50, 4.9, 60, True)], 6),
    ("period tracker", [(1_900_000, 4.8, 3500, True), (800_000, 4.7, 3100, True), (220_000, 4.6, 2500, True),
                        (90_000, 4.5, 1800, True), (40_000, 4.4, 1200, True), (7_000, 4.3, 600, True)], 6),
    ("lan invoice", [(40, 4.0, 300, True), (500_000, 4.8, 3000, False), (300_000, 4.7, 2800, False),
                     (90_000, 4.6, 2500, False), (60_000, 4.5, 2000, False)], 5),
    ("meditation", [(3_000_000, 4.9, 3200, True), (1_500_000, 4.8, 3000, True), (60_000, 4.7, 1500, True),
                    (25_000, 4.6, 1200, False), (4_000, 4.5, 700, True), (800, 4.2, 300, False),
                    (120, 4.0, 100, True), (10, 5.0, 20, False)], 8),
    ("habit tracker", [(150_000, 4.7, 2000, True), (35_000, 4.6, 1400, True), (12_000, 4.5, 900, True),
                       (2_500, 4.4, 500, True), (600, 4.2, 250, True)], 4),
    ("budget planner couples", [(900, 4.5, 800, True), (150, 4.3, 300, False), (30, 4.0, 120, True)], 3),
    ("sleep sounds", [(700_000, 4.8, 3300, True), (200_000, 4.7, 2700, True), (80_000, 4.7, 2100, True),
                      (20_000, 4.6, 1500, True), (5_000, 4.5, 900, False), (900, 4.4, 400, True),
                      (100, 4.1, 150, False)], 7),
    ("fasting", [(400_000, 4.8, 2600, True), (150_000, 4.7, 2000, True), (15_000, 4.5, 1000, True),
                 (999, 4.4, 500, True), (1_001, 4.3, 400, True)], 5),
    ("nasdaq", [(90_000, 4.4, 4000, True), (500_000, 4.7, 3000, False), (200_000, 4.6, 2500, False)], 3),
    ("pdf scanner", [(1_000_000, 4.8, 3600, True), (400_000, 4.7, 3000, True), (100_000, 4.6, 2400, True),
                     (100_000, 4.6, 2300, True), (100_000, 4.5, 2200, False), (30_000, 4.4, 1500, True),
                     (8_000, 4.2, 800, False), (2_000, 4.0, 400, True), (500, 3.8, 200, True),
                     (50, 3.5, 50, False)], 10),
    ("one app keyword", [(5_000, 4.5, 1000, True)], 1),
    ("two apps keyword", [(700, 4.2, 500, True), (60, 4.0, 90, False)], 2),
]


def build(keyword, apps, publishers):
    out = []
    for i, (ratings, stars, age_days, in_title) in enumerate(apps):
        released = (FIXED_NOW - timedelta(days=age_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        out.append({
            "trackId": 5000 + i,
            "trackName": f"App {i} {keyword.title()}" if in_title else f"App {i} Pro",
            "userRatingCount": ratings,
            "averageUserRating": stars,
            "releaseDate": released,
            "currentVersionReleaseDate": released,
            "primaryGenreName": "Productivity",
            "sellerName": f"Publisher {i % publishers}",
        })
    return out
