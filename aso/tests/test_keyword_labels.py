"""Labels on keywords: the user's own grouping of Search History (issue #25).

A label belongs to the keyword, so it shows in every country the keyword is
tracked in. It is added and removed from the multi-select bar or with the x
on a chip, the Filters panel filters by it, the CSV export carries it, and a
label exists while a keyword carries it. aso/keyword_labels.py is the only
writer. The browser side was driven end to end with Playwright; see
docs/development/KEYWORD_LABELS_PLAN.md.

Free-tier test: no aso_pro import.
"""

import csv
import io
import json

from django.test import TestCase

from aso import history_revision, keyword_labels
from aso.models import App, Keyword, KeywordLabel, SearchResult


def _result(keyword, country="us", **fields):
    values = {
        "popularity_score": 40, "difficulty_score": 30,
        "difficulty_breakdown": {}, "competitors_data": [],
    }
    values.update(fields)
    return SearchResult.objects.create(keyword=keyword, country=country, **values)


class NameTest(TestCase):
    def test_spaces_are_trimmed_and_collapsed(self):
        self.assertEqual(keyword_labels.clean_name("  high   intent "), "high intent")

    def test_an_empty_name_says_what_to_do(self):
        with self.assertRaisesMessage(keyword_labels.LabelError, "Type a name for the label."):
            keyword_labels.clean_name("   ")

    def test_a_long_name_says_the_limit(self):
        keyword_labels.clean_name("x" * keyword_labels.MAX_LENGTH)
        with self.assertRaisesMessage(keyword_labels.LabelError, "at most 40 characters"):
            keyword_labels.clean_name("x" * (keyword_labels.MAX_LENGTH + 1))

    def test_one_label_whatever_the_case(self):
        a = Keyword.objects.create(keyword="airis cull")
        b = Keyword.objects.create(keyword="photo cleaner")
        keyword_labels.add([a.pk], "Competitor")
        name, added = keyword_labels.add([b.pk], "competitor")
        self.assertEqual((name, added), ("Competitor", 1))
        self.assertEqual(set(KeywordLabel.objects.values_list("name", flat=True)), {"Competitor"})


class WriteTest(TestCase):
    def setUp(self):
        self.a = Keyword.objects.create(keyword="airis cull")
        self.b = Keyword.objects.create(keyword="photo cleaner")

    def test_adding_twice_changes_nothing(self):
        self.assertEqual(keyword_labels.add([self.a.pk, self.b.pk], "seasonal"), ("seasonal", 2))
        self.assertEqual(keyword_labels.add([self.a.pk, self.b.pk], "seasonal"), ("seasonal", 0))
        self.assertEqual(KeywordLabel.objects.count(), 2)

    def test_ids_that_are_not_keywords_are_ignored(self):
        self.assertEqual(keyword_labels.add([self.a.pk, 99999, "x", None], "seasonal"), ("seasonal", 1))

    def test_removing_takes_it_off_only_those_keywords(self):
        keyword_labels.add([self.a.pk, self.b.pk], "seasonal")
        self.assertEqual(keyword_labels.remove([self.a.pk], "seasonal"), 1)
        self.assertEqual(list(KeywordLabel.objects.values_list("keyword_id", flat=True)), [self.b.pk])

    def test_the_labels_a_selection_carries_with_how_many(self):
        keyword_labels.add([self.a.pk, self.b.pk], "seasonal")
        keyword_labels.add([self.a.pk], "competitor")
        self.assertEqual(keyword_labels.labels_of([self.a.pk, self.b.pk]), [
            {"name": "competitor", "count": 1}, {"name": "seasonal", "count": 2},
        ])

    def test_a_deleted_keyword_takes_its_labels(self):
        keyword_labels.add([self.a.pk], "seasonal")
        self.a.delete()
        self.assertFalse(KeywordLabel.objects.exists())

    def test_a_label_moves_the_dashboards_change_count(self):
        """Another window shows the label without a reload (aso/history_revision.py)."""
        before = history_revision.count()
        keyword_labels.add([self.a.pk], "seasonal")
        added = history_revision.count()
        self.assertGreater(added, before)
        keyword_labels.remove([self.a.pk], "seasonal")
        self.assertGreater(history_revision.count(), added)


