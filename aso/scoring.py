"""Centralized ASO scoring functions.

Single source of truth for opportunity scoring and keyword classification.
Used by both free (Dashboard, Opportunity page) and Pro features
(Researcher, Competitor Analyzer, Simulator, Metadata Evaluator).
"""

import math

# Popularity → estimated daily searches (US App Store baseline).
# THE canonical curve - DownloadEstimator imports it (no duplicate).
#
# Anchored to Apple's official top-search-terms dataset (Apple Ads
# Platform API v1): only terms with roughly >= 500 weekly searches
# (~70/day) are reported, and the observed dataset floor sits around
# popularity 40 - the first hard absolute calibration point Apple has
# ever provided. Both popularity sources speak this 1-100 scale
# (estimate v2 is fitted to it), so one curve serves both:
#   * 1-39: the below-top-terms region - linear ramp from ~1/day up to
#     just under the ~70/day anchor.
#   * 40-100: log-linear growth (~15%/point) from the anchor, slope
#     cross-checked against within-genre rank data (Zipf over 500 ranks
#     gives a ~320x #1-vs-#500 spread, matching e.g. "indeed" - US
#     BUSINESS #1, value 79 - at roughly 16k searches/day).
POP_TO_SEARCHES = [
    (1, 1),
    (20, 35),
    (39, 65),
    (40, 70),
    (45, 140),
    (50, 280),
    (55, 570),
    (60, 1_150),
    (65, 2_300),
    (70, 4_600),
    (75, 9_300),
    (80, 19_000),
    (85, 38_000),
    (90, 76_000),
    (95, 152_000),
    (100, 300_000),
]
_POP_TO_SEARCHES = POP_TO_SEARCHES  # internal alias

_MAX_SEARCHES = 300_000  # pop=100 baseline


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


# A keyword seen less than this often in a storefront is Low Volume there,
# whatever its relative popularity. Same threshold DownloadEstimator uses to
# stop quoting a download range nobody could act on.
MIN_DAILY_SEARCHES = 1.0

def daily_searches(popularity, country=None) -> float:
    """Estimated searches per day for a keyword in a storefront.

    POP_TO_SEARCHES is calibrated for the US App Store, so the result is
    scaled by the storefront's own market size (aso/countries.py). This is the
    single source for that multiplication: DownloadEstimator calls it too.
    """
    from aso import countries

    searches = _pop_to_searches(popularity or 0)
    if not country:
        return searches
    entry = countries.get(country)
    factor = entry.market if entry else countries.smallest_market()
    return searches * factor


# The 0-100 scale for opportunity, anchored so it can be said in a sentence:
# one download a day from a keyword is 50, and every tenfold change moves it
# 20 points. So the scale runs from about one download a YEAR at 0 to about
# three hundred a day at 100, which is the range the app actually produces.
OPPORTUNITY_MIDPOINT_DOWNLOADS = 1.0
OPPORTUNITY_POINTS_PER_DECADE = 20.0

# The end of the download table and of the first page of results. Ranks
# past it still count, on the continuous tail DownloadEstimator.ttr_at()
# draws; this is only where the table stops and where the wording changes.
WORST_POSITION = 20

# The rank model: where an app of a given strength lands for a keyword of a
# given difficulty, both measured on the one yardstick in aso/strength.py.
#
# Measured, not assumed. rank_calibration_study (2026-09-22) read 309 App
# Store searches of up to 196 results in six storefronts and, for the 5,846
# apps with the keyword in their title, set the gap (difficulty minus the
# app's strength) against the rank Apple gave them. Where an app lands is
# spread wide: eight in ten apps with 2,000 to 10,000 ratings on a keyword of
# difficulty 50 to 60 land between #3 and #79, around #18 in the middle; one
# with under a hundred ratings lands around the middle of the list, but
# reaches the top ten one time in ten on an easy keyword and one in fifty on
# a hard one. A score that counts downloads
# has to count that chance, so each point below is the EFFECTIVE rank: the
# rank whose tap-through equals the average tap-through of the apps at that
# gap. (gap, effective rank), from the 70% training terms; log-linear
# between the points. Below the first point it stays flat: no app does better
# than the strongest measured. Past the last point the last segment's slope
# continues, up to DEEPEST_RANK: the few apps observed there (too few for a
# point of their own) keep sliding at that rate, about #63 at a gap of 65
# and #90 at 68, so holding the rank flat would flatter the hardest keywords
# (plan, D3d). Continuous, and never better for a harder keyword. On the 30% held-out terms it orders apps with
# Spearman 0.40 and predicts their average tap-through within a factor of
# 1.24, where the curve it replaces missed by a factor of 2.4 (gates E1 and
# E2, fixed before the fit: STRENGTH_AND_RANK_CALIBRATION_PLAN.md, D3c;
# output in docs/development/rank_calibration_2026-09.txt).
RANK_BY_GAP: list[tuple[float, float]] = [
    (-37.3, 1.56),
    (-32.6, 1.58),
    (-27.2, 3.09),
    (-22.6, 4.28),
    (-17.4, 5.28),
    (-12.5, 6.22),
    (-7.3, 7.62),
    (-2.3, 10.44),
    (5.2, 13.82),
    (15.0, 16.70),
    (22.5, 17.23),
    (27.4, 17.86),
    (38.8, 23.08),
    (52.5, 31.82),
    (59.0, 47.72),
]
DEEPEST_RANK = 250.0

# What RANK_BY_GAP was measured on, for the pages that describe it. Refit
# the table and these change with it.
RANK_STUDY = {"searches": 309, "storefronts": ("us", "br", "fr", "ca", "mx", "it"),
              "apps": 5_846}

