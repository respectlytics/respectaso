"""Sample-data preview of the Store section (STORE_AUTOPILOT_PLAN.md, step 11).

Shown wherever the real page cannot render: the free edition (a Pro
feature), a Pro install without a valid license, one whose license
expired (all three show every tab at once, keywords locked), and a Pro
install with no App Store Connect key yet (STATE_CONNECT: one tab, under
the section's tabs, with the Pro view's own call to action). It uses an
invented app, so people see what the section does before they get it.

Free-tier module: no aso_pro imports, no network, no database.
"""

from . import countries, pro_preview_words

STATE_FREE = "free"
STATE_UNLICENSED = "unlicensed"
STATE_EXPIRED = "expired"
STATE_CONNECT = "connect"
STATES = (STATE_FREE, STATE_UNLICENSED, STATE_EXPIRED)

# Each tab's title and lead, shared by the live tab and its sample; a tab
# whose title is a short label (the tab and the window title) has a heading.
TAB_WORDS = {
    "autopilot": {"title": "Get found", "heading": "Get found in more searches on the App Store",
                  "lead": "AI rewrites your name, subtitle, keywords and promotional text. You approve every change."},
    "pages": {"title": "Custom product pages",
              "lead": "Show the screenshots and words that fit each kind of search."},
    "results": {"title": "Sales & growth", "lead": "What you earn, where downloads come from, and what changed after each release."},
    "reviews": {"title": "Reviews", "lead": "Answer every review, by hand or drafted in the reviewer's language."},
}

# The call to action's headline on each tab: what that tab does for the reader.
TAB_HEADLINES = {
    "autopilot": "Get found in more searches on the App Store, and see what each change did",
    "pages": "Show each kind of search the screenshots and words that fit it",
    "results": "See what you earn, and how it changed after each release or store change",
    "reviews": "Answer every review in the reviewer's language",
}

# How Get found works, in four stages: the one story the Get found card, its
# guide (aso_pro store/how.html), this preview and respectaso.com all tell,
# in these words (owner, 2026-11-16). (icon, title, line)
STAGES = (
    ("find", "Find searches", "Phrases people search, from your store text and the keywords you track."),
    ("write", "AI rewrites your name, subtitle, keywords and promotional text", "In each language you choose, within Apple's limits."),
    ("approve", "You approve", "Untick what you don't want. Nothing changes before you apply."),
    ("chart", "See what it did", "Views and downloads before and after, on Sales & growth."),
)
# The third stage when "Apply it by itself, then tell me" is on (drafts the person starts).
STAGE_AUTO_APPLY = ("approve", "Applies, then tells you", "Restore puts any field back.")

# (field, locale, old, new, limit, keyword field); keyword fields are locked.
SAMPLE_FIELDS = (
    ("Name", "English (US)", "Habito", "Habito: Habit Tracker Daily", 30, False),
    ("Subtitle", "English (US)", "Build better days", "Streaks, goals and routines", 30, False),
    ("Keywords", "English (US)", "habit,streak,routine,goals", "planner,reminder,journal,morning,checklist", 100, True),
    ("Promotional text", "English (US)", "New streak reminders.",
     "Build habits that last with gentle reminders, streaks and a calm daily plan.", 170, False),
)


# How each page works, as a numbered flow, and why it matters, in one line.
# "store" is the whole section (the locked preview shows every tab).
# icon names are drawn by aso/partials/store_flow.html.
EXPLAINERS = {
    "autopilot": {"why": "The words on your store page decide which searches show your app.", "steps": STAGES},
    "pages": {
        "why": "People who search for one theme see the screenshots that fit it first.",
        "steps": (
            ("find", "Pick the searches", "A page your AI suggests, or your own."),
            ("write", "AI writes the promotional text", "For those searches, using the space."),
            ("approve", "AI puts the best screenshot first", "You can still move any of them."),
            ("show", "People searching see it", "Once you submit it and Apple approves."),
        ),
    },
    "results": {
        "why": "See how your numbers changed after each release, and where to look first.",
        "steps": (
            ("chart", "Apple's numbers, daily", "Proceeds, downloads, views and impressions, with your history."),
            ("mark", "Every release marked", "Each version and each store change on one timeline."),
            ("compare", "Where to look first", "The one step from seen to paid that needs you, in plain words."),
        ),
    },
    "reviews": {
        "why": "Your reply shows under the review, for everyone who reads it.",
        "steps": (
            ("review", "A review comes in", "From every country your app is in."),
            ("draft", "A reply is drafted", "In the reviewer's language, ready to edit."),
            ("reply", "You post it", "Apple shows it under the review."),
        ),
    },
}