class ReadTest(TestCase):
    def test_names_in_use_are_those_on_rows_in_the_table(self):
        app = App.objects.create(name="Fluntro")
        other = App.objects.create(name="Calm Minutes")
        shown = Keyword.objects.create(keyword="airis cull", app=app)
        elsewhere = Keyword.objects.create(keyword="meditation", app=other)
        no_rows = Keyword.objects.create(keyword="gone", app=app)
        _result(shown)
        _result(shown, country="gb")
        _result(elsewhere)
        keyword_labels.add([shown.pk], "competitor")
        keyword_labels.add([elsewhere.pk], "Seasonal")
        keyword_labels.add([no_rows.pk], "orphan")
        self.assertEqual(keyword_labels.names_in_use(), ["competitor", "Seasonal"])
        self.assertEqual(keyword_labels.names_in_use(app_id=app.pk), ["competitor"])

    def test_names_by_keyword_in_reading_order(self):
        keyword = Keyword.objects.create(keyword="airis cull")
        keyword_labels.add([keyword.pk], "Seasonal")
        keyword_labels.add([keyword.pk], "competitor")
        self.assertEqual(keyword_labels.names_by_keyword([keyword.pk]), {keyword.pk: ["competitor", "Seasonal"]})


class EndpointTest(TestCase):
    def setUp(self):
        self.a = Keyword.objects.create(keyword="airis cull")
        self.b = Keyword.objects.create(keyword="photo cleaner")

    def post(self, name, body):
        from django.urls import reverse

        return self.client.post(reverse(name), data=json.dumps(body), content_type="application/json")

    def test_add_answers_with_the_name_used(self):
        keyword_labels.add([self.a.pk], "Competitor")
        response = self.post("aso:keyword_labels_add", {"keyword_ids": [self.a.pk, self.b.pk], "name": " competitor "})
        self.assertEqual(response.json(), {"success": True, "name": "Competitor", "added": 1})

    def test_a_name_the_user_cannot_use_says_why(self):
        response = self.post("aso:keyword_labels_add", {"keyword_ids": [self.a.pk], "name": ""})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "Type a name for the label.")

    def test_no_keywords_says_what_to_do(self):
        for name in ("aso:keyword_labels_add", "aso:keyword_labels_remove", "aso:keyword_labels_of"):
            with self.subTest(name=name):
                response = self.post(name, {"keyword_ids": [], "name": "x"})
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["error"], "Select at least one keyword first.")

    def test_remove_and_list(self):
        _result(self.a)
        keyword_labels.add([self.a.pk, self.b.pk], "seasonal")
        self.assertEqual(self.post("aso:keyword_labels_remove", {"keyword_ids": [self.b.pk], "name": "seasonal"}).json(),
                         {"success": True, "removed": 1})
        self.assertEqual(self.post("aso:keyword_labels_of", {"keyword_ids": [self.a.pk, self.b.pk]}).json(), {
            "success": True, "labels": [{"name": "seasonal", "count": 1}], "in_use": ["seasonal"],
        })

    def test_only_post(self):
        from django.urls import reverse

        self.assertEqual(self.client.get(reverse("aso:keyword_labels_add")).status_code, 405)