# What a person reads: where apps land, not the effective rank. RANK_BY_GAP
# is the rank whose taps equal the AVERAGE over apps at a gap, and the few
# that land near the top pull that average far up: at a gap where the
# effective rank is #14, the middle app lands around #68. Shown as a place,
# "#14" read as a promise that almost no app keeps (the owner's "options
# trading" rows, 2026-09-23). So the screens say where the middle app lands
# and how many reach the top 10, and say that the score is the average.
# (gap, middle rank, share in the top 10), from rank_calibration_study over
# all 5,847 observations of the same week's searches, run again on
# 2026-09-23 (docs/development/rank_calibration_2026-09.txt, run 3); the middle rank never
# improves and the share never grows as the gap widens. Apps that rank
# below the ~196 results a search returns are not observed, so both are, if
# anything, kind. Between points: the rank log-linear, the share linear.
TYPICAL_BY_GAP: list[tuple[float, float, float]] = [
    (-42.5, 1.0, 0.885),   # n=26
    (-37.3, 1.0, 0.850),   # n=40
    (-32.5, 1.0, 0.836),   # n=61
    (-27.4, 4.0, 0.630),   # n=73
    (-22.4, 13.0, 0.456),  # n=90
    (-17.5, 16.0, 0.367),  # n=128
    (-12.5, 21.0, 0.367),  # n=171
    (-7.3, 31.0, 0.244),   # n=213
    (-2.4, 42.0, 0.153),   # n=294
    (2.4, 54.0, 0.088),    # n=353
    (7.6, 69.0, 0.074),    # n=432
    (12.6, 78.0, 0.034),   # n=522
    (17.5, 85.0, 0.034),   # n=538
    (22.5, 96.0, 0.028),   # n=529
    (27.5, 98.5, 0.021),   # n=574
    (32.5, 99.0, 0.004),   # n=479
    (37.3, 99.0, 0.003),   # n=400
    (42.5, 103.0, 0.003),  # n=312
    (47.2, 103.0, 0.003),  # n=293
    (52.5, 103.0, 0.003),  # n=132
    (57.1, 123.0, 0.000),  # n=75
    (61.8, 123.0, 0.000),  # n=52
]



def _profile(app):
    """The profile a score uses: the app's, or a brand new app's when there is
    no app or its store data has not been read yet."""
    from .strength import NEW_APP

    if app is None or not getattr(app, "known", True):
        return NEW_APP
    return app


def app_strength(app=None) -> float:
    """The app's strength, 0-100, on the same yardstick as the competitors,
    with the keyword taken as targeted (in its title)."""
    return _profile(app).strength(targeted=True)


def _new_app_strength() -> float:
    from .strength import NEW_APP

    return NEW_APP.strength(targeted=True)


def title_carries(app, keyword) -> bool:
    """Whether the app's title (its App Store name) already carries the
    keyword: the exact phrase, or all of its words."""
    if app is None or not keyword or not getattr(app, "name", None):
        return False
    from .services import _keyword_title_evidence

    evidence = _keyword_title_evidence(keyword.lower().strip(), app.name, "")
    return bool(evidence["exact_phrase"] or evidence["all_words"])


def effective_difficulty(difficulty, app=None) -> float:
    """Difficulty as this app experiences it: what is left after its own
    strength above a brand new app's. Equal to the difficulty for a new app,
    never above it."""
    lift = max(0.0, app_strength(app) - _new_app_strength())
    return max(0.0, min(max(difficulty or 0, 0), 100) - lift)


def rank_from_gap(gap: float) -> float:
    """The effective rank at a gap of difficulty minus strength: the measured
    table, log-linear between its points, flat below the first, and past the
    last at the last segment's slope up to DEEPEST_RANK."""
    if gap <= RANK_BY_GAP[0][0]:
        return RANK_BY_GAP[0][1]
    if gap >= RANK_BY_GAP[-1][0]:
        (g0, r0), (g1, r1) = RANK_BY_GAP[-2], RANK_BY_GAP[-1]
        slope = (math.log(r1) - math.log(r0)) / (g1 - g0)
        return min(DEEPEST_RANK, r1 * math.exp(slope * (gap - g1)))
    for (g0, r0), (g1, r1) in zip(RANK_BY_GAP, RANK_BY_GAP[1:]):
        if g0 <= gap <= g1:
            t = (gap - g0) / (g1 - g0) if g1 > g0 else 0.0
            return math.exp(math.log(r0) + t * (math.log(r1) - math.log(r0)))
    return RANK_BY_GAP[-1][1]


def reachable_rank(difficulty, app=None, app_rank=None, keyword=None) -> float:
    """The rank whose downloads this app can expect for this keyword here.

    From the gap between the keyword's difficulty and the app's strength,
    both measured on the one yardstick: the effective rank of RANK_BY_GAP,
    what apps like it get with the keyword in their title. The app's real
    rank changes that in two ways. When its title already carries the
    keyword, the real rank is simply the answer, better or worse than the
    model: it is what carrying the keyword does for this app. When it does
    not, the model says what carrying it would bring, and a real rank that is
    already better wins: an app at #2 is never told it can expect #9.
    """
    gap = min(max(difficulty or 0, 0), 100) - app_strength(app)
    model = rank_from_gap(gap)
    if app_rank and app_rank >= 1:
        if title_carries(app, keyword):
            return float(app_rank)
        return min(model, float(app_rank))
    return model


def reachable_position(difficulty, app=None, app_rank=None, keyword=None) -> int:
    """The effective rank, rounded: what the score counts. It is not where a
    typical app lands, so the screens never show it as a place for the
    model's rank (typical_landing is); a real rank is shown as it is."""
    return int(reachable_rank(difficulty, app, app_rank, keyword) + 0.5)


def typical_landing(difficulty, app=None) -> tuple[int, float]:
    """Where the middle app like this one lands for this keyword with it in
    its title, and the share of such apps that reach the top 10
    (TYPICAL_BY_GAP), from the same gap as the score.

    Never better than the effective rank at the same gap: the middle app
    cannot beat the average of a field whose few leaders pull the average
    up. Past the last measured point that is what carries the rank on, on
    the same slope the score uses (RANK_BY_GAP, DEEPEST_RANK), since apps
    below Apple's ~200 results are not observed at all.
    """
    gap = min(max(difficulty or 0, 0), 100) - app_strength(app)
    points = TYPICAL_BY_GAP
    if gap <= points[0][0]:
        rank, share = points[0][1], points[0][2]
    elif gap >= points[-1][0]:
        rank, share = points[-1][1], points[-1][2]
    else:
        for (g0, r0, s0), (g1, r1, s1) in zip(points, points[1:]):
            if g0 <= gap <= g1:
                t = (gap - g0) / (g1 - g0) if g1 > g0 else 0.0
                rank = math.exp(math.log(r0) * (1 - t) + math.log(r1) * t)
                share = s0 + (s1 - s0) * t
                break
    rank = max(rank, rank_from_gap(gap))
    return max(1, int(rank + 0.5)), share


def rank_text(rank: int) -> str:
    """A rank as the screens print it: "#68", or "past #200" beyond the
    roughly 200 results an App Store search returns."""
    return "past #200" if rank > 200 else f"#{rank}"


def _around(rank: int) -> str:
    """"around #68", or "past #200"."""
    return "past #200" if rank > 200 else f"around #{rank}"


