"""The To do engine (aso/todo.py) and an app's keywords in brief
(aso/rank_change.py), docs/development/DAILY_USE_PLAN.md step 2.

Lines exist only with a Pro license (D12), and no source is registered by
the free edition, so every test that needs lines brings a source of its own
and runs under DEBUG_SKIP_LICENSE.
"""

import re
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from aso import rank_change, todo, ui_state
from aso.models import App, Keyword, SearchResult


class FakeSource:
    """A source with lines of its own; counts every call of lines()."""

    def __init__(self, lines=(), fails=False):
        self.result = list(lines)
        self.fails = fails
        self.version = 1
        self.calls = 0
        self.periods = []

    def stamp(self):
        return self.version

    def lines(self, apps, days):
        self.calls += 1
        self.periods.append(days)
        if self.fails:
            raise RuntimeError("this source broke")
        return list(self.result)


def line(key, tone="warning", counts=True, app_id=1):
    return todo.Line(app_id=app_id, key=key, tone=tone, text="Something to look at.", label="Open", url="/",
                     counts=counts)


class TodoTestBase(TestCase):
    def setUp(self):
        todo.reset()
        rank_change.reset()
        self.addCleanup(todo.reset)
        self.addCleanup(rank_change.reset)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        data = override_settings(DATA_DIR=Path(tmp.name))
        data.enable()
        self.addCleanup(data.disable)

    def use(self, *sources):
        """Run with exactly these sources and no refiner."""
        for name, value in (("sources", list(sources)), ("refiners", [])):
            patcher = patch.object(todo, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)


