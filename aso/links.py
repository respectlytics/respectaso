"""The pages of our own sites the app links to, in one place.

The pricing address used to be typed out in two modules and three
templates; everything that links to respectaso.com or another of our sites
reads it from here.
Free-tier module: no aso_pro, licensing or llm_providers import.
"""

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# A browser opened by an app sends no Referer, so every link to one of our sites
# names the app as where the visit came from, or the site would count it as direct.
# The tag is the app's one entry in the register of ref tags (the loheden-legal
# skill, The ref tags): the same at respectaso.com and at every other site of ours.
REF = "respectaso-app"
# Every site of Loheden AI Solutions.
HOSTS = ("respectaso.com", "respectlytics.com", "thereseai.com", "loheden.com", "prokoll.com",
         "sharepredictions.com", "wiseprediction.com")


def is_ours(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower().removeprefix("www.")
    return any(host == known or host.endswith("." + known) for known in HOSTS)


def tagged(url: str) -> str:
    """A link to one of our sites, naming the app as where the visit came from."""
    parts = urlsplit(url)
    query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
             if key != "ref" and not key.startswith("utm_")]
    return urlunsplit(parts._replace(query=urlencode([*query, ("ref", REF)])))


PRICING_URL = tagged("https://respectaso.com/pricing/")
PRO_PAGE_URL = tagged("https://respectaso.com/pro/")
RENEW_URL = tagged("https://respectaso.com/license/renew/")
POPULARITY_WRITEUP_URL = tagged("https://respectaso.com/blog/apple-search-ads-popularity-unreliable-aso-keyword-data/")
RESPECTLYTICS_URL = tagged("https://respectlytics.com/")
YOUTUBE_URL = "https://www.youtube.com/@TheCreatorBehind"
X_URL = "https://x.com/sinecanswork"
GITHUB_URL = "https://github.com/respectlytics/respectaso"
CONTACT_EMAIL = "respectaso@loheden.com"
