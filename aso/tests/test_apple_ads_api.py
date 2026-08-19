"""Tests for the Apple Ads Platform API v1 thin client (aso.apple_ads.api).

Live-API contract notes baked into these tests (verified 2026-08-17):
- /acls wraps its list as {"result": {"acls": [...]}}.
- The popularity query returns NO totalCount; the pager terminates on a
  short page.
- promotedObjectId requires operator IN with a list value.
- RateLimit-Limit/Remaining/Reset arrive on every response (5/second).
"""

import datetime as dt
from unittest import mock

import jwt as pyjwt
from django.test import SimpleTestCase

from aso.apple_ads import api


def _response(status=200, payload=None, headers=None):
    response = mock.MagicMock()
    response.status_code = status
    response.headers = headers or {}
    if payload is None:
        response.json.side_effect = ValueError("no body")
    else:
        response.json.return_value = payload
    return response


CREDS = {
    "client_id": "SEARCHADS.client",
    "team_id": "SEARCHADS.team",
    "key_id": "kid-1",
    "private_key_pem": None,  # filled in setUpClass
}


def _fresh_token_state():
    api.invalidate_token_cache()


class ClientSecretTest(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec

        key = ec.generate_private_key(ec.SECP256R1())
        cls.private_pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode()
        cls.public_pem = key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode()

    def test_claims_and_header(self):
        secret = api.build_client_secret(
            self.private_pem, "SEARCHADS.c", "SEARCHADS.t", "kid-9"
        )
        header = pyjwt.get_unverified_header(secret)
        self.assertEqual(header["alg"], "ES256")
        self.assertEqual(header["kid"], "kid-9")
        claims = pyjwt.decode(
            secret, self.public_pem, algorithms=["ES256"],
            audience=api.JWT_AUDIENCE,
        )
        self.assertEqual(claims["sub"], "SEARCHADS.c")
        self.assertEqual(claims["iss"], "SEARCHADS.t")
        self.assertEqual(claims["exp"] - claims["iat"], api.CLIENT_SECRET_TTL)


class TokenLifecycleTest(SimpleTestCase):
    def setUp(self):
        _fresh_token_state()
        self.addCleanup(_fresh_token_state)
        self.creds = dict(CREDS, private_key_pem=ClientSecretTest.private_pem)
        ClientSecretTest.setUpClass()
        self.creds["private_key_pem"] = ClientSecretTest.private_pem

    def test_token_fetch_success(self):
        with mock.patch.object(api.requests, "post", return_value=_response(
            200, {"access_token": "tok-1", "expires_in": 3600}
        )) as post:
            token, expires_at = api.fetch_access_token(self.creds)
        self.assertEqual(token, "tok-1")
        self.assertGreater(expires_at, 0)
        body = post.call_args.kwargs["data"]
        self.assertEqual(body["grant_type"], "client_credentials")
        self.assertEqual(body["scope"], api.OAUTH_SCOPE)
        self.assertEqual(body["client_id"], self.creds["client_id"])

    def test_token_rejection_is_auth_error(self):
        for status in (400, 401, 403):
            with mock.patch.object(
                api.requests, "post", return_value=_response(status, {})
            ):
                with self.assertRaises(api.AppleAdsAuthError):
                    api.fetch_access_token(self.creds)

    def test_bearer_caches_until_refresh_margin(self):
        responses = [
            _response(200, {"access_token": "tok-a", "expires_in": 3600}),
            _response(200, {"access_token": "tok-b", "expires_in": 3600}),
        ]
        with mock.patch.object(api.requests, "post", side_effect=responses) as post:
            self.assertEqual(api._bearer(self.creds), "tok-a")
            self.assertEqual(api._bearer(self.creds), "tok-a")  # cached
            self.assertEqual(post.call_count, 1)
            # Simulate approaching expiry: shrink the cached window.
            with api._token_lock:
                api._token_cache["expires_at"] = (
                    api.TOKEN_REFRESH_MARGIN / 2 + __import__("time").time()
                )
            self.assertEqual(api._bearer(self.creds), "tok-b")
            self.assertEqual(post.call_count, 2)

    def test_request_retries_once_on_401_then_auth_error(self):
        token_responses = [
            _response(200, {"access_token": "tok-old", "expires_in": 3600}),
            _response(200, {"access_token": "tok-new", "expires_in": 3600}),
        ]
        with mock.patch.object(api.requests, "post", side_effect=token_responses), \
                mock.patch.object(api.requests, "request",
                                  return_value=_response(401, {})) as request:
            with self.assertRaises(api.AppleAdsAuthError):
                api._request("GET", "/me", self.creds, sleeper=lambda s: None)
        # One call with the old token, one with the refreshed token.
        self.assertEqual(request.call_count, 2)
        tokens = [c.kwargs["headers"]["Authorization"]
                  for c in request.call_args_list]
        self.assertEqual(tokens, ["Bearer tok-old", "Bearer tok-new"])

    def test_401_then_success_after_refresh(self):
        token_responses = [
            _response(200, {"access_token": "tok-old", "expires_in": 3600}),
            _response(200, {"access_token": "tok-new", "expires_in": 3600}),
        ]
        api_responses = [
            _response(401, {}),
            _response(200, {"result": {}}),
        ]
        with mock.patch.object(api.requests, "post", side_effect=token_responses), \
                mock.patch.object(api.requests, "request",
                                  side_effect=api_responses):
            payload = api._request("GET", "/me", self.creds,
                                   sleeper=lambda s: None)
        self.assertEqual(payload, {"result": {}})


class RequestPlumbingTest(SimpleTestCase):
    def setUp(self):
        _fresh_token_state()
        self.addCleanup(_fresh_token_state)
        ClientSecretTest.setUpClass()
        self.creds = dict(CREDS, private_key_pem=ClientSecretTest.private_pem)
        self.token_patch = mock.patch.object(
            api, "_bearer", return_value="tok-x"
        )
        self.token_patch.start()
        self.addCleanup(self.token_patch.stop)

    def test_context_header_only_when_account_given(self):
        with mock.patch.object(api.requests, "request",
                               return_value=_response(200, {})) as request:
            api._request("GET", "/me", self.creds)
            self.assertNotIn("X-AP-Context",
                             request.call_args.kwargs["headers"])
            api._request("POST", "/x", self.creds, ad_account_id="42")
            self.assertEqual(
                request.call_args.kwargs["headers"]["X-AP-Context"],
                "adAccountId=42",
            )

    def test_status_mapping(self):
        cases = [
            (403, api.AppleAdsAccessError),
            (404, api.AppleAdsAccessError),
            (400, api.AppleAdsAPIError),
        ]
        for status, exc in cases:
            with mock.patch.object(
                api.requests, "request",
                return_value=_response(status, {"error": {"code": "X"}}),
            ):
                with self.assertRaises(exc):
                    api._request("GET", "/x", self.creds,
                                 sleeper=lambda s: None)

    def test_429_exhaustion_and_500_transient(self):
        with mock.patch.object(api.requests, "request",
                               return_value=_response(429, {})):
            with self.assertRaises(api.AppleAdsRateLimitedError):
                api._request("GET", "/x", self.creds, sleeper=lambda s: None)
        responses = [_response(500, {}), _response(200, {"ok": 1})]
        with mock.patch.object(api.requests, "request", side_effect=responses):
            payload = api._request("GET", "/x", self.creds,
                                   sleeper=lambda s: None)
        self.assertEqual(payload, {"ok": 1})

    def test_retry_after_seconds_and_http_date(self):
        sleeps = []
        responses = [
            _response(429, {}, headers={"Retry-After": "3"}),
            _response(200, {"ok": 1}),
        ]
        with mock.patch.object(api.requests, "request", side_effect=responses):
            api._request("GET", "/x", self.creds, sleeper=sleeps.append)
        self.assertGreaterEqual(sleeps[0], 3.0)
        self.assertLessEqual(sleeps[0], 3.0 * (1 + api.JITTER_FACTOR))
        # HTTP-date form parses without crashing and yields a non-negative wait.
        self.assertIsNone(api._parse_retry_after({"Retry-After": "garbage"}))
        self.assertEqual(
            api._parse_retry_after({"Retry-After": "Thu, 01 Jan 1970 00:00:00 GMT"}),
            0.0,
        )

    def test_backoff_capped_at_max(self):
        for attempt in range(1, 10):
            self.assertLessEqual(
                api._retry_delay(500, {}, attempt), api.MAX_RETRY_DELAY
            )
            self.assertLessEqual(
                api._retry_delay(429, {}, attempt), api.MAX_RETRY_DELAY
            )

    def test_rate_headers_captured(self):
        with mock.patch.object(api.requests, "request", return_value=_response(
            200, {}, headers={"RateLimit-Limit": "5",
                              "RateLimit-Remaining": "3",
                              "RateLimit-Reset": "1"},
        )):
            api._request("GET", "/x", self.creds)
        headers = api.get_last_rate_headers()
        self.assertEqual(headers, {"limit": 5, "remaining": 3, "reset": 1})


class DiscoveryParsingTest(SimpleTestCase):
    def setUp(self):
        _fresh_token_state()
        self.addCleanup(_fresh_token_state)
        ClientSecretTest.setUpClass()
        self.creds = dict(CREDS, private_key_pem=ClientSecretTest.private_pem)
        patcher = mock.patch.object(api, "_bearer", return_value="tok-x")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_acls_live_envelope(self):
        """The live API wraps the list: {"result": {"acls": [...]}}."""
        payload = {"success": True, "result": {"acls": [
            {"roles": ["API Campaign Manager"],
             "adAccount": {"id": 2525520, "name": "Org", "orgId": 2525520}},
        ]}, "info": {}}
        with mock.patch.object(api.requests, "request",
                               return_value=_response(200, payload)):
            acls = api.list_acls(self.creds)
        self.assertEqual(acls, [{
            "ad_account_id": 2525520, "ad_account_name": "Org",
            "org_id": 2525520, "roles": ["API Campaign Manager"],
        }])

    def test_acls_documented_flat_list_still_works(self):
        payload = {"result": [
            {"roles": ["Admin"], "adAccount": {"id": 1, "name": "A", "orgId": 2}},
        ]}
        with mock.patch.object(api.requests, "request",
                               return_value=_response(200, payload)):
            acls = api.list_acls(self.creds)
        self.assertEqual(acls[0]["ad_account_id"], 1)

    def test_me_parsing(self):
        payload = {"result": {"userId": 7, "orgId": 9}}
        with mock.patch.object(api.requests, "request",
                               return_value=_response(200, payload)):
            me = api.get_me(self.creds)
        self.assertEqual(me, {"user_id": 7, "org_id": 9})


class PopularityQueryTest(SimpleTestCase):
    def setUp(self):
        _fresh_token_state()
        self.addCleanup(_fresh_token_state)
        ClientSecretTest.setUpClass()
        self.creds = dict(CREDS, private_key_pem=ClientSecretTest.private_pem)
        patcher = mock.patch.object(api, "_bearer", return_value="tok-x")
        patcher.start()
        self.addCleanup(patcher.stop)

    WEEK = dt.date(2026, 8, 9)  # a Sunday

    def _row(self, term, rank=1):
        return {"week": "2026-08-09", "countryOrRegion": "US",
                "genre": "BUSINESS", "searchTerm": term, "rankInGenre": rank,
                "searchPopularityInGenre": 90, "searchPopularity1to100": 70,
                "searchPopularity1to5": 4}

    def test_request_body_shape(self):
        with mock.patch.object(api.requests, "request", return_value=_response(
            200, {"result": {"rows": []}, "pagination": {}}
        )) as request:
            api.query_search_term_popularity(
                self.creds, "42", country="us", week_start=self.WEEK,
            )
        body = request.call_args.kwargs["json"]
        self.assertEqual(body["fields"], api.POPULARITY_FIELDS)
        self.assertEqual(body["filters"], [{
            "field": "countryOrRegion", "operator": "EQUALS", "value": "US",
        }])
        self.assertEqual(body["timeRange"], {
            "start": "2026-08-09", "end": "2026-08-15",
            "granularity": "WEEKLY_SUN_SAT",
        })
        # Live contract: no sorting, no fetchTotalCount.
        self.assertNotIn("sorting", body)
        self.assertNotIn("fetchTotalCount", body["pagination"])

    def test_pager_terminates_on_short_page_without_totalcount(self):
        pages = [
            _response(200, {"result": {"rows": [self._row("a"), self._row("b")]},
                            "pagination": {}}),
            _response(200, {"result": {"rows": [self._row("c")]},
                            "pagination": {}}),
        ]
        between = []
        with mock.patch.object(api.requests, "request", side_effect=pages):
            collected = [
                rows for rows, _total, _idx in api.iter_search_term_popularity(
                    self.creds, "42", country="us", week_start=self.WEEK,
                    page_size=2, between_pages=lambda: between.append(1),
                )
            ]
        self.assertEqual([len(p) for p in collected], [2, 1])
        self.assertEqual(len(between), 1)  # called once, before page 2

    def test_pager_single_short_page(self):
        with mock.patch.object(api.requests, "request", return_value=_response(
            200, {"result": {"rows": [self._row("a")]}, "pagination": {}}
        )):
            pages = list(api.iter_search_term_popularity(
                self.creds, "42", country="us", week_start=self.WEEK,
                page_size=5,
            ))
        self.assertEqual(len(pages), 1)

    def test_malformed_rows_tolerated(self):
        with mock.patch.object(api.requests, "request", return_value=_response(
            200, {"result": {"rows": "nonsense"}}
        )):
            rows, total = api.query_search_term_popularity(
                self.creds, "42", country="us", week_start=self.WEEK,
            )
        self.assertEqual(rows, [])
        self.assertEqual(total, -1)


class ImpressionShareQueryTest(SimpleTestCase):
    def setUp(self):
        _fresh_token_state()
        self.addCleanup(_fresh_token_state)
        ClientSecretTest.setUpClass()
        self.creds = dict(CREDS, private_key_pem=ClientSecretTest.private_pem)
        patcher = mock.patch.object(api, "_bearer", return_value="tok-x")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_promoted_object_id_uses_in_operator(self):
        """Live contract: EQUALS is rejected for promotedObjectId."""
        with mock.patch.object(api.requests, "request", return_value=_response(
            200, {"result": {"rows": []}, "pagination": {"totalCount": 0}}
        )) as request:
            api.query_impression_share(
                self.creds, "42", promoted_object_id="123",
                week_start=dt.date(2026, 8, 9),
            )
        filters = request.call_args.kwargs["json"]["filters"]
        self.assertEqual(filters[0], {
            "field": "promotedObjectId", "operator": "IN", "value": ["123"],
        })

    def test_non_sunday_start_rejected(self):
        with self.assertRaises(ValueError):
            api.query_impression_share(
                self.creds, "42", promoted_object_id="123",
                week_start=dt.date(2026, 8, 10),  # a Monday
            )

    def test_weeks_clamped_to_four(self):
        with mock.patch.object(api.requests, "request", return_value=_response(
            200, {"result": {"rows": []}, "pagination": {}}
        )) as request:
            api.query_impression_share(
                self.creds, "42", promoted_object_id="123",
                week_start=dt.date(2026, 8, 9), weeks=9,
            )
        time_range = request.call_args.kwargs["json"]["timeRange"]
        self.assertEqual(time_range["end"], "2026-09-05")  # 4 weeks


class WeekMathTest(SimpleTestCase):
    UTC = dt.timezone.utc

    def test_week_start_sunday(self):
        self.assertEqual(api.week_start_sunday(dt.date(2026, 8, 12)),
                         dt.date(2026, 8, 9))
        self.assertEqual(api.week_start_sunday(dt.date(2026, 8, 9)),
                         dt.date(2026, 8, 9))
        self.assertEqual(api.week_start_sunday(dt.date(2026, 8, 15)),
                         dt.date(2026, 8, 9))

    def test_latest_available_week_publication_boundary(self):
        cases = [
            # Monday 06:59 UTC: last week's data not yet published.
            (dt.datetime(2026, 8, 10, 6, 59, tzinfo=self.UTC), dt.date(2026, 7, 26)),
            # Monday 07:00 UTC: published.
            (dt.datetime(2026, 8, 10, 7, 0, tzinfo=self.UTC), dt.date(2026, 8, 2)),
            # Mid-week.
            (dt.datetime(2026, 8, 13, 12, 0, tzinfo=self.UTC), dt.date(2026, 8, 2)),
            # Sunday: still last-published week.
            (dt.datetime(2026, 8, 16, 23, 0, tzinfo=self.UTC), dt.date(2026, 8, 2)),
            # Year boundary.
            (dt.datetime(2026, 1, 1, 12, 0, tzinfo=self.UTC), dt.date(2025, 12, 21)),
        ]
        for now, expected in cases:
            got = api.latest_available_week(now)
            self.assertEqual(got, expected, now)
            self.assertEqual(got.weekday(), 6)

    def test_naive_datetime_treated_as_utc(self):
        got = api.latest_available_week(dt.datetime(2026, 8, 13, 12, 0))
        self.assertEqual(got, dt.date(2026, 8, 2))

    def test_weeks_back(self):
        self.assertEqual(api.weeks_back(dt.date(2026, 8, 9), 3),
                         dt.date(2026, 7, 19))