@override_settings(DEBUG_SKIP_LICENSE=True)
class LinesTest(TodoTestBase):
    def test_a_closed_line_is_not_open_and_not_counted(self):
        self.use(FakeSource([line("store-run:12"), line("asc-key:missing")]))
        self.assertEqual(todo.count(), 2)
        ui_state.dismiss_notice("store-run:12")
        self.assertEqual([one.key for one in todo.open_lines()], ["asc-key:missing"])
        self.assertEqual(todo.count(), 1)
        self.assertEqual(len(todo.all_lines()), 2, "a closed line is still one of all_lines()")

    def test_news_is_shown_but_not_counted(self):
        self.use(FakeSource([line("page-live:abc", tone="success", counts=False), line("asc-key:missing")]))
        self.assertEqual(len(todo.open_lines()), 2)
        self.assertEqual(todo.count(), 1)

    def test_a_failing_source_leaves_the_others(self):
        self.use(FakeSource(fails=True), FakeSource([line("asc-key:missing")]))
        with self.assertLogs("aso.todo", level="ERROR"):
            keys = [one.key for one in todo.all_lines()]
        self.assertEqual(keys, ["asc-key:missing"])

    def test_a_source_whose_stamp_fails_leaves_the_others(self):
        broken = FakeSource([line("store-run:1")])
        broken.stamp = lambda: 1 / 0
        self.use(broken, FakeSource([line("asc-key:missing")]))
        with self.assertLogs("aso.todo", level="ERROR"):
            self.assertEqual(len(todo.all_lines()), 2)

    def test_lines_follow_the_tone_order_and_group_by_app(self):
        self.use(FakeSource([line("news:1", tone="success", app_id=2), line("stop:1", tone="error", app_id=1),
                             line("look:1", tone="info", app_id=2)]))
        self.assertEqual([one.tone for one in todo.all_lines()], ["error", "info", "success"])
        grouped = todo.by_app()
        self.assertEqual([one.key for one in grouped[2]], ["look:1", "news:1"])
        self.assertEqual([one.key for one in grouped[1]], ["stop:1"])

    def test_within_a_tone_a_line_that_asks_comes_before_news(self):
        self.use(FakeSource([line("rival-news:1", tone="info", counts=False), line("store-proposal:1", tone="info")]))
        self.assertEqual([one.key for one in todo.all_lines()], ["store-proposal:1", "rival-news:1"])

    def test_the_topic_is_the_key_without_its_state(self):
        self.assertEqual(line("store-page:abc:draft").topic, "store-page:abc")

    def test_lines_are_worked_out_once_until_reset(self):
        source = FakeSource([line("asc-key:missing")])
        self.use(source)
        todo.all_lines()
        todo.all_lines()
        self.assertEqual(source.calls, 1)
        todo.reset()
        todo.all_lines()
        self.assertEqual(source.calls, 2)

    def test_each_period_is_worked_out_on_its_own_and_the_default_is_the_pages(self):
        from aso.apps_home import DEFAULT_PERIOD

        class ByPeriod(FakeSource):
            def lines(self, apps, days):
                super().lines(apps, days)
                return [line(f"store-drop:1:{days}d:2026-11")]

        source = ByPeriod()
        self.use(source)
        self.assertEqual([one.key for one in todo.all_lines(30)], ["store-drop:1:30d:2026-11"])
        self.assertEqual([one.key for one in todo.all_lines(7)], ["store-drop:1:7d:2026-11"])
        self.assertEqual(source.periods, [30, 7])
        todo.all_lines(30)
        todo.all_lines(7)
        self.assertEqual(source.calls, 2, "each period is kept")
        # No period, or one the page does not offer: the page's default.
        self.assertEqual(todo.all_lines(), todo.all_lines(DEFAULT_PERIOD))
        self.assertEqual(todo.all_lines(14), todo.all_lines(DEFAULT_PERIOD))
        self.assertEqual(source.calls, 2)
        self.assertNotEqual(todo.state(7)["stamp"], todo.state(30)["stamp"])

    def test_without_the_wait_lines_are_worked_out_again_only_on_a_change(self):
        source = FakeSource([line("asc-key:missing")])
        self.use(source)
        with patch.object(todo, "MIN_SECONDS", 0):
            todo.all_lines()
            todo.all_lines()
            self.assertEqual(source.calls, 1, "nothing changed")
            source.version = 2
            todo.all_lines()
            self.assertEqual(source.calls, 2, "the source's stamp changed")
            App.objects.create(name="Moonpond")
            todo.all_lines()
            self.assertEqual(source.calls, 3, "the history revision changed")
            todo.all_lines()
            self.assertEqual(source.calls, 3)

    def test_an_app_added_within_the_wait_gets_its_lines_at_once(self):
        source = FakeSource([line("asc-key:missing")])
        self.use(source)
        todo.all_lines()
        App.objects.create(name="Moonpond")
        todo.all_lines()
        self.assertEqual(source.calls, 2, "a new app is worked out at once")
        todo.all_lines()
        self.assertEqual(source.calls, 2)

    def test_the_state_stamp_follows_what_is_open(self):
        self.use(FakeSource([line("store-run:12"), line("asc-key:missing")]))
        before = todo.state()
        ui_state.dismiss_notice("store-run:12")
        after = todo.state()
        self.assertNotEqual(before["stamp"], after["stamp"])
        self.assertEqual((before["count"], after["count"]), (2, 1))

    def test_the_state_stamp_follows_what_a_line_says(self):
        """A reply posted elsewhere turns "4 reviews wait" into "3 reviews wait" under the same key; the
        page that drew the old words while the lines were kept (MIN_SECONDS) must be drawn again (13.3)."""
        source = FakeSource([line("store-reviews:1:9")])
        self.use(source)
        before = todo.state()
        source.result = [todo.Line(app_id=1, key="store-reviews:1:9", tone="warning", text="Something else now.",
                                   label="Open", url="/")]
        source.version += 1
        todo.reset()
        self.assertNotEqual(before["stamp"], todo.state()["stamp"])

    def test_every_key_is_a_notice_name(self):
        """Lines of every registered source (the Pro build's) and of a test source:
        each key is a name the X can keep (ui_state.NOTICE_KEY)."""
        App.objects.create(name="Moonpond")
        registered = list(todo.sources)
        self.use(*registered, FakeSource([line("asc-key:missing"), line("store-page:abc:draft")]))
        keys = [one.key for one in todo.all_lines()]
        self.assertTrue(keys)
        for key in keys:
            self.assertRegex(key, ui_state.NOTICE_KEY)
        ui_state.dismiss_notice(*keys)


@override_settings(DEBUG_SKIP_LICENSE=False)
class WithoutProTest(TodoTestBase):
    def test_no_line_without_a_pro_license(self):
        source = FakeSource([line("asc-key:missing")])
        self.use(source)
        self.assertEqual(todo.all_lines(), [])
        self.assertEqual(todo.open_lines(), [])
        self.assertEqual(todo.by_app(), {})
        self.assertEqual(todo.count(), 0)
        self.assertEqual(source.calls, 0)
        with self.settings(DEBUG_SKIP_LICENSE=True):
            self.assertEqual(todo.count(), 1)

    def test_the_top_bar_number_is_hidden_without_pro(self):
        self.use(FakeSource([line("asc-key:missing")]))
        html = self.client.get(reverse("aso:dashboard")).content.decode()
        badge = re.search(r'<span data-todo-count class="([^"]*)"[^>]*>(\d+)</span>', html)
        self.assertIsNotNone(badge)
        self.assertIn("hidden", badge.group(1).split())
        self.assertEqual(badge.group(2), "0")


