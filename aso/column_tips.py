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
    "picked": "for the app you picked",
    "tracked": "for the app under each keyword",
    "finder": "for a new app",
}

OPPORTUNITY_TIPS = {
    "new_app": (
        "What the keyword is worth to a brand new app: the downloads a day it can "
        "expect with the keyword in its title."
    ),
    "app": (
        "What the keyword is worth to the simulated app, at the rank an app this "
        "strong can reach."
    ),
    "picked": (
        "What the keyword is worth to the app you picked, at the rank an app this "
        "strong can reach."
    ),
    "tracked": (
        "The one to act on: what the keyword is worth to the app named under it, "
        "or a new app, at a rank it can reach."
    ),
    "finder": (
        "The one to act on: what the keyword is worth in each storefront to the app "
        "you picked, or a new app."
    ),
}

COLUMN_TIPS = {
    "keyword": "The search term, exactly as people type it into the App Store.",
    "term": (
        "A word or phrase from the recommended title, subtitle and keyword field, "
        "the way Apple matches it against searches."
    ),
    "source": (
        "Where the keyword comes from: your seed, the AI, a metadata field or words "
        "combined across fields."
    ),
    "popularity": (
        "How sought after the keyword is. It is an index, not a number of searches: "
        "compare countries by Opportunity."
    ),
    "difficulty": (
        "How hard it is to rank, 0 to 100, judged by the apps already ranking: their "
        "ratings, growth, brands and titles."
    ),
    "downloads": (
        "What the #1 app gets here in downloads a day: the prize, not what a given "
        "app gets. Hover for #1, #5 and #10."
    ),
    "classification": (
        "What to do with the keyword, from its Opportunity; Low Volume means even #1 "
        "brings under one download a day."
    ),
    "competitor_rank": (
        "Where the competitor ranks here among the App Store's top 200 results. A "
        "dash means it is not in them."
    ),
    "app_rank": (
        "Where the app ranks here today among the App Store's top 200 results. A "
        "dash means it is not in them."
    ),
    "status": (
        "Covered: already in the keyword table above. New: a term the AI added, "
        "checked on the App Store for real demand."
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
