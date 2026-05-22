"""Centralized ASO scoring functions.

Single source of truth for opportunity scoring and keyword classification.
Used by both free (Dashboard, Opportunity page) and Pro features
(Researcher, Competitor Analyzer, Simulator, Metadata Evaluator).
"""

import math

# Popularity → estimated daily searches (US App Store baseline).
# Mirrors DownloadEstimator._POP_TO_SEARCHES in aso/services.py.
_POP_TO_SEARCHES = [
    (5, 1),
    (10, 3),
    (15, 5),
    (20, 10),
    (25, 20),
    (30, 35),
    (35, 55),
    (40, 90),
    (45, 140),
    (50, 200),
    (55, 290),
    (60, 400),
    (65, 550),
    (70, 750),
    (75, 1_100),
    (80, 2_000),
    (85, 4_000),
    (90, 8_000),
    (95, 16_000),
    (100, 32_000),
]

_MAX_SEARCHES = 32_000  # pop=100 baseline


def _pop_to_searches(popularity: int) -> float:
    """Interpolate daily search volume from popularity score."""
    if popularity is None or popularity <= 0:
        return 0
    pts = _POP_TO_SEARCHES
    if popularity <= pts[0][0]:
        return pts[0][1] * (popularity / pts[0][0])
    if popularity >= pts[-1][0]:
        return pts[-1][1]
    for i in range(1, len(pts)):
        p0, s0 = pts[i - 1]
        p1, s1 = pts[i]
        if popularity <= p1:
            ratio = (popularity - p0) / (p1 - p0)
            return s0 + ratio * (s1 - s0)
    return pts[-1][1]


def calc_opportunity(popularity: int, difficulty: int) -> int:
    """Calculate opportunity score (0-100).

    Formula:
        volume = log10(1 + daily_searches) / log10(1 + 32000)
        gate   = 1 - (difficulty / 100)^2
        opportunity = volume × gate × 100

    Volume uses log-normalized estimated daily searches so that
    the exponential nature of App Store search volume is captured.

    Difficulty acts as a quadratic gate — gentle penalty at low
    difficulty, accelerating at high difficulty.  A keyword with
    difficulty 100 always scores 0 regardless of popularity.
    """
    if not popularity or popularity <= 0:
        return 0
    searches = _pop_to_searches(popularity)
    if searches <= 0:
        return 0
    volume = math.log10(1 + searches) / math.log10(1 + _MAX_SEARCHES)
    gate = 1 - (difficulty / 100) ** 2
    raw = volume * gate * 100
    return max(0, min(100, int(raw)))


# All valid classification labels — used for DB column validation.
CLASSIFICATION_LABELS = [
    "Sweet Spot",
    "Hidden Gem",
    "Low Volume",
    "High Competition",
    "Good Target",
    "Avoid",
    "Moderate",
]


def classify_keyword(popularity: int, difficulty: int) -> str:
    """Classify a keyword based on popularity, difficulty, and opportunity.

    This is the SINGLE SOURCE OF TRUTH for keyword classification.
    Every consumer (Dashboard, Opportunity, Researcher, Competitor,
    Simulator, CSV export, insight filters) must use this function
    or read a value that was produced by it.  Never duplicate this
    logic in JavaScript, SQL, or Q-objects.
    """
    opp = calc_opportunity(popularity, difficulty)
    if popularity >= 40 and difficulty <= 40:
        return "Sweet Spot"
    if 25 <= popularity < 40 and difficulty <= 30 and opp >= 30:
        return "Hidden Gem"
    if popularity < 15:
        return "Low Volume"
    if difficulty >= 65:
        return "High Competition"
    if opp >= 55:
        return "Good Target"
    if opp <= 25:
        return "Avoid"
    return "Moderate"


# ── Targeting advice tuples ── (icon, label, css_classes, description)

_TARGETING = {
    "Sweet Spot": ("🎯", "Sweet Spot", "bg-green-900/20 text-green-300 border-green-500/20",
                   "High search volume + low competition — the ideal ASO target."),
    "Good Target": ("✅", "Good Target", "bg-green-900/20 text-green-300 border-green-500/20",
                    "Solid search volume with manageable competition."),
    "Hidden Gem": ("💎", "Hidden Gem", "bg-blue-900/20 text-blue-300 border-blue-500/20",
                   "Moderate search volume with minimal competition — a genuine opportunity others have overlooked."),
    "High Competition": ("⚔️", "High Competition", "bg-yellow-900/20 text-yellow-300 border-yellow-500/20",
                         "Dominated by established apps with thousands of ratings. Focus on long-tail variants instead."),
    "Moderate": ("👍", "Moderate", "bg-slate-800 text-slate-300 border-white/10",
                 "Reasonable opportunity. Can work as a supporting keyword."),
    "Low Volume": ("🔍", "Low Volume", "bg-slate-800 text-slate-300 border-white/10",
                   "Very few searches. Only worth targeting if highly relevant to your app."),
    "Avoid": ("🚫", "Avoid", "bg-red-900/20 text-red-300 border-red-500/20",
              "Low opportunity. Effort better spent elsewhere."),
}

# Difficulty-only fallbacks (when popularity is unknown)
_DIFF_ONLY_TARGETING = {
    25: ("🟢", "Easy to Rank", "bg-green-900/20 text-green-300 border-green-500/20",
         "Low competition — a well-optimized app can rank quickly."),
    50: ("🟡", "Moderate", "bg-yellow-900/20 text-yellow-300 border-yellow-500/20",
         "Achievable with strong ASO."),
    75: ("🟠", "Competitive", "bg-orange-900/20 text-orange-300 border-orange-500/20",
         "Consider long-tail variants."),
}
_DIFF_ONLY_DEFAULT = ("🔴", "Very Competitive", "bg-red-900/20 text-red-300 border-red-500/20",
                      "Dominated by established apps. Target easier keywords first.")


def get_targeting_advice(popularity, difficulty):
    """Return (icon, label, css_classes, description) for ASO targeting.

    This is the single source of truth — called by the model property,
    mirrored by the JS in opportunity.html and dashboard.html.
    """
    if popularity is not None:
        label = classify_keyword(popularity, difficulty)
        return _TARGETING[label]
    # Difficulty-only (popularity unavailable)
    for threshold, advice in sorted(_DIFF_ONLY_TARGETING.items()):
        if difficulty <= threshold:
            return advice
    return _DIFF_ONLY_DEFAULT