def top_ten_phrase(share: float) -> str:
    """How many reach the top 10, ending in its verb: "about 1 in 9
    reaches", "most reach". Reads "... the top 10"."""
    if share >= 0.7:
        return "most reach"
    if share >= 0.4:
        return "about half reach"
    if share < 0.01:
        return "fewer than 1 in 100 reach"
    # A measured share, said the way a person would: 1 in 9, 1 in 25, 1 in
    # 100, never "1 in 99".
    n = 1 / share
    if n <= 20:
        n = round(n)
    elif n <= 50:
        n = 5 * round(n / 5)
    else:
        n = 10 * round(n / 10)
    return f"about 1 in {n} reaches"


def expected_downloads(popularity, difficulty, country=None, app=None,
                       app_rank=None, keyword=None) -> float:
    """Downloads a day this app can expect from this keyword, at its effective rank.

    The single input to the opportunity score, and the same figure the chart
    on the page is drawn from, unrounded: the chart rounds it to two decimals
    for display, and a score built on the rounded number would put half the
    storefronts on an identical zero. Conservative end of the range, because
    a score built on the optimistic end would flatter every keyword equally.
    Read at the real-valued rank, so it moves smoothly with difficulty and
    with the app's profile.
    """
    if not popularity or popularity <= 0:
        return 0.0
    from .services import DownloadEstimator

    return DownloadEstimator().downloads_at(
        popularity,
        reachable_rank(difficulty, app, app_rank, keyword),
        country=country or "us",
    )


def calc_opportunity(popularity: int, difficulty: int, country=None,
                     app=None, app_rank=None, keyword=None) -> int:
    """Calculate opportunity score (0-100): what this keyword is worth here.

    Formula:
        rank        = the rank whose taps this app can expect, measured from
                      where apps like it land for keywords this hard (the
                      gap between the difficulty and its strength in this
                      storefront), or its real rank when that is better
        downloads   = what that rank pays, in THIS storefront
        opportunity = 50 + 20 × log10(downloads per day)

    So one download a day scores 50 and every tenfold change moves it 20
    points. The score is a plain function of the downloads, nothing else,
    which is what makes the comparison a user actually performs, between two
    countries, mean something: more reachable downloads is always a higher
    score, in all 175 storefronts, with no special cases. Every step of it is
    continuous, and aso/tests/test_opportunity_invariants.py holds it to that.

    ``country`` omitted keeps the United States, the scale everything is
    calibrated to. ``app`` is the tracked app's AppProfile in this storefront,
    None for a brand new app, which the interface says wherever the score is
    shown; ``app_rank`` its real rank for this keyword here, when known;
    ``keyword`` the keyword, which decides whether that real rank is final
    (reachable_rank).
    """
    downloads = expected_downloads(popularity, difficulty, country, app, app_rank, keyword)
    return _score_for_downloads(downloads)


def _score_for_downloads(downloads: float) -> int:
    """The opportunity scale: 50 at one download a day, 20 points a decade."""
    if downloads <= 0:
        return 0
    raw = 50 + OPPORTUNITY_POINTS_PER_DECADE * math.log10(
        downloads / OPPORTUNITY_MIDPOINT_DOWNLOADS
    )
    return max(0, min(100, int(round(raw))))


def top_spot_opportunity(popularity, country=None) -> int:
    """What the keyword would score for an app holding #1 here: the most it
    can be worth to anyone. It tells an empty market from an unreachable one."""
    if not popularity or popularity <= 0:
        return 0
    from .services import DownloadEstimator

    return _score_for_downloads(
        DownloadEstimator().downloads_at(popularity, 1, country=country or "us")
    )


# Difficulty bands: the SINGLE SOURCE OF TRUTH for the label and the colour
# of a difficulty score. Each entry is (upper_bound_inclusive, label, css).
# It used to exist five times: SearchResult.difficulty_label and
# .difficulty_color, two opportunity views, and twice in JavaScript inside
# opportunity.html, where the colour had already drifted (text-red-400
# instead of text-red-600 above 90). no-duplicate-logic.instructions.md
# lists this table as the single source.
DIFFICULTY_BANDS = [
    (15, "Very Easy", "text-green-400"),
    (35, "Easy", "text-green-300"),
    (55, "Moderate", "text-yellow-400"),
    (75, "Hard", "text-orange-400"),
    (90, "Very Hard", "text-red-400"),
    (101, "Extreme", "text-red-600"),
]


# The line above every keyword table the AI tabs hand to a model, so the
# model reads the two values the way the screens mean them
# (KEYWORD_DECISIONS_PLAN.md, D4).
OPPORTUNITY_TABLE_NOTE = (
    "Opportunity is what a keyword is worth to this app today; At #1 is what it "
    "is worth to whoever holds the top spot, on the same 0 to 100 scale. A keyword "
    "low today but high At #1 is worth targeting, since it pays as the app climbs; "
    "one low on both is worth little."
)


def keyword_rank_key(row, country=None) -> tuple:
    """How the AI tabs order keywords: worth today first, then worth at #1,
    so a new app's keywords, mostly low today, still come in a useful order."""
    return (row.get("opportunity") or 0, top_spot_opportunity(row.get("popularity") or 0, country))


def _difficulty_band(score) -> tuple[int, str, str]:
    value = score or 0
    for band in DIFFICULTY_BANDS:
        if value <= band[0]:
            return band
    return DIFFICULTY_BANDS[-1]


def difficulty_label(score) -> str:
    """Human readable difficulty interpretation, e.g. 45 -> "Moderate"."""
    return _difficulty_band(score)[1]


def difficulty_color(score) -> str:
    """Tailwind text colour class for a difficulty score."""
    return _difficulty_band(score)[2]


# The chip each band takes where a table draws difficulty on a tinted
# background (the AI tabs): the band's own text colour on a matching tint.
# The AI tabs used to colour difficulty by their own cut-offs (amber from 31)
# while the Difficulty column elsewhere called anything up to 35 Easy, in
# green (2026-09-23).
DIFFICULTY_CHIP = {
    "Very Easy": "bg-green-900/30 text-green-400",
    "Easy": "bg-green-900/30 text-green-300",
    "Moderate": "bg-yellow-900/30 text-yellow-400",
    "Hard": "bg-orange-900/30 text-orange-400",
    "Very Hard": "bg-red-900/30 text-red-400",
    "Extreme": "bg-red-900/40 text-red-500",
}


def difficulty_chip(score) -> str:
    """Tinted chip classes for a difficulty score, from its band."""
    return DIFFICULTY_CHIP[difficulty_label(score)]