# The same app in an App Store search, before and after (Why it matters):
# which searches its name and subtitle answer, never a rank it would reach.
SAMPLE_SEARCH = {
    "before": {"name": "Habito", "subtitle": "Build better days", "matches": ("habito", "better days")},
    "after": {"name": "Habito: Habit Tracker Daily", "subtitle": "Streaks, goals and routines",
              "matches": ("habit tracker", "daily habits", "streaks", "goals", "routines")},
}


# The sample app in two App Store searches, as the Search pages tab draws
# them: its store page for its usual search, and a search page for one theme
# with the same name and icon, its own first screenshots and its own
# promotional text. Each screenshot is a label, a colour and an icon that
# says what it shows (store_shot.html).
SAMPLE_PAGES = {
    "main": {"query": "habit tracker", "label": "Your store page",
             "name": "Habito: Habit Tracker Daily", "subtitle": "Streaks, goals and routines",
             "shots": (("Streaks", "sky", "flame"), ("Reminders", "amber", "bell"), ("Mornings", "emerald", "sun")),
             "promo": "Build habits that last with gentle reminders, streaks and a calm daily plan."},
    "theme": {"query": "morning routine", "label": "Custom product page: Morning Routine",
              "name": "Habito: Habit Tracker Daily", "subtitle": "Streaks, goals and routines",
              "shots": (("Mornings", "emerald", "sun"), ("Streaks", "sky", "flame"), ("Reminders", "amber", "bell")),
              "promo": "Start every morning the same calm way: wake up, stretch, plan, and keep the streak going."},
}

# The first meeting with a Store tab (aso/partials/store_intro.html, owner
# 2026-11-16: nobody should need to know already what the tab is): what it
# is in a title, how in one line, and a picture that explains on its own.
INTROS = {
    "autopilot": {"title": "Your words decide which searches find you",
                  "line": "Get found rewrites your name, subtitle, keywords and promotional text to match more searches."},
    "pages": {"title": "Show each search the screenshot that fits",
              "line": "A custom product page is your store page with its own first screenshot, for chosen searches."},
}

# The Get found picture: each part of the sample app's store page and the
# searches its words match.
SAMPLE_WORDS = (
    ("Name", "Habito: Habit Tracker Daily", ("habit tracker", "daily habits")),
    ("Subtitle", "Streaks, goals and routines", ("streaks", "goals")),
    ("Keywords", "planner,reminder,morning", ("habit planner", "morning routine")),
)

# Pages the sample app's AI suggests on the Search pages walkthrough, each
# with the searches it is for; the first is the one picked.
SAMPLE_PAGE_IDEAS = (
    {"name": "Morning routine", "searches": ("morning routine", "wake up routine", "morning habits")},
    {"name": "Habit streaks", "searches": ("streak tracker", "habit streaks")},
    {"name": "Daily goals", "searches": ("daily goals", "goal tracker")},
)

# Phrases people search that fit the sample app, and how often (bar width %),
# for the first stage of How Get found works.
SAMPLE_PHRASES = (("habit tracker", 88), ("daily habits", 64), ("streak app", 47), ("routine planner", 35),
                  ("goal tracker", 52))


# The Sales & growth sample, laid out as the real tab is (aso_pro
# store/results.html): the focus card, six KPI tiles, from seen to paid, how
# people find the app, what stands out, the daily chart with a release and a
# store change, countries, and what one change did. Words as the real tab
# says them (aso_pro store/insights.py: FOCUS_*, METRIC_*, SOURCE_LABELS).
SAMPLE_FOCUS = {"title": "Fewer people get your app after seeing it",
                "line": "3.4% of the people who saw it got it, against 3.9% in the 30 days before.",
                "where": "Screenshots, ratings and the top of your page decide who gets it.", "step": 2}
