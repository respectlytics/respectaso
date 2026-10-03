"""Shared test helpers for the aso suite (re-exported by aso_pro.tests.helpers)."""


class _SyncThread:
    """Runs the target inline on start() so background work is deterministic.

    Stands in for ``threading.Thread`` wherever a test needs the worker body to
    actually execute (the run queue's dispatcher, the Local AI test job). The
    signature mirrors the keyword arguments those call sites pass.
    """

    def __init__(self, target=None, args=(), kwargs=None, daemon=None, name=None):
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}
        self.daemon = daemon
        self.name = name

    def start(self):
        if self._target:
            self._target(*self._args, **self._kwargs)


def ranked_search(apps, extra_ids=(), source="itunes"):
    """A RankedSearch for tests: ``apps`` in Apple's order (the first 25 are
    the competitors), then ``extra_ids`` ranked after them. An app without a
    trackId gets 900000 + its position."""
    from aso.services import RankedSearch

    apps = list(apps)
    ids = [int(app.get("trackId") or 900000 + index) for index, app in enumerate(apps)]
    ids += [int(track_id) for track_id in extra_ids]
    return RankedSearch(ranked_ids=ids, apps=apps[:25], result_count=len(ids), source=source)


class AppleLookup:
    """Apple's Lookup endpoint at the HTTP layer, answering a script of
    outcomes, one per call; the last one repeats. Patch it in for
    ``aso.services.requests.get``. Any other URL fails the test.

    Outcomes: "timeout", "connection" (requests' own exceptions), 403, 429
    or 503 (Apple busy, with a Retry-After), 500, "garbage" (not JSON),
    "empty" (Apple answered: no such app), or an app dict (Apple's result).
    ``calls`` keeps the params of every call.
    """

    def __init__(self, *outcomes, retry_after="2"):
        self.outcomes = list(outcomes) or ["empty"]
        self.retry_after = retry_after
        self.calls = []

    def __call__(self, url, params=None, timeout=None, **kwargs):
        import json

        import requests

        from aso.services import ITunesSearchService

        if url != ITunesSearchService.LOOKUP_URL:
            raise AssertionError(f"Only Apple's Lookup is faked here, not {url}")
        self.calls.append(dict(params or {}))
        outcome = self.outcomes[min(len(self.calls), len(self.outcomes)) - 1]
        if outcome == "timeout":
            raise requests.exceptions.ReadTimeout("read timed out")
        if outcome == "connection":
            raise requests.exceptions.ConnectionError("connection reset by peer")
        response = requests.Response()
        response.url = url
        response.status_code = 200
        if isinstance(outcome, int):
            response.status_code = outcome
            response.headers["Retry-After"] = self.retry_after
            response._content = b""
        elif outcome == "garbage":
            response._content = b"<html>Service Unavailable</html>"
        elif outcome == "empty":
            response._content = b'{"resultCount": 0, "results": []}'
        else:
            response._content = json.dumps({"resultCount": 1, "results": [outcome]}).encode()
        return response


class AppleSearch:
    """Apple's Search endpoint at the HTTP layer, answering a script of
    outcomes, one per call; the last one repeats. Patch it in for
    ``aso.services.requests.get``, alone or beside an AppleLookup through
    AppleApi. Any other URL fails the test.

    Outcomes: 403, 429 or 503 (Apple busy, with a Retry-After), or a list of
    app dicts (Apple's results, in its order). ``calls`` keeps the params of
    every call.
    """

    def __init__(self, *outcomes, retry_after="2"):
        self.outcomes = list(outcomes) or [[]]
        self.retry_after = retry_after
        self.calls = []

    def __call__(self, url, params=None, timeout=None, **kwargs):
        import json

        import requests

        from aso.services import ITunesSearchService

        if url != ITunesSearchService.SEARCH_URL:
            raise AssertionError(f"Only Apple's Search is faked here, not {url}")
        self.calls.append(dict(params or {}))
        outcome = self.outcomes[min(len(self.calls), len(self.outcomes)) - 1]
        response = requests.Response()
        response.url = url
        response.status_code = 200
        if isinstance(outcome, int):
            response.status_code = outcome
            response.headers["Retry-After"] = self.retry_after
            response._content = b""
        else:
            response._content = json.dumps({"resultCount": len(outcome), "results": outcome}).encode()
        return response


class AppleApi:
    """Apple's Lookup and Search at the HTTP layer at once: each call goes to
    the fake for its URL (AppleLookup, AppleSearch)."""

    def __init__(self, lookup=None, search=None):
        self.lookup = lookup or AppleLookup()
        self.search = search or AppleSearch()

    def __call__(self, url, params=None, timeout=None, **kwargs):
        from aso.services import ITunesSearchService

        fake = self.lookup if url == ITunesSearchService.LOOKUP_URL else self.search
        return fake(url, params=params, timeout=timeout, **kwargs)