# All valid classification labels, best first: what the legend, the filter
# and the badges show. Five, each one range of one number on screen
# (classify_keyword). Hidden Gem, Avoid and Moderate were retired on
# 2026-09-23 (KEYWORD_DECISIONS_PLAN.md, round 4): Hidden Gem and Avoid
# judged the difficulty a second time, beside the column that shows it, and
# Moderate shared its name with a difficulty band on the same row.
CLASSIFICATION_LABELS = [
    "Sweet Spot",
    "Good Target",
    "Supporting",
    "Worth Climbing",
    "Low Volume",
]

# The four or five words under each label in the legend and the filter. The
# long version lives in _TARGETING below; this is what fits on a chip.
# The solid colour each label takes in the keyword mix bar. Separate from the
# chip colours above because a 2px bar segment needs a flat fill, not a tint.
CLASSIFICATION_BAR = {
    "Sweet Spot": "bg-green-500",
    "Good Target": "bg-emerald-500",
    "Supporting": "bg-blue-500",
    "Worth Climbing": "bg-yellow-500",
    "Low Volume": "bg-slate-600",
}

# The stroke colour of the progress ticker's arc. An SVG stroke cannot take a
# Tailwind class, so the hex sits beside the classes rather than in a seventh
# table in JavaScript.
CLASSIFICATION_RING = {
    "Sweet Spot": "#22c55e",
    "Good Target": "#22c55e",
    "Supporting": "#3b82f6",
    "Worth Climbing": "#eab308",
    "Low Volume": "#64748b",
}

# What to do with a keyword: every label belongs to one decision, and the
# badge's explanation opens with it. Target now when it is worth something
# to the app today; target and climb when the top spot pays well but the app
# gets little at its rank today, so the keyword pays as the app climbs; skip
# when even the top spot pays little (KEYWORD_DECISIONS_PLAN.md, D3 and D7).
CLASSIFICATION_DECISION = {
    "Sweet Spot": "Target now",
    "Good Target": "Target now",
    "Supporting": "Target now",
    "Worth Climbing": "Target and climb",
    "Low Volume": "Skip",
}

# Each line is exactly the range the tag stands for, true of every row that
# carries it: "a day" is what the app (or a new app) can expect, read from
# the Opportunity score, except for Low Volume, which reads the Downloads at
# #1 column. A test holds every row of a sweep to these words.
CLASSIFICATION_SUMMARY = {
    "Sweet Spot": "10 or more downloads a day",
    "Good Target": "1 to 10 downloads a day",
    "Supporting": "One every 10 days to 1 a day",
    "Worth Climbing": "Little today, 1+ a day at #1",
    "Low Volume": "Under 1 a day even at #1",
}


# The difficulty factors as the breakdown shows them: the breakdown key, the
# yardstick weight it carries, the name on screen and what it measures. The
# percentage on screen is read from aso.strength.WEIGHTS, so it is always the
# one the score uses. It used to be typed into two JavaScript copies.
DIFFICULTY_FACTORS = [
    ("rating_volume", "volume", "Rating count",
     "How many ratings the apps already ranking have, in the middle of the field. "
     "The more they have, the harder they are to outrank."),
    ("review_velocity", "momentum", "Rating growth speed",
     "How fast those apps gain new ratings. Apps that grow fast are actively "
     "maintained and harder to pass."),
    ("dominant_players", "dominance", "Big-brand presence",
     "Whether big-name apps dominate the results. Big brands are hard to displace."),
    ("title_relevance", "title", "Keyword in titles",
     "How many of those apps carry this keyword in their title. The more do, "
     "the harder it is to stand out for it."),
    ("rating_quality", "rating", "Competitor ratings",
     "The average star rating of those apps. Highly rated apps are harder to displace."),
    ("market_age", "age", "Market maturity",
     "How long those apps have been on the App Store. Older apps are more entrenched."),
    ("publisher_diversity", "publishers", "Publisher variety",
     "Whether many different publishers compete, or a few dominate."),
]


def difficulty_factor_legend() -> list[dict]:
    """The factors behind a difficulty score, for every breakdown on screen."""
    from .strength import WEIGHTS

    return [
        {"key": key, "label": label, "weight": round(WEIGHTS[weight] * 100), "tip": tip}
        for key, weight, label, tip in DIFFICULTY_FACTORS
    ]


def classification_legend() -> list[dict]:
    """Every label, in the order the legend shows them, ready to render.

    The dashboard used to spell all seven out in the template, twice, with
    their own summaries. A label added here now appears in the legend, in the
    insight filter and on the badges without anyone editing HTML.
    """
    out = []
    for label in CLASSIFICATION_LABELS:
        icon, _, css, _ = _TARGETING[label]
        out.append({
            "label": label,
            "icon": icon,
            "css": css,
            "bar": CLASSIFICATION_BAR[label],
            "ring": CLASSIFICATION_RING[label],
            "summary": CLASSIFICATION_SUMMARY[label],
            "decision": CLASSIFICATION_DECISION[label],
            "description": _TARGETING[label][3],
        })
    return out


# The bars, on round numbers of downloads a day. The opportunity scale moves
# 20 points for every tenfold change, so 70 is ten a day, 50 one a day and 30
# one every ten days: each tag is one decade of what the app can expect. They
# used to sit at 75, 55, 35 and 25 (about 18, 2, a fifth and a twentieth of a
# download a day), which nobody could say in a sentence.
SWEET_SPOT_SCORE = 70
GOOD_TARGET_SCORE = 50
SUPPORTING_SCORE = 30
# Real demand: #1 brings at least one download a day, at the low end of the
# Downloads at #1 column, the conservative end every score counts on.
REAL_DEMAND_PER_DAY = 1.0


def as_shown(per_day: float) -> float:
    """A download figure as the tables print it (fmt_downloads), read back as
    a number: "0.9" is 0.9. A bar compared with this can never disagree with
    the column a reader checks it against."""
    text = fmt_downloads(per_day)
    if text.endswith("K"):
        return float(text[:-1]) * 1000
    return float(text)


# The colour of an Opportunity score, on the same bars as the tags it
# decides: green from ten downloads a day, emerald from one, blue from one
# every ten days (the Supporting tag's colour), grey below. The AI tabs used
# to colour it on the popularity scale and the Opportunity Finder on a third.
_OPPORTUNITY_COLOURS = (
    # (from score, text, tinted chip): whole class names, so the Tailwind
    # build finds them in this file.
    (SWEET_SPOT_SCORE, "text-green-300", "bg-green-900/30 text-green-300"),
    (GOOD_TARGET_SCORE, "text-emerald-300", "bg-emerald-900/30 text-emerald-300"),
    (SUPPORTING_SCORE, "text-blue-300", "bg-blue-900/30 text-blue-300"),
    (0, "text-slate-400", "bg-slate-700/40 text-slate-400"),
)