SAMPLE_FOCUS_STEPS = ("Saw it", "Opened your page", "Got it", "Paid")
# (label, hint, value, change, up, series of 30 days)
_WAVE = (0, 3, 1, 4, 2, 5, 3, 6, 4, 2, 5, 7, 4, 6, 8, 5, 7, 9, 6, 8, 10, 7, 9, 11, 8, 10, 12, 9, 11, 13)
SAMPLE_KPIS = (
    ("Proceeds", "What Apple pays you, after its commission", "USD 4,286", "+6%", True, _WAVE),
    ("Downloads", "First-time downloads", "6,214", "-3%", False, tuple(13 - v for v in _WAVE)),
    ("Page views", "Times your store page was opened", "18,930", "+11%", True, _WAVE),
    ("Impressions", "Times your app was seen in the App Store", "182,400", "+9%", True, _WAVE),
    ("Conversion", "Of the people who saw your app, the share who got it", "3.4%", "-0.5 points", False,
     tuple(13 - v for v in _WAVE)),
    ("Purchases", "In-app purchases and subscription renewals", "1,128", "+5%", True, _WAVE),
)
# (label, value, hint or share of the people who saw it, bar height %)
SAMPLE_FUNNEL = (("Saw your app", "48,200", "people who saw it", 100),
                 ("Opened your page", "9,150", "19.0% of them", 44),
                 ("Got it", "1,640", "3.4% of them", 18),
                 ("Paid in the app", "212", "0.4% of them", 7))
SAMPLE_SOURCES = (("App Store search", "61%", "1,003"), ("Browsing the App Store", "22%", "361"),
                  ("Links on websites", "11%", "181"), ("Links in other apps", "6%", "95"))
SAMPLE_FINDINGS = (
    ("good", "Since 2.3 went live on Oct 2, downloads per day are 29% higher than in the 14 days before."),
    ("info", "People in Germany opened your page 11,340 times, and it has no German version."),
    ("bad", "In Japan, 3.9% of the people who saw your app got it, down from 5.2%."),
)
# (flag code, country, downloads, bar %, proceeds, conversion, change, up)
SAMPLE_COUNTRIES = (("us", "United States", "2,412", 100, "USD 2,180", "4.1%", "+8%", True),
                    ("gb", "United Kingdom", "874", 36, "USD 712", "3.8%", "+3%", True),
                    ("de", "Germany", "803", 33, "USD 236", "2.9%", "-6%", False),
                    ("jp", "Japan", "690", 29, "USD 512", "3.9%", "-12%", False),
                    ("fr", "France", "512", 21, "USD 301", "3.1%", "+1%", True))
# One applied change, 14 days before and after, and its keywords' ranks.
SAMPLE_EFFECT = {
    "what": "Nov 1 · Name, Subtitle · English (US)",
    "numbers": (("Impressions", "41,200 to 47,900"), ("Page views", "4,010 to 4,620"),
                ("Downloads", "1,320 to 1,450"), ("Got it, of those who saw it", "3.2% to 3.0%")),
    "keywords": (("habit tracker", "#18", "#11"), ("daily habits", "unranked", "#34"), ("streak app", "#9", "#8"),
                 ("routine planner", "#22", "#25")),
}
# Downloads a day over 60 days, a release on day 15 and a store change on day 45.
_DAILY = (23, 23, 25, 22, 24, 23, 22, 24, 22, 24, 22, 22, 24, 25, 22, 29, 31, 32, 30, 30, 32, 28, 31, 29, 29,
          28, 29, 31, 29, 30, 25, 23, 24, 22, 22, 23, 25, 24, 23, 24, 24, 23, 25, 25, 23, 28, 28, 29, 28, 27,
          29, 26, 27, 29, 26, 27, 26, 28, 29, 28)
SAMPLE_CHART = {"release": 15, "change": 45, "dates": ("Sep 17", "Oct 2", "Nov 1", "Nov 15")}


def _spark(values, width=120, height=32) -> str:
    """The points of a small trend line, as the real KPI tiles draw it."""
    top, low = max(values), min(values)
    span = (top - low) or 1
    n = len(values) - 1
    return " ".join(f"{width * i / n:.1f},{height - 2 - (height - 4) * (v - low) / span:.1f}"
                    for i, v in enumerate(values))


def _chart() -> dict:
    width, top, bottom = 900.0, 20.0, 210.0
    low, high = 10, 40
    n = len(_DAILY) - 1

    def x(i):
        return width * i / n

    points = " ".join(f"{x(i):.1f},{bottom - (bottom - top) * (v - low) / (high - low):.1f}"
                      for i, v in enumerate(_DAILY))
    return {"points": points, "release_x": f"{x(SAMPLE_CHART['release']):.1f}",
            "change_x": f"{x(SAMPLE_CHART['change']):.1f}", "dates": SAMPLE_CHART["dates"]}


# (stars, reviewer, territory, title, body, drafted reply)
SAMPLE_REVIEWS = (
    (2, "mila_k", "Germany", "Erinnerungen kommen nicht",
     "Seit dem letzten Update bekomme ich keine Erinnerungen mehr.",
     ("Danke für den Hinweis! Bitte aktiviere die Mitteilungen für Habito in den Einstellungen erneut. "
      "Mit dem nächsten Update ist das behoben.")),
)


