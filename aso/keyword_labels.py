"""Labels the user puts on keywords, to group them their own way.

The Search History filters sort keywords by what the data says (app,
country, Insight, popularity, difficulty). With hundreds of keywords people
also need to record what they decided: "competitor", "seasonal", "shortlist
for the next update" (public issue #25). A label is that decision.

Kept small on purpose: a label belongs to the keyword (so every country it is
tracked in shows it), it is added and removed from the Dashboard's
multi-select bar or with the x on a chip, the Filters panel filters by it,
and the CSV export carries it. There is no page to manage labels: a label
exists while a keyword carries it.

Called labels, not tags, because the Insight badges (Sweet Spot, Low Volume)
are already "Insight tags" in the app and on the website, and a Tags filter
beside the Insight filter read as the same thing (2026-09-30).

This module is the only writer of KeywordLabel. Ships in the free-tier `aso`
app, so it must not import from aso_pro or licensing.
"""

from __future__ import annotations

from collections import defaultdict

from django.db.models import Count

MAX_LENGTH = 40


class LabelError(ValueError):
    """A label name the user cannot use, with the sentence that says why."""


def clean_name(raw) -> str:
    """The name as the user meant it: spaces trimmed and collapsed.

    Raises LabelError with a sentence for the user when nothing is left or the
    name is too long.
    """
    name = " ".join(str(raw or "").split())
    if not name:
        raise LabelError("Type a name for the label.")
    if len(name) > MAX_LENGTH:
        raise LabelError(f"A label can be at most {MAX_LENGTH} characters.")
    return name


def canonical(name: str) -> str:
    """The spelling already in use for this name, whatever its case, so
    "Seasonal" and "seasonal" stay one label."""
    from .models import KeywordLabel

    existing = (
        KeywordLabel.objects.filter(name__iexact=name)
        .order_by("id").values_list("name", flat=True).first()
    )
    return existing or name


def _keyword_ids(ids) -> list[int]:
    from .models import Keyword

    wanted = set()
    for value in ids or []:
        try:
            wanted.add(int(value))
        except (TypeError, ValueError):
            continue
    return list(Keyword.objects.filter(pk__in=wanted).values_list("pk", flat=True))


def add(keyword_ids, raw_name) -> tuple[str, int]:
    """Put the label on these keywords. Returns (the name used, how many
    keywords got it now); keywords that already carry it are left alone."""
    from .models import KeywordLabel

    name = canonical(clean_name(raw_name))
    ids = _keyword_ids(keyword_ids)
    already = set(
        KeywordLabel.objects.filter(keyword_id__in=ids, name=name)
        .values_list("keyword_id", flat=True)
    )
    new = [KeywordLabel(keyword_id=pk, name=name) for pk in ids if pk not in already]
    KeywordLabel.objects.bulk_create(new, ignore_conflicts=True)
    return name, len(new)


def remove(keyword_ids, name) -> int:
    """Take the label off these keywords. Returns how many carried it."""
    from .models import KeywordLabel

    removed, _ = KeywordLabel.objects.filter(
        keyword_id__in=_keyword_ids(keyword_ids), name=str(name or ""),
    ).delete()
    return removed


def labels_of(keyword_ids) -> list[dict]:
    """The labels these keywords carry, each with how many of them carry it,
    for the "remove a label" list of the multi-select bar."""
    from .models import KeywordLabel

    rows = (
        KeywordLabel.objects.filter(keyword_id__in=_keyword_ids(keyword_ids))
        .values("name").annotate(count=Count("id")).order_by("name")
    )
    return [{"name": row["name"], "count": row["count"]} for row in rows]


def names_in_use(app_id=None) -> list[str]:
    """Every label on a keyword that has a row in Search History, for the
    filter and the suggestions; scoped to one app when the table is."""
    from .models import KeywordLabel

    rows = KeywordLabel.objects.filter(keyword__results__isnull=False)
    if app_id:
        rows = rows.filter(keyword__app_id=app_id)
    return sorted(set(rows.values_list("name", flat=True)), key=str.lower)


def names_by_keyword(keyword_ids) -> dict[int, list[str]]:
    """{keyword id: its label names, in order}, for rows and exports."""
    from .models import KeywordLabel

    found: dict[int, list[str]] = defaultdict(list)
    for keyword_id, name in KeywordLabel.objects.filter(
        keyword_id__in=list(keyword_ids)
    ).values_list("keyword_id", "name"):
        found[keyword_id].append(name)
    return {pk: sorted(names, key=str.lower) for pk, names in found.items()}


def keyword_ids_labelled(names):
    """A subquery of the keywords that carry any of these labels, to filter a
    queryset with ``keyword_id__in``."""
    from .models import KeywordLabel

    return KeywordLabel.objects.filter(name__in=list(names)).values("keyword_id")