def opportunity_css(score, chip=False) -> str:
    """Tailwind classes for an Opportunity score: text only, or a tinted chip."""
    for bar, text, tinted in _OPPORTUNITY_COLOURS:
        if (score or 0) >= bar:
            return tinted if chip else text
    return _OPPORTUNITY_COLOURS[-1][2 if chip else 1]


def has_real_demand(popularity, country=None) -> bool:
    """Whether #1 here brings at least one download a day, as the low end of
    the Downloads at #1 column shows it. A fact about the keyword in this
    storefront, the same for every app and every difficulty."""
    return as_shown(top_spot_range(popularity, country)[0]) >= REAL_DEMAND_PER_DAY


def classify_keyword(popularity: int, difficulty: int, country=None,
                     app=None, app_rank=None, keyword=None) -> str:
    """Classify a keyword: what to do with it, for this app or a new app.

    This is the SINGLE SOURCE OF TRUTH for keyword classification.
    Every consumer (Dashboard, Opportunity, Researcher, Competitor,
    Simulator, CSV export, insight filters) must use this function
    or read a value that was produced by it.  Never duplicate this
    logic in JavaScript, SQL, or Q-objects.

    Each tag is one range of one number the row shows:

        Low Volume      #1 brings under 1 download a day (Downloads at #1)
        Sweet Spot      the app can expect 10 or more a day (Opportunity 70+)
        Good Target     1 to 10 a day (50 to 69)
        Supporting      one every 10 days to 1 a day (30 to 49)
        Worth Climbing  less than that today, but #1 brings 1+ a day

    Demand is decided first, and from the keyword alone: the same keyword in
    the same storefront is Low Volume for every app and at every difficulty,
    or for none. On 2026-09-23 two keywords with identical demand read Low
    Volume and Moderate, because the ceiling was only consulted when the
    app's own value was low (KEYWORD_DECISIONS_PLAN.md, round 4).
    Difficulty is not judged here a second time: it has its own column, and
    it already decides where the app lands, which is what the score counts.
    """
    if not has_real_demand(popularity, country):
        return "Low Volume"
    opportunity = calc_opportunity(popularity, difficulty, country, app, app_rank, keyword)
    if opportunity >= SWEET_SPOT_SCORE:
        return "Sweet Spot"
    if opportunity >= GOOD_TARGET_SCORE:
        return "Good Target"
    if opportunity >= SUPPORTING_SCORE:
        return "Supporting"
    return "Worth Climbing"


def fmt_downloads(n) -> str:
    """A download figure as every table prints it: "0.4", "5.9", "24", "1.2K".

    The one formatter behind the Downloads at #1 cell, the App Summary, the
    markdown export and the sentences that quote the same figures, so a
    tooltip never says a number the column beside it does not. The twin of
    fmt() in static/js/ai-tabs-shared.js.
    """
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "0"
    if n >= 1000:
        text = f"{n / 1000:.1f}"
        return (text[:-2] if text.endswith(".0") else text) + "K"
    if n < 1:
        return f"{n:.1f}"
    if n < 10:
        text = f"{n:.1f}"
        return text[:-2] if text.endswith(".0") else text
    return str(round(n))


def top_spot_range(popularity, country=None) -> tuple[float, float]:
    """Downloads a day at #1, low and high, rounded as the Downloads at #1
    column rounds them (DownloadEstimator.estimate)."""
    if not popularity or popularity <= 0:
        return 0.0, 0.0
    from .services import DownloadEstimator

    low, high = DownloadEstimator().range_at(popularity, 1, country=country or "us")
    return round(low, 2), round(high, 2)


def downloads_range_phrase(low: float, high: float) -> str:
    """A download range in words, with the figures the table shows:
    "5.9 to 24 downloads a day". When the low end would print as 0.0 the
    figures stop meaning anything, so it says the most it can be instead."""
    if high <= 0:
        return "no downloads"
    if fmt_downloads(low) != "0.0":
        return f"{fmt_downloads(low)} to {fmt_downloads(high)} downloads a day"
    return "at most " + downloads_phrase(high).removeprefix("about ")


def downloads_phrase(per_day: float) -> str:
    """A download figure a person can picture.

    Three a day stays three a day. A fiftieth of one becomes "one every seven
    weeks", because nobody reads 0.02 as anything at all.
    """
    if per_day >= 10:
        return f"about {per_day:,.0f} downloads a day"
    if per_day >= 1.5:
        return f"about {per_day:.1f} downloads a day"
    if per_day <= 0:
        return "no downloads"
    days = 1 / per_day
    if days < 1.5:
        return "about one download a day"
    if days >= 1825:
        # Past about five years the figure stops being information. Say so.
        return "effectively nothing"
    if days < 60:
        return f"about one download every {days:.0f} days"
    if days < 730:
        return f"about one download every {days / 30:.0f} months"
    return f"about one download every {days / 365:.0f} years"


def searches_phrase(per_day: float) -> str:
    """Searches a day, rounded the way a person would say them."""
    if per_day >= 100:
        return f"about {per_day:,.0f} searches a day"
    if per_day >= 10:
        return f"about {per_day:.0f} searches a day"
    if per_day >= 1:
        return f"about {per_day:.1f} searches a day"
    return "under one search a day"


def _store_name(country) -> str:
    """"the Argentina App Store". Naming the store sidesteps the article that
    "the United States" needs and "Argentina" does not."""
    from aso import countries

    return f"the {countries.name(country or 'us')} App Store"


def short_app_name(name) -> str:
    """The part of an App Store name that fits under a score.

    "Calm Minutes: Meditation & Sleep" becomes "Calm Minutes"; anything
    still longer than 16 characters is cut with an ellipsis. The tooltip
    carries the full name.
    """
    base = sentence_app_name(name)
    return base if len(base) <= 16 else base[:15].rstrip() + "\u2026"


def sentence_app_name(name) -> str:
    """The app's name as a sentence would use it: the brand, without the
    App Store subtitle. "Calm Minutes: Meditation & Sleep" reads "Calm
    Minutes". Never shortened further, so it is always a real name."""
    full = (name or "").strip()
    base = full
    for separator in (":", " - ", " \u2013 ", " \u2014 ", " |"):
        if separator in base:
            base = base.split(separator, 1)[0]
    return base.strip() or full


def _ratings_text(count) -> str:
    if not count or count <= 0:
        return "no ratings yet"
    return f"{count:,} rating{'s' if count != 1 else ''}"


def _age_text(years) -> str:
    if years is None:
        return ""
    if years < 1:
        months = max(1, round(years * 12))
        return f"{months} month{'s' if months != 1 else ''}"
    whole = round(years)
    return f"{whole} year{'s' if whole != 1 else ''}"