@override_settings(DEBUG_SKIP_LICENSE=True)
class TopBarTest(TodoTestBase):
    def _badge(self):
        html = self.client.get(reverse("aso:dashboard")).content.decode()
        return re.search(r'<span data-todo-count class="([^"]*)"[^>]*>(\d+)</span>', html)

    def test_the_top_bar_shows_the_lines_that_ask_for_something(self):
        self.use(FakeSource([line("asc-key:missing"), line("store-run:3"),
                             line("page-live:abc", tone="success", counts=False)]))
        badge = self._badge()
        self.assertNotIn("hidden", badge.group(1).split())
        self.assertEqual(badge.group(2), "2")

    def test_a_broken_engine_never_breaks_the_page(self):
        with patch.object(todo, "count", side_effect=RuntimeError("broken")):
            badge = self._badge()
        self.assertIn("hidden", badge.group(1).split())
        self.assertEqual(badge.group(2), "0")


class DismissedNoticesTest(TodoTestBase):
    def test_returns_the_names_closed_with_their_x(self):
        self.assertEqual(ui_state.dismissed_notices(), set())
        ui_state.dismiss_notice("store-run:12", "store-page:abc:draft")
        ui_state.dismiss(ui_state.KEYWORD_CLEANUP_BANNER)   # not a notice with an X
        self.assertEqual(ui_state.dismissed_notices(), {"store-run:12", "store-page:abc:draft"})


class BriefTest(TodoTestBase):
    def setUp(self):
        super().setUp()
        self.app = App.objects.create(name="Moonpond")
        self.now = timezone.now()

    def read(self, keyword, rank, days_ago, country="us"):
        result = SearchResult.objects.create(keyword=keyword, country=country, app_rank=rank,
                                             difficulty_score=40, popularity_score=50)
        SearchResult.objects.filter(pk=result.pk).update(searched_at=self.now - timedelta(days=days_ago))

    def test_counts_keywords_top_ten_and_moves_within_three_days(self):
        sleep = Keyword.objects.create(keyword="sleep sounds", app=self.app)
        rain = Keyword.objects.create(keyword="rain noise", app=self.app)
        focus = Keyword.objects.create(keyword="focus timer", app=self.app)
        # sleep sounds: up in the US against a read two days older; top 10 in two countries, counted once.
        self.read(sleep, 8, days_ago=2)
        self.read(sleep, 5, days_ago=0)
        self.read(sleep, 3, days_ago=0, country="gb")
        # rain noise: worse than five days ago, too old to compare, so no move.
        self.read(rain, 9, days_ago=5)
        self.read(rain, 15, days_ago=0)
        # focus timer: down against yesterday; not ranked in Germany.
        self.read(focus, 20, days_ago=1)
        self.read(focus, 30, days_ago=0)
        self.read(focus, None, days_ago=0, country="de")
        # Another app's keyword is not counted.
        other = App.objects.create(name="Elsewhere")
        self.read(Keyword.objects.create(keyword="sleep sounds", app=other), 1, days_ago=0)

        self.assertEqual(rank_change.brief(self.app), {
            "keywords": 3, "top10": 1, "ranking": 4, "pairs": 5, "countries": 3, "up": 1, "down": 1,
        })

    def test_a_second_call_with_no_change_reads_only_the_revision(self):
        self.read(Keyword.objects.create(keyword="sleep sounds", app=self.app), 4, days_ago=0)
        first = rank_change.brief(self.app)
        with self.assertNumQueries(1):
            self.assertEqual(rank_change.brief(self.app), first)

    def test_a_change_is_worked_out_again(self):
        sleep = Keyword.objects.create(keyword="sleep sounds", app=self.app)
        self.read(sleep, 4, days_ago=0)
        self.assertEqual(rank_change.brief(self.app)["top10"], 1)
        self.read(sleep, 40, days_ago=0)
        self.assertEqual(rank_change.brief(self.app)["top10"], 0)

    def test_within_gap_counts_the_readers_days(self):
        self.assertTrue(rank_change.within_gap(self.now, self.now - timedelta(days=3)))
        self.assertFalse(rank_change.within_gap(self.now, self.now - timedelta(days=4)))
