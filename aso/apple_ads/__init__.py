"""Apple Ads popularity integration (official Apple Ads Platform API v1).

Syncs Apple's weekly top-search-terms dataset per storefront so the user
can choose Apple's official search popularity as the popularity source
instead of RespectASO's internal estimate, and pulls per-app impression
share where the user's ads serve.

Package layout:
  storage.py     - settings.json keys owned by this feature (credential
                   ids, connection state, sync state); coexists with
                   aso_pro's settings storage. The private key lives in
                   its own file, see keys.py.
  keys.py        - local EC P-256 key pair (generate/import/derive).
  api.py         - thin client for the v1 API: OAuth token lifecycle,
                   insights queries, retry/backoff, RateLimit capture.
  sync.py        - weekly dataset sync, 65-week backfill, retention
                   pruning, and the bounded first-country download.
  impressions.py - weekly impression-share sync per tracked app.

Scoring code never imports this package directly - it reads Apple values
through aso.popularity (the single resolution choke point), which only
does database lookups. Network I/O happens exclusively in background sync
threads, plus the one bounded synchronous first-country download.
"""