def _profile_text(profile) -> str:
    """"1,234 ratings averaging 4.6 stars over 3 years": what the app brings."""
    from .strength import years_since

    text = _ratings_text(profile.ratings)
    if profile.ratings and profile.average:
        text += f" averaging {profile.average:.1f} stars"
    age = _age_text(years_since(profile.released))
    if age:
        text += f" over {age}"
    return text


def quoted_keyword(keyword) -> str:
    """The keyword as a sentence names it, in quotes, so a tooltip says which
    row it belongs to; "this keyword" when it is not known. The owner read
    "Options Trading AI has this keyword in its title and ranks #2" over a
    table with two rows it could belong to, and took the #2 for the other
    row's #14 (2026-09-23)."""
    text = (keyword or "").strip()
    return f"\u201c{text}\u201d" if text else "this keyword"


def opportunity_reach(popularity, difficulty, country=None, app=None, app_rank=None,
                      keyword=None) -> dict:
    """Where an app could rank for a keyword, what that pays, and why.

    The opportunity score is the downloads at this rank, so this is the
    score's working shown: the table puts ``label`` under the number and
    ``explanation`` behind it, and the result card prints the explanation in
    full. Composed here, once, so every screen says the same thing.

    ``app`` is the tracked app's AppProfile in this storefront, or None; a
    profile marked unknown is an app whose store data has not been read yet.
    ``app_rank`` is its real rank for this keyword here, when known, and
    ``keyword`` the keyword, which decides whether that rank is final.

    Returns:
        position     the effective rank (whose taps the app can expect), rounded
        downloads    downloads a day at that rank, unrounded
        label        the short line under the score, e.g. "Calm Minutes: typically
                     ~#40", or "Calm Minutes ranks #2" when its real rank is used
        explanation  whose rank it is, where it comes from, what it pays
        name         the app the rank belongs to, or None for a new app
        in_title     whether that app's title already carries the keyword
        real_rank    whether the position is the app's real rank
        searches     searches a day for the keyword here
        typical      where the middle app like this one lands (TYPICAL_BY_GAP)
        top_ten      the share of such apps that reach the top 10
    """
    position = reachable_position(difficulty, app, app_rank, keyword)
    model_position = reachable_position(difficulty, app)
    searches = daily_searches(popularity, country)
    downloads = expected_downloads(popularity, difficulty, country, app, app_rank, keyword)
    store = _store_name(country)
    band = difficulty_label(difficulty)

    name = sentence_app_name(app.name) if app is not None and app.name else None
    known = name is not None and getattr(app, "known", True)
    in_title = name is not None and title_carries(app, keyword)
    # The real rank decides when the title already carries the keyword (it
    # is what carrying it does for this app), or when it beats the model.
    real_rank_decides = bool(app_rank) and name is not None and (
        in_title or app_rank < model_position
    )
    stronger = known and not real_rank_decides and app_strength(app) > _new_app_strength()

    typical, top_ten = typical_landing(difficulty, app if known else None)
    who_short = short_app_name(name) if (known or real_rank_decides) else "new app"
    if real_rank_decides:
        label = f"{who_short} ranks #{position}"
    else:
        label = f"{who_short}: typically {'' if typical > 200 else '~'}{rank_text(typical)}"

    whose = f"apps as strong as {name}" if stronger else "new apps"
    why = (
        f"the difficulty of {difficulty or 0} ({band}), which measures how "
        "strong the apps already ranking for this keyword are"
    )
    if stronger:
        why += f", set against the same measures for {name}"
    why += f", and from where {whose} actually rank in App Store searches"

    prefix = ""
    kw = quoted_keyword(keyword)
    if real_rank_decides and in_title:
        opening = (
            f"{name} has {kw} in its title and ranks #{app_rank} for it "
            f"in {store}, so that is the rank this uses."
        )
    elif real_rank_decides:
        opening = (
            f"{name} already ranks #{app_rank} in the search results for {kw} "
            f"in {store}, so that is the rank this uses."
        )
    elif known and app_rank:
        # It ranks, without the keyword in its title and not better than the
        # model: say both facts, never a "typical" rank that reads as worse
        # than the one it already holds.
        opening = (
            f"{name} has {_profile_text(app)} in {store}, and ranks #{app_rank} for "
            f"{kw} without it in its title. Apps that strong, with the "
            "keyword in their title, land all over the search results; the middle "
            f"one lands {_around(typical)}"
            + (", so this app already does better than most of them."
               if app_rank < typical else ".")
        )
    elif known:
        opening = (
            f"{name} has {_profile_text(app)} in {store}. Apps that strong, "
            f"with {kw} in their title, land all over the search "
            f"results; the middle one lands {_around(typical)}."
        )
        new_typical = typical_landing(difficulty)[0]
        if stronger and new_typical != typical:
            opening += f" A brand new app typically lands {_around(new_typical)}."
    else:
        if name:
            prefix = (
                f"RespectASO has not read {name}'s ratings in {store} yet, "
                "so this assumes a brand new app. "
            )
        opening = (
            f"Brand new apps, with no ratings yet, that put {kw} in "
            f"their title land all over the search results in {store}; the "
            f"middle one lands {_around(typical)}."
        )

    past_first_page = (
        " That is past the first 20 results, where far fewer people scroll."
        if position > WORST_POSITION else ""
    )
    # A low score always shows its ceiling: what the keyword pays whoever
    # holds the top spot, the part that can change as the app grows. The
    # range is the one the Downloads at #1 column shows.
    ceiling = ""
    if position > 1 and searches >= MIN_DAILY_SEARCHES:
        at_first = downloads_range_phrase(*top_spot_range(popularity, country))
        ceiling = f" Holding #1 here would bring {at_first}."

    if searches < MIN_DAILY_SEARCHES:
        subject = name if known else "A brand new app"
        where = (f"already ranks #{app_rank}" if real_rank_decides
                 else f"would typically land {_around(typical)}, judging by {why}")
        explanation = (
            f"{prefix}This keyword gets under one search a day in {store}, so "
            f"there is little to win at any rank. {subject} {where}."
            f"{past_first_page if real_rank_decides else ''}"
        )
    elif real_rank_decides:
        explanation = (
            f"{prefix}{opening} At #{position}, with "
            f"{searches_phrase(searches)} here, that is "
            f"{downloads_phrase(downloads)}, and that is what the Opportunity "
            f"score measures.{past_first_page}{ceiling}"
        )
    else:
        # The score is the average over where apps land. Said as downloads,
        # never as "the downloads of #14": that rank is where almost none of
        # them land, and read as a place it misleads.
        subject = name if known else "a new app"
        if typical > 10:
            share = (" the few near the top get most of the downloads, so "
                     f"averaged over all of them, {subject}")
        else:
            share = f" averaged over where they land, {subject}"
        explanation = (
            f"{prefix}{opening} That comes from {why}. With "
            f"{searches_phrase(searches)} here,{share} can expect "
            f"{downloads_phrase(downloads)}, and that is what the Opportunity "
            f"score measures.{ceiling}"
        )
    if stronger:
        explanation += (
            " The same keyword scores differently for each of your apps, "
            "because a stronger app ranks higher."
        )

    return {
        "position": position,
        "downloads": downloads,
        "label": label,
        "explanation": explanation,
        # Whose rank this is: the app's name when it is the app's own (its
        # profile is known, or its real rank decides), else None for a new
        # app. The badge sentence speaks of the one or the other.
        "name": name if (known or real_rank_decides) else None,
        "in_title": in_title,
        "real_rank": real_rank_decides,
        "searches": searches,
        # Where the middle app like this one lands, and the share in the top
        # 10: the words on screen for the model's rank.
        "typical": typical,
        "top_ten": top_ten,
    }


