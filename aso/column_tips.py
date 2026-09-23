"""What each keyword table column means, said once for every table.

The Dashboard, the Country Opportunity Finder and the six tables of the AI
tabs (Keyword Analysis and Metadata Coverage Analysis, in the Researcher,
the Competitor tab and the Simulator) all show the same columns. Their hover
text lives here, so a column never means one thing on one screen and
another thing on the next. Templates read it through the ``column_info`` tag
in aso/templatetags/aso_tags.py.

The Opportunity column is one number, and the words under its heading say
whose it is: a brand new app in the Researcher and the Competitor tab, the
simulated app in the Simulator, the scan's app (or a new app) in the
Opportunity Finder, and on the Dashboard the app named under each keyword.
It used to show two numbers, "43 → 76", under "now → at #1": the second
was Downloads at #1 again on another scale, and a keyword researched for an
app that does not exist yet has no "now" (the owner, 2026-09-23).
"""

# The words under the Opportunity heading, naming whose score it is. A table
# of one app names that app instead (opportunity_subline).
OPPORTUNITY_SUBJECTS = {
    "new_app": "for a new app",
    "app": "for this app",
    "tracked": "for the app under each keyword",
    "finder": "for a new app",
}

_OPPORTUNITY_SCALE = (
    " 50 is about one download a day, and every tenfold change moves it 20 points."
)
_UPSIDE = " What #1 pays is in the Downloads at #1 column."
_HOVER = " Hover a score for how it is worked out."

OPPORTUNITY_TIPS = {
    "new_app": (
        "What each keyword is worth to a brand new app: the downloads a day it can "
        "expect with the keyword in its title, from where new apps actually land in "
        "App Store searches." + _HOVER + _UPSIDE + _OPPORTUNITY_SCALE
    ),
    "app": (
        "What each keyword is worth to the app being simulated, judged by its "
        "ratings, average stars and age in this storefront: its real rank when it "
        "already ranks well, otherwise where apps as strong land with the keyword in "
        "their title." + _HOVER + _UPSIDE + _OPPORTUNITY_SCALE
    ),
    "tracked": (
        "The one to act on: what the keyword is worth in this storefront to the app "
        "named under it, at the rank that app can realistically reach, judged by its "
        "own ratings there. A keyword not tied to an app is scored for a brand new "
        "app, and says so." + _HOVER + _UPSIDE + _OPPORTUNITY_SCALE
    ),
    "finder": (
        "The one to act on, and what this table is sorted by: what the keyword is "
        "worth in each storefront to the app you picked, or to a brand new app if you "
        "picked none, at the rank that app can realistically reach there, judged by "
        "its own ratings in that storefront." + _HOVER + _UPSIDE + _OPPORTUNITY_SCALE
        + " That scale is why countries compare fairly."
    ),
}

COLUMN_TIPS = {
    "keyword": "The search term, exactly as people type it into the App Store.",
    "term": (
        "A word or phrase from the recommended title, subtitle and keyword field, "
        "the way Apple matches it against searches."
    ),
    "source": (
        "Where the keyword comes from: your seed keyword, the AI's suggestions, the "
        "fields of the metadata, or a combination of words across fields (Title + "
        "KF, for example). Hover a badge for its exact meaning."
    ),
    "popularity": (
        "How sought after this keyword is, on Apple's 1 to 100 scale. It is an "
        "index, not a number of searches: the same 41 means far more searches in a "
        "large storefront than in a small one. For what it is worth in this "
        "country, read Opportunity."
    ),
    "difficulty": (
        "How hard it is to rank, from 0 to 100, from 7 signals across the apps "
        "already ranking: their ratings, how fast they grow, brand strength, title "
        "relevance, rating quality, market maturity and how many publishers share "
        "the results."
    ),
    "downloads": (
        "What this keyword pays per day to whichever app holds #1 in the search "
        "results here: the size of the prize, not what a given app would get. "
        "What an app can realistically expect is the Opportunity score, with its "
        "rank on the line underneath. Hover a cell for #1, #5 and #10."
    ),
    "classification": (
        "What to do with the keyword. Low Volume: skip it, because even #1 brings "
        "under one download a day (the low end of Downloads at #1). Otherwise the "
        "Opportunity score decides: Sweet Spot from 10 downloads a day, Good Target "
        "from 1, Supporting from one every 10 days, and below that Worth Climbing. "
        "Hover a tag for what it means for that row."
    ),
    "competitor_rank": (
        "Where the competitor's app ranks for this keyword in this storefront, from "
        "a live App Store search of the top 200 results. A dash means it is not in "
        "them."
    ),
    "app_rank": (
        "Where the app ranks for this keyword in this storefront today, from a live "
        "App Store search of the top 200 results. A dash means it is not in them."
    ),
    "status": (
        "Covered: already scored in the keyword table above. New: a term the AI "
        "brought into the metadata that was not in that table, searched on the App "
        "Store to check it has real search volume."
    ),
}


def column_tip(column: str, subject: str = "new_app") -> str:
    """The hover text for one column. ``subject`` only matters for
    Opportunity: "new_app", "app", "tracked" or "finder"."""
    if column == "opportunity":
        return OPPORTUNITY_TIPS[subject]
    return COLUMN_TIPS[column]


def opportunity_subline(subject: str = "new_app", app_name: str | None = None) -> str:
    """The words under the Opportunity heading: "for a new app", or "for" the
    app's short name when the whole table is scored for one app."""
    if app_name:
        from .scoring import short_app_name

        return f"for {short_app_name(app_name)}"
    return OPPORTUNITY_SUBJECTS[subject]
