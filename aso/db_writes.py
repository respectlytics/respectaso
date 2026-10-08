"""Large writes in short batches, so no writer holds the database for long.

Every process shares one SQLite database, and a write holds its write lock
until it commits (core/database.py). The upkeep that grows with the user's
data, such as deleting a backlog of old rows, used to be one statement: on a
2 GB database the hourly cleanup held the lock for 3.4 seconds, and the daily
refresh behind it failed with "database is locked". In batches each write
takes a moment, and other writers take their turn in between.

Free-tier module: it names no other app.
"""

BATCH = 500


def _rows(queryset, ids):
    """The rows with these primary keys, on the queryset's own database."""
    return queryset.model._base_manager.db_manager(queryset.db).filter(pk__in=ids)


def delete_in_batches(queryset, batch: int = BATCH) -> int:
    """Delete the rows of ``queryset``, ``batch`` at a time, each batch its
    own transaction. Returns how many rows of its model went (cascades are
    not counted)."""
    model = queryset.model
    ids = list(queryset.values_list("pk", flat=True))
    deleted = 0
    for start in range(0, len(ids), batch):
        _total, per_model = _rows(queryset, ids[start:start + batch]).delete()
        deleted += per_model.get(model._meta.label, 0)
    return deleted


def update_in_batches(queryset, batch: int = BATCH, **values) -> int:
    """``queryset.update(**values)``, ``batch`` rows at a time. Returns how
    many rows changed."""
    ids = list(queryset.values_list("pk", flat=True))
    updated = 0
    for start in range(0, len(ids), batch):
        updated += _rows(queryset, ids[start:start + batch]).update(**values)
    return updated


def rows_in_batches(queryset, batch: int = BATCH):
    """Yield the rows of ``queryset``, read ``batch`` at a time by short
    queries, for a loop that writes as it goes.

    ``queryset.iterator()`` keeps one read open for the whole loop. In WAL
    mode a connection whose read began before another writer committed may
    not write until that read ends, so the loop's first write after any other
    write failed at once with "database is locked": on a 2 GB database the
    one-time history rescoring failed that way at launch (2026-10-08). Here
    each batch is read in full before the caller sees its first row."""
    ids = list(queryset.values_list("pk", flat=True))
    for start in range(0, len(ids), batch):
        yield from list(queryset.filter(pk__in=ids[start:start + batch]))
