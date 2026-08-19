"""iTunes genre -> Apple Ads genre-bucket mapping and keyword inference.

Apple's top-search-terms dataset buckets terms into 15 broad genres
(observed live 2026-08: BUSINESS, EDUCATION, ENTERTAINMENT, FINANCE,
FOOD_DRINK, GAMES, HEALTH_FITNESS, LIFESTYLE, NEW_PUBLICATION,
PHOTO_VIDEO, PRODUCTIVITY_UTILITIES, SHOPPING, SOCIAL_NETWORKING,
SPORTS, TRAVEL). iTunes Search results carry `primaryGenreName` values
from a finer taxonomy; this module maps them onto Apple's buckets so a
keyword's category can be inferred from its own competitor apps.

The inference feeds the genre-aware fallback cap (see
aso.popularity.absent_cap): a keyword absent from the dataset is only
provably less popular than ITS OWN category's least-reported term, and
category floors differ a lot (US week of 2026-08-09: SPORTS 40 ...
GAMES 56). Inference is a majority vote over the top competitors -
imperfect by nature, so unmapped/unknown falls back to the country's
GLOBAL floor, which is always the most conservative cap.

Free-tier module: no aso_pro imports, no network.
"""

from collections import Counter

# iTunes primaryGenreName -> Apple Ads genre bucket. Any "Games" genre
# (including subgenres like "Games/Action") maps via the GAMES prefix
# rule in map_itunes_genre().
ITUNES_TO_APPLE_GENRE = {
    "business": "BUSINESS",
    "developer tools": "PRODUCTIVITY_UTILITIES",
    "education": "EDUCATION",
    "entertainment": "ENTERTAINMENT",
    "finance": "FINANCE",
    "food & drink": "FOOD_DRINK",
    "graphics & design": "PRODUCTIVITY_UTILITIES",
    "health & fitness": "HEALTH_FITNESS",
    "lifestyle": "LIFESTYLE",
    "magazines & newspapers": "NEW_PUBLICATION",
    "medical": "HEALTH_FITNESS",
    "music": "ENTERTAINMENT",
    "navigation": "TRAVEL",
    "news": "NEW_PUBLICATION",
    "photo & video": "PHOTO_VIDEO",
    "productivity": "PRODUCTIVITY_UTILITIES",
    "reference": "EDUCATION",
    "shopping": "SHOPPING",
    "social networking": "SOCIAL_NETWORKING",
    "sports": "SPORTS",
    "stickers": "ENTERTAINMENT",
    "travel": "TRAVEL",
    "utilities": "PRODUCTIVITY_UTILITIES",
    "weather": "PRODUCTIVITY_UTILITIES",
    "book": "NEW_PUBLICATION",
    "books": "NEW_PUBLICATION",
}


def map_itunes_genre(primary_genre_name: str) -> str | None:
    """Map one iTunes primaryGenreName to an Apple genre bucket, or None."""
    name = (primary_genre_name or "").strip().lower()
    if not name:
        return None
    if name.startswith("games") or name == "game":
        return "GAMES"
    return ITUNES_TO_APPLE_GENRE.get(name)


def genre_label(genre: str) -> str:
    """Display label for an Apple genre bucket.

    "HEALTH_AND_FITNESS" -> "Health And Fitness". The ONE place bucket
    keys become user-visible text (Top Terms, popularity popovers).
    """
    return (genre or "").replace("_", " ").title()


def infer_genre(competitors: list[dict], top_n: int = 10) -> str | None:
    """Infer a keyword's Apple genre bucket from its competitor apps.

    Majority vote over the top results' primaryGenreName. Returns None
    when nothing maps (callers fall back to the global floor).
    """
    votes = Counter()
    for competitor in (competitors or [])[:top_n]:
        bucket = map_itunes_genre(competitor.get("primaryGenreName", ""))
        if bucket:
            votes[bucket] += 1
    if not votes:
        return None
    return votes.most_common(1)[0][0]
