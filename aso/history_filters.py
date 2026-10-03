"""The Search History filters, read once and applied one way.

The Dashboard's table and its CSV export each used to read the filters from
the address and apply them in their own copy, which already differed in how
they read a number. Both now go through here, so an export always holds the
rows the table shows, whatever filter is added next.

    HistoryFilters.from_query(request.GET)   what the address asks for
    latest_ids(filters)                      the newest row of every keyword
                                             and country in the app and
                                             country the table is scoped to
    narrow(results, filters)                 search text, popularity,
                                             difficulty, Insight and labels

Ships in the free-tier `aso` app, so it must not import from aso_pro or
licensing.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

from django.db.models import Max

# The page sizes the Dashboard's table offers, and the one it opens with.
HISTORY_PER_PAGE_CHOICES = (25, 50, 100, 200)
HISTORY_PER_PAGE_DEFAULT = 25

# The address keys of the filters: those that may repeat, then those that
# hold one value. The page script keeps the same two lists
# (HISTORY_FILTER_LISTS and HISTORY_FILTER_VALUES in dashboard.html), and
# aso/tests/test_ui_memory.py checks that they agree.
FILTER_LIST_KEYS = ("insight", "label")
FILTER_VALUE_KEYS = ("pop_min", "diff_max", "q")
FILTER_KEYS = FILTER_LIST_KEYS + FILTER_VALUE_KEYS


def _int_or_none(raw):
    try:
        return int(raw) if raw not in (None, "") else None
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class HistoryFilters:
    app_id: int | None = None
    country: str = ""
    insights: tuple[str, ...] = ()
    labels: tuple[str, ...] = ()
    pop_min: int | None = None
    diff_max: int | None = None
    q: str = ""

    @classmethod
    def from_query(cls, query) -> HistoryFilters:
        from .scoring import CLASSIFICATION_LABELS

        return cls(
            app_id=_int_or_none(query.get("app")),
            country=(query.get("country") or "").strip().lower(),
            insights=tuple(i for i in query.getlist("insight") if i in CLASSIFICATION_LABELS),
            labels=tuple(dict.fromkeys(t for t in query.getlist("label") if t)),
            pop_min=_int_or_none(query.get("pop_min")),
            diff_max=_int_or_none(query.get("diff_max")),
            q=(query.get("q") or "").strip(),
        )

    @property
    def narrowing(self) -> bool:
        """Whether a filter hides rows of the app and country in view."""
        return bool(
            self.insights or self.labels or self.pop_min is not None
            or self.diff_max is not None or self.q
        )

    def query_string(self) -> str:
        """These filters as an address query, for links that keep the view."""
        pairs = []
        if self.app_id:
            pairs.append(("app", self.app_id))
        if self.country:
            pairs.append(("country", self.country))
        pairs += [("insight", i) for i in self.insights]
        pairs += [("label", t) for t in self.labels]
        if self.pop_min is not None:
            pairs.append(("pop_min", self.pop_min))
        if self.diff_max is not None:
            pairs.append(("diff_max", self.diff_max))
        if self.q:
            pairs.append(("q", self.q))
        return urlencode(pairs)


def latest_ids(filters: HistoryFilters) -> list[int]:
    """The newest row of each keyword and country in the table's scope."""
    from .models import SearchResult

    scope = {}
    if filters.app_id:
        scope["keyword__app_id"] = filters.app_id
    if filters.country:
        scope["country"] = filters.country
    return list(
        SearchResult.objects.filter(**scope)
        .values("keyword_id", "country")
        .annotate(latest_id=Max("id"))
        .values_list("latest_id", flat=True)
    )


def narrow(results, filters: HistoryFilters):
    """Apply the filters that hide rows. Popularity is the effective one, the
    value the row shows under the user's source; the annotation it adds
    (``effective_pop``) is what the table sorts popularity by."""
    from .keyword_labels import keyword_ids_labelled
    from .popularity import annotate_effective_popularity

    if filters.q:
        results = results.filter(keyword__keyword__icontains=filters.q)
    results = annotate_effective_popularity(results)
    if filters.pop_min is not None:
        results = results.filter(effective_pop__isnull=False, effective_pop__gte=filters.pop_min)
    if filters.diff_max is not None:
        results = results.filter(difficulty_score__isnull=False, difficulty_score__lte=filters.diff_max)
    if filters.insights:
        # The stored classification column: classify_keyword() is the single
        # source of truth and sets it on save, so __in is exact.
        results = results.filter(classification__in=filters.insights)
    if filters.labels:
        # Any of the chosen labels, as with Insight.
        results = results.filter(keyword_id__in=keyword_ids_labelled(filters.labels))
    return results