def opportunity_basis(popularity, difficulty, country=None, app=None, app_rank=None,
                      keyword=None) -> str:
    """The sentence that shows the arithmetic behind an opportunity score.

    The explanation half of opportunity_reach(), for the screens that print
    it in full rather than behind a tooltip.
    """
    return opportunity_reach(
        popularity, difficulty, country, app=app, app_rank=app_rank, keyword=keyword,
    )["explanation"]


def scored_for(app=None, country=None, *, reason=None) -> str:
    """One sentence above a keyword table saying whose score it is.

    ``reason`` covers two surfaces: "competitor" (the AI Competitor tab
    analyses someone else's app) and "each_storefront" (a Finder scan for an
    app, across many storefronts).
    """
    if reason == "competitor":
        return (
            "Opportunity is scored for a brand new app, because the app "
            "analysed here is a competitor's, not yours."
        )
    name = sentence_app_name(app.name) if app is not None and app.name else None
    if name and reason == "each_storefront":
        # An app added by hand has no App Store listing, so no storefront has
        # ratings to read and every row scores it as a brand new app.
        if not getattr(app, "known", True):
            return (
                f"Opportunity is scored for a brand new app, because {name} was "
                "added by hand and has no App Store ratings to read."
            )
        return (
            f"Opportunity is scored for {name}, from its ratings, average "
            "stars and age in each storefront."
        )
    store = _store_name(country)
    if name and getattr(app, "known", True):
        if not app.ratings:
            return (
                f"Opportunity is scored for {name}, which has no ratings in "
                f"{store} yet, so it scores like a brand new app."
            )
        return (
            f"Opportunity is scored for {name}, from its ratings, average "
            f"stars and age in {store} ({_profile_text(app)})."
        )
    if name:
        return (
            "Opportunity is scored for a brand new app, because RespectASO "
            f"could not read {name}'s ratings in {store}."
        )
    return (
        "Opportunity is scored for a brand new app, because this is not tied "
        "to one of your apps."
    )


# The storefront the scoring guide uses to show that popularity is relative.
# Any mid sized market makes the point; Argentina is the one that was asked
# about, a keyword at popularity 48 that the old guide called good volume.
GUIDE_EXAMPLE_POPULARITY = 48
GUIDE_EXAMPLE_COUNTRY = "ar"


def _named_list(names) -> str:
    """"United States, Brazil and France"."""
    names = list(names)
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def scoring_guide() -> dict:
    """The dashboard's scoring guide, built from the scoring code itself.

    It used to be typed into the template and graded popularity on its own:
    "30 to 49, good search volume". Popularity is an index, so that verdict
    was true in one storefront and false in another, and the guide argued
    with the Low Volume label on the row beneath it. Every figure here now
    comes out of the same functions that score the rows.
    """
    per_decade = OPPORTUNITY_POINTS_PER_DECADE
    # Each band names the tag that starts there, from the same bars
    # classify_keyword uses, so the guide and the tags cannot drift apart.
    starts = {SWEET_SPOT_SCORE: "Sweet Spot", GOOD_TARGET_SCORE: "Good Target",
              SUPPORTING_SCORE: "Supporting"}
    opportunity = []
    for score in (SWEET_SPOT_SCORE, GOOD_TARGET_SCORE, SUPPORTING_SCORE, 10):
        per_day = OPPORTUNITY_MIDPOINT_DOWNLOADS * 10 ** ((score - 50) / per_decade)
        opportunity.append({
            "range": f"{score}+" if score == SWEET_SPOT_SCORE else str(score),
            "meaning": downloads_phrase(per_day).capitalize(),
            "tag": starts.get(score, ""),
        })
    floor = OPPORTUNITY_MIDPOINT_DOWNLOADS * 10 ** (-50 / per_decade)
    opportunity.append({
        "range": "0",
        "meaning": f"{downloads_phrase(floor).capitalize()}, or fewer",
    })

    difficulty = []
    low = 0
    for bound, label, css in DIFFICULTY_BANDS:
        high = min(bound, 100)
        first = typical_landing(low)[0]
        last = typical_landing(high)[0]
        where = rank_text(first) if first == last else f"{rank_text(first)} to {rank_text(last)}"
        difficulty.append({
            "range": f"{low}-{high}",
            "label": label,
            "css": css,
            "where": where,
        })
        low = high + 1

    from aso import countries

    # Where a brand new app typically lands at three points of the difficulty
    # scale, and how many reach the top 10, for the methodology page's
    # sentence, so it can never drift from the table.
    new_app = {}
    for key, point in (("easy", 10), ("middling", 45), ("hard", 75)):
        typical, top_ten = typical_landing(point)
        new_app[key] = typical
        new_app[f"{key}_top_ten"] = top_ten_phrase(top_ten)

    us = daily_searches(GUIDE_EXAMPLE_POPULARITY, "us")
    there = daily_searches(GUIDE_EXAMPLE_POPULARITY, GUIDE_EXAMPLE_COUNTRY)
    return {
        "opportunity": opportunity,
        "difficulty": difficulty,
        "new_app": new_app,
        "rank_study": {
            "searches": f"{RANK_STUDY['searches']:,}",
            "apps": f"{RANK_STUDY['apps']:,}",
            "storefronts": _named_list(countries.name(c) for c in RANK_STUDY["storefronts"]),
        },
        "popularity_example": {
            "popularity": GUIDE_EXAMPLE_POPULARITY,
            "us": f"{round(us, -1):,.0f}",
            "there": f"{there:.0f}",
            "there_name": countries.name(GUIDE_EXAMPLE_COUNTRY),
        },
    }


