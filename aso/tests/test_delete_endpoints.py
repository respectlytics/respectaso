"""Tests for the Search History delete endpoints.

A dashboard row represents a (keyword, country) tracking entry, so deleting a
row must drop EVERY SearchResult snapshot for that pair (not just the latest)
and clean up keywords left with no results in any country. Covers the single
row endpoint (result_delete) and the multi-select bulk endpoint
(results_bulk_delete), which identifies rows by their stable
(keyword_id, country) pair because snapshot ids churn on refresh.
"""

import json

from django.test import TestCase
from django.urls import reverse

from aso.models import Keyword, SearchResult


def create_tracking_entry(keyword_text, country="us", snapshots=1):
    """Create a keyword tracked in a country with `snapshots` result rows.

    Returns (keyword, latest_result).
    """
    keyword, _ = Keyword.objects.get_or_create(keyword=keyword_text)
    result = None
    for _ in range(snapshots):
        result = SearchResult.objects.create(
            keyword=keyword,
            country=country,
            popularity_score=50,
            difficulty_score=40,
        )
    return keyword, result


class ResultDeleteTest(TestCase):
    def delete_result(self, result_id):
        return self.client.post(reverse("aso:result_delete", args=[result_id]))

    def test_deletes_all_snapshots_of_the_pair(self):
        keyword, latest = create_tracking_entry("weather app", snapshots=3)
        resp = self.delete_result(latest.id)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["success"])
        self.assertFalse(SearchResult.objects.filter(keyword=keyword, country="us").exists())

    def test_keeps_other_countries_and_the_keyword(self):
        keyword, us_result = create_tracking_entry("weather app", country="us")
        create_tracking_entry("weather app", country="de")
        self.delete_result(us_result.id)
        keyword.refresh_from_db()
        self.assertFalse(SearchResult.objects.filter(keyword=keyword, country="us").exists())
        self.assertTrue(SearchResult.objects.filter(keyword=keyword, country="de").exists())

    def test_deletes_orphaned_keyword(self):
        keyword, result = create_tracking_entry("weather app")
        self.delete_result(result.id)
        self.assertFalse(Keyword.objects.filter(id=keyword.id).exists())

    def test_unknown_id_returns_404(self):
        resp = self.delete_result(99999)
        self.assertEqual(resp.status_code, 404)

    def test_get_not_allowed(self):
        _, result = create_tracking_entry("weather app")
        resp = self.client.get(reverse("aso:result_delete", args=[result.id]))
        self.assertEqual(resp.status_code, 405)


class ResultsBulkDeleteTest(TestCase):
    def bulk_delete(self, payload):
        return self.client.post(
            reverse("aso:results_bulk_delete"),
            data=json.dumps(payload),
            content_type="application/json",
        )

    def entries(self, *pairs):
        return {
            "entries": [
                {"keyword_id": keyword.id, "country": country} for keyword, country in pairs
            ]
        }

    def test_deletes_multiple_tracking_entries(self):
        kw1, _ = create_tracking_entry("weather app", snapshots=2)
        kw2, _ = create_tracking_entry("photo editor")
        kw3, _ = create_tracking_entry("meditation timer")

        resp = self.bulk_delete(self.entries((kw1, "us"), (kw2, "us")))

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"success": True, "deleted": 2})
        self.assertFalse(Keyword.objects.filter(id__in=[kw1.id, kw2.id]).exists())
        self.assertTrue(SearchResult.objects.filter(keyword=kw3).exists())

    def test_duplicate_entries_count_once(self):
        kw, _ = create_tracking_entry("weather app", snapshots=2)
        resp = self.bulk_delete(self.entries((kw, "us"), (kw, "us")))
        self.assertEqual(resp.json()["deleted"], 1)
        self.assertFalse(Keyword.objects.filter(id=kw.id).exists())

    def test_keeps_keyword_with_surviving_countries(self):
        kw, _ = create_tracking_entry("weather app", country="us")
        create_tracking_entry("weather app", country="de")
        resp = self.bulk_delete(self.entries((kw, "us")))
        self.assertEqual(resp.json()["deleted"], 1)
        self.assertTrue(Keyword.objects.filter(id=kw.id).exists())
        self.assertTrue(SearchResult.objects.filter(keyword=kw, country="de").exists())

    def test_skips_missing_pairs_idempotently(self):
        kw, _ = create_tracking_entry("weather app")
        payload = self.entries((kw, "us"))
        payload["entries"].append({"keyword_id": 99999, "country": "us"})
        payload["entries"].append({"keyword_id": kw.id, "country": "fr"})

        resp = self.bulk_delete(payload)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["deleted"], 1)
        # Re-posting the same payload deletes nothing further.
        resp = self.bulk_delete(payload)
        self.assertEqual(resp.json()["deleted"], 0)

    def test_rejects_invalid_payloads(self):
        kw, _ = create_tracking_entry("weather app")
        invalid_payloads = [
            {},
            {"entries": []},
            {"entries": "not-a-list"},
            {"entries": [42]},
            {"entries": [{"keyword_id": "abc", "country": "us"}]},
            {"entries": [{"keyword_id": kw.id, "country": ""}]},
            {"entries": [{"keyword_id": kw.id}]},
            {"entries": [{"country": "us"}]},
        ]
        for payload in invalid_payloads:
            resp = self.bulk_delete(payload)
            self.assertEqual(resp.status_code, 400, f"payload {payload!r} should be rejected")
            self.assertFalse(resp.json()["success"])
        # Nothing was deleted by any of the rejected payloads.
        self.assertTrue(SearchResult.objects.filter(keyword=kw).exists())

    def test_rejects_malformed_json(self):
        resp = self.client.post(
            reverse("aso:results_bulk_delete"),
            data="not json",
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_get_not_allowed(self):
        resp = self.client.get(reverse("aso:results_bulk_delete"))
        self.assertEqual(resp.status_code, 405)
