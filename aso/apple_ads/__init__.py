"""Apple Ads popularity integration.

Fetches Apple's own keyword search popularity (granular 5-100 values, the
same figures the ads.apple.com keyword planner shows) so the user can choose
it as the popularity source instead of RespectASO's internal estimate.

Package layout:
  storage.py - settings.json keys owned by this feature (cookies, app id,
               sync state); coexists with aso_pro's settings storage.
  client.py  - HTTP client for the keyword-planner popularity endpoint,
               including the full rate-limit and backoff policy.
  auth.py    - embedded Apple sign-in (pywebview window) and cookie session.
  sync.py    - background sync populating the AppleSearchPopularity table.

Scoring code never imports this package directly - it reads Apple values
through aso.popularity (the single resolution choke point), which only does
database lookups. Network I/O happens exclusively in background sync threads.
"""