# ── Targeting advice tuples ── (icon, label, css_classes, description)

_TARGETING = {
    "Sweet Spot": ("🎯", "Sweet Spot", "bg-green-900/20 text-green-300 border-green-500/20",
                   "Target now. Ten or more downloads a day at the rank you can expect."),
    "Good Target": ("✅", "Good Target", "bg-green-900/20 text-green-300 border-green-500/20",
                    "Target now. One to ten downloads a day at the rank you can expect."),
    "Supporting": ("👍", "Supporting", "bg-blue-900/20 text-blue-300 border-blue-500/20",
                   "Target now, as a supporting keyword. Between one download every ten days "
                   "and one a day at the rank you can expect."),
    "Worth Climbing": ("🌱", "Worth Climbing", "bg-yellow-900/20 text-yellow-300 border-yellow-500/20",
                       "Worth targeting. #1 brings a download a day or more, but the rank you "
                       "can expect today brings little. It pays more as the app climbs, and a "
                       "title with it also ranks for longer searches that contain it."),
    "Low Volume": ("🔍", "Low Volume", "bg-slate-800 text-slate-300 border-white/10",
                   "Skip. Even #1 brings under one download a day here."),
}

# What each "target now" label adds after where the app stands.
_TARGET_NOW_TAIL = {
    "Sweet Spot": " One of the best keywords you can pick.",
    "Good Target": "",
    "Supporting": " Worth a place as a supporting keyword.",
}

def targeting_description(label, popularity, difficulty, country=None, app=None,
                          app_rank=None, keyword=None, reach=None) -> str:
    """The badge's sentence for this row: the decision, and why, for this app
    by name or for a new app (KEYWORD_DECISIONS_PLAN.md, D6).

    Built from the facts of opportunity_reach(), so the badge and the score
    beside it never disagree on whose rank it is or what it brings. The fixed
    sentences in _TARGETING are for the legend, which describes a tag rather
    than a row.
    """
    if reach is None:
        reach = opportunity_reach(popularity, difficulty, country, app=app,
                                  app_rank=app_rank, keyword=keyword)
    position = reach["position"]
    now = downloads_phrase(reach["downloads"])
    name = reach["name"]
    store = _store_name(country)
    typical = reach["typical"]
    top = downloads_range_phrase(*top_spot_range(popularity, country))
    averages = ("; counting the few that get near the top, that averages"
                if typical > 10 else ", which averages")
    kw = quoted_keyword(keyword)
    if name and reach["real_rank"] and reach["in_title"]:
        stands = f"{name} ranks #{position} with {kw} in its title, which brings {now}."
    elif name and reach["real_rank"]:
        stands = f"{name} already ranks #{position} for {kw}, which brings {now}."
    elif name and app_rank and app_rank < typical:
        # It already ranks better than the middle app as strong: never say
        # it "typically lands" below the rank it holds (the owner's #23 row
        # read "typically ~#52", 2026-09-23).
        stands = (f"{name} ranks #{app_rank} for {kw} without it in its title, better than "
                  f"most apps as strong do with it there{averages} {now}.")
    else:
        # Where such apps typically land, and what that averages: never the
        # effective rank as a place (see TYPICAL_BY_GAP).
        lands = (f"apps as strong as {name}, with {kw} in their title, typically land"
                 if name else f"a new app with {kw} in its title typically lands")
        lands = f"{lands} {_around(typical)}"
        stands = f"{lands[0].upper()}{lands[1:]}{averages} {now}."

    if label in _TARGET_NOW_TAIL:
        upside = f" #1 here brings {top}." if position > 1 else ""
        return f"Target now. {stands}{_TARGET_NOW_TAIL[label]}{upside}"
    if label == "Worth Climbing" and not reach["real_rank"]:
        # Where it lands is in the score's own explanation; the tag says
        # the decision and the two figures it rests on.
        subject = name or "a new app"
        return (f"Worth targeting. #1 for {kw} here brings {top}, but {subject} can expect only "
                f"{now} for now. It pays more as the app gains ratings, and a title with it "
                "also ranks for longer searches that contain it.")
    if label == "Worth Climbing":
        if name and reach["in_title"]:
            climb = "It pays more as the app gains ratings and climbs."
        else:
            climb = "Putting it in the title is how it climbs, and it pays more as it does."
        return f"Worth targeting. #1 for {kw} here brings {top}. {stands} {climb}"
    # Low Volume: the keyword, not the app, is the limit.
    if reach["searches"] < MIN_DAILY_SEARCHES:
        skip = f"Skip. {kw[0].upper()}{kw[1:]} gets under one search a day in {store}."
    else:
        only = "" if top.startswith("at most") else "only "
        skip = (f"Skip. Even #1 for {kw} in {store} brings {only}{top}, too few searches "
                "to be worth a place in the listing.")
    if name and app_rank:
        skip += (f" {name} already ranks #{app_rank} for it: keep it where it costs "
                 "nothing, but do not give it a title or subtitle slot.")
    return skip

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


def get_targeting_advice(popularity, difficulty, country=None, app=None, app_rank=None,
                         keyword=None):
    """Return (icon, label, css_classes, description) for ASO targeting.

    The single source of truth for the badge; the label comes from
    classify_keyword() for the same app, and the description is composed for
    this row (targeting_description).
    """
    if popularity is not None:
        label = classify_keyword(popularity, difficulty, country, app, app_rank, keyword)
        icon, _, css, _ = _TARGETING[label]
        return icon, label, css, targeting_description(
            label, popularity, difficulty, country, app, app_rank, keyword,
        )
    # Difficulty-only (popularity unavailable)
    for threshold, advice in sorted(_DIFF_ONLY_TARGETING.items()):
        if difficulty <= threshold:
            return advice
    return _DIFF_ONLY_DEFAULT


def targeting_payload(popularity, difficulty, country=None, app=None,
                      app_rank=None, keyword=None) -> dict:
    """What a classification badge needs, ready to render.

    Every screen that shows the badge reads this. ``reach`` is the line under
    the score and its explanation, for the same app and rank.
    """
    icon, label, css, description = get_targeting_advice(
        popularity, difficulty, country, app, app_rank, keyword
    )
    reach = opportunity_reach(popularity, difficulty, country, app=app, app_rank=app_rank,
                              keyword=keyword)
    return {
        "icon": icon,
        "label": label,
        "css": css,
        "description": description,
        "basis": reach["explanation"],
        "reach_label": reach["label"],
    }