def trust_lines(*, confirm_on: bool) -> tuple[str, ...]:
    """What a person must know to trust a draft, under the Get found button
    (and its sample) that the person starts: true to the "When a draft you
    start is ready" setting."""
    first = "Nothing changes until you approve" if confirm_on else "Applies by itself when it is ready"
    return (first, "Promotional text goes live at once, the rest when you submit it",
            "Restore puts any field back")


def sample_fields(locked: bool = True) -> list[dict]:
    return [{"label": label, "locale": locale, "old": old, "new": new, "limit": limit,
             "locked": keyword and locked, "count": len(new)}
            for label, locale, old, new, limit, keyword in SAMPLE_FIELDS]


def _review(row) -> dict:
    stars, who, where, title, body, reply = row
    return {"stars": range(stars), "empty_stars": range(5 - stars), "rating": stars,
            "reviewer": who, "territory": where, "title": title, "body": body, "reply": reply}


def free_tabs(active: str) -> list[dict]:
    """The Store tabs where the section has none of its own (the free
    edition): four previews of this page (?tab=) and the Simulator's own."""
    from django.urls import reverse

    base = reverse("aso:pro_promo_store")
    tabs = [{"key": "autopilot", "label": TAB_WORDS["autopilot"]["title"], "url": base},
            {"key": "simulator", "label": "Simulator", "url": reverse("aso:pro_promo_simulator")}]
    tabs += [{"key": key, "label": TAB_WORDS[key]["title"], "url": f"{base}?tab={key}"}
             for key in ("pages", "results", "reviews")]
    return [dict(tab, active=tab["key"] == active) for tab in tabs]


def preview_context(state: str, *, license_url: str | None = None, tab: str | None = None,
                    cta: dict | None = None, free: bool = False) -> dict:
    """Template context for aso/store_preview.html: one tab of the Store
    section, each a picture of what it helps with (owner, 2026-11-16: every
    tab on one page was too much to read). ``license_url`` is the Pro build's
    Settings, License page; STATE_CONNECT takes the Pro view's ``cta``.
    ``free`` draws the tabs where the section has none of its own (the free
    edition, ``free_tabs``); the Pro build has its section tabs."""
    tab = tab or "autopilot"
    if tab not in TAB_WORDS:
        raise ValueError(f"unknown tab: {tab!r}")
    if state == STATE_CONNECT:
        if cta is None:
            raise ValueError("the connect state needs a cta")
    elif state not in STATES:
        raise ValueError(f"unknown preview state: {state!r}")
    locked = state != STATE_CONNECT
    explainer = EXPLAINERS[tab]
    return {
        "preview": {
            "state": state,
            "locked": locked,
            "tab": tab,
            "tabs": free_tabs(tab) if free else [],
            "words": TAB_WORDS[tab],
            "why": explainer["why"],
            "flow": [{"icon": icon, "title": title, "line": line} for icon, title, line in explainer["steps"]],
            "search": SAMPLE_SEARCH,
            "pages": SAMPLE_PAGES,
            "fields": sample_fields(locked),
            "kpis": [{"label": a, "hint": h, "value": b, "change": c, "up": d, "spark": _spark(series)}
                     for a, h, b, c, d, series in SAMPLE_KPIS],
            "focus": SAMPLE_FOCUS, "focus_steps": SAMPLE_FOCUS_STEPS,
            "funnel": [{"label": a, "value": b, "hint": c, "height": d, "focus": i == SAMPLE_FOCUS["step"]}
                       for i, (a, b, c, d) in enumerate(SAMPLE_FUNNEL)],
            "sources": [{"label": a, "share": b, "downloads": c} for a, b, c in SAMPLE_SOURCES],
            "findings": [{"tone": a, "text": b} for a, b in SAMPLE_FINDINGS],
            "countries": [{"flag": countries.flag(c), "name": n, "downloads": d, "bar": b, "proceeds": m,
                           "conversion": v, "change": ch, "up": up}
                          for c, n, d, b, m, v, ch, up in SAMPLE_COUNTRIES],
            "effect": SAMPLE_EFFECT, "chart": _chart(),
            "review": _review(SAMPLE_REVIEWS[0]),
            "cta": cta or dict(pro_preview_words.cta("store", state, license_url), headline=TAB_HEADLINES[tab]),
        },
    }