class DashboardTest(TestCase):
    """The table shows the labels, filters by them, and pages through them."""

    def setUp(self):
        self.app = App.objects.create(name="Fluntro")
        self.rows = {}
        for i, word in enumerate(["airis cull", "photolite", "albumly", "photo cleaner"]):
            keyword = Keyword.objects.create(keyword=word, app=self.app)
            self.rows[word] = keyword
            _result(keyword, popularity_score=20 + i)
        _result(self.rows["photo cleaner"], country="gb")
        keyword_labels.add([self.rows["airis cull"].pk, self.rows["photo cleaner"].pk], "competitor")

    def get(self, **params):
        from django.urls import reverse

        return self.client.get(reverse("aso:dashboard"), {"app": self.app.pk, **params})

    def shown(self, response):
        return sorted((r.keyword.keyword, r.country) for r in response.context["history_results"])

    def test_rows_carry_their_labels_in_every_country(self):
        response = self.get()
        labels = {(r.keyword.keyword, r.country): r.label_names for r in response.context["history_results"]}
        self.assertEqual(labels[("photo cleaner", "us")], ["competitor"])
        self.assertEqual(labels[("photo cleaner", "gb")], ["competitor"])
        self.assertEqual(labels[("albumly", "us")], [])
        self.assertContains(response, 'data-label="competitor"', count=3)

    def test_the_filter_shows_the_rows_that_carry_any_chosen_label(self):
        keyword_labels.add([self.rows["albumly"].pk], "seasonal")
        response = self.get(label=["competitor", "seasonal"])
        self.assertEqual(self.shown(response), [
            ("airis cull", "us"), ("albumly", "us"), ("photo cleaner", "gb"), ("photo cleaner", "us"),
        ])
        self.assertTrue(response.context["has_filters"])
        self.assertContains(response, "4 of 5 matching")

    def test_the_filter_is_always_there_and_says_how_to_start(self):
        """The owner, knowing the feature existed, could not find it: the
        filter appeared only once a tag existed and the Tags button only
        once a row was ticked. Until a label exists the filter says how."""
        how = "No labels yet. Tick keywords in the table, then choose Labels in the bar that appears."
        response = self.get()
        self.assertContains(response, 'id="label-dropdown-wrap"')
        self.assertNotContains(response, how)
        KeywordLabel.objects.all().delete()
        response = self.get()
        self.assertContains(response, 'id="label-dropdown-wrap"')
        self.assertContains(response, how)

    def test_a_chosen_label_nobody_carries_stays_offered_so_it_can_be_cleared(self):
        response = self.get(label="gone")
        self.assertEqual(self.shown(response), [])
        self.assertIn("gone", response.context["label_choices"])

    def test_the_next_page_keeps_the_filters(self):
        response = self.get(label="competitor", insight="Low Volume", q="photo", per_page=25)
        query = response.context["history_query"]
        self.assertIn("label=competitor", query)
        self.assertIn("insight=Low+Volume", query)
        self.assertIn("q=photo", query)
        self.assertIn(f"app={self.app.pk}", query)


class ExportTest(TestCase):
    def test_the_export_holds_the_rows_the_table_shows_with_their_labels(self):
        from django.urls import reverse

        app = App.objects.create(name="Fluntro")
        for word in ["airis cull", "photolite", "albumly"]:
            _result(Keyword.objects.create(keyword=word, app=app))
        labelled = Keyword.objects.get(keyword="airis cull")
        keyword_labels.add([labelled.pk], "seasonal")
        keyword_labels.add([labelled.pk], "Competitor")
        params = {"app": app.pk, "label": "Competitor"}

        table = self.client.get(reverse("aso:dashboard"), params)
        export = self.client.get(reverse("aso:export_history_csv"), params).content.decode()
        rows = list(csv.reader(io.StringIO(export)))
        header = rows[0]
        body = [r for r in rows[1:] if len(r) == len(header)]
        self.assertEqual(header[:3], ["Keyword", "App", "Labels"])
        self.assertEqual([r[0] for r in body], [r.keyword.keyword for r in table.context["history_results"]])
        self.assertEqual(body[0][2], "Competitor; seasonal")


class HistoryFiltersTest(TestCase):
    """One reading of the filters for the table and the export."""

    def parse(self, query):
        from django.http import QueryDict

        from aso.history_filters import HistoryFilters

        return HistoryFilters.from_query(QueryDict(query))

    def test_what_cannot_be_read_is_left_out(self):
        filters = self.parse("app=x&pop_min=abc&diff_max=&insight=Nonsense&insight=Low Volume&label=a&label=a&label=")
        self.assertIsNone(filters.app_id)
        self.assertIsNone(filters.pop_min)
        self.assertIsNone(filters.diff_max)
        self.assertEqual(filters.insights, ("Low Volume",))
        self.assertEqual(filters.labels, ("a",))

    def test_a_zero_is_a_filter(self):
        filters = self.parse("pop_min=0")
        self.assertTrue(filters.narrowing)
        self.assertIn("pop_min=0", filters.query_string())

    def test_app_and_country_scope_the_table_without_narrowing_it(self):
        self.assertFalse(self.parse("app=3&country=US").narrowing)
        self.assertEqual(self.parse("app=3&country=US").query_string(), "app=3&country=us")
