"""The respectaso.com pages the app links to, in one place.

The pricing address used to be typed out in two modules and three
templates; everything that links to the website reads it from here.
Free-tier module: no aso_pro, licensing or llm_providers import.
"""

# ?ref=app tells the website's download and sales statistics that the visit came from
# the app: a browser opened by an app sends no Referer, so it would count as direct.
PRICING_URL = "https://respectaso.com/pricing/?ref=app"
PRO_PAGE_URL = "https://respectaso.com/pro/?ref=app"
RENEW_URL = "https://respectaso.com/license/renew/?ref=app"
YOUTUBE_URL = "https://www.youtube.com/@TheCreatorBehind"
X_URL = "https://x.com/sinecanswork"
GITHUB_URL = "https://github.com/respectlytics/respectaso"
RESPECTLYTICS_URL = "https://respectlytics.com/?utm_source=respectaso&utm_medium=help_menu&utm_campaign=oss"
CONTACT_EMAIL = "respectaso@loheden.com"
