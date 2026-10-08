"""Deleted data gives its disk space back to the Mac.

SQLite keeps the pages a deletion frees inside the database file unless the
database is in auto-vacuum mode. A user who tracked 1,600 keywords and deleted
them all still had a 1.6 GB file (public issue #27). So:

- right after migrate, ``convert_after_migrate`` puts the database in
  ``auto_vacuum = FULL`` with one VACUUM, the only way SQLite changes that
  mode once a table exists. On an older database the VACUUM also hands back
  everything earlier deletions left inside the file; on a new one it takes a
  moment. Every database the app uses is migrated first: by the Mac app
  (desktop/main.py) and Docker (entrypoint.sh) before their server starts,
  by the MCP server, and by the test runner;
- from then on SQLite shortens the file at every commit that frees pages, so
  every deletion anywhere gives its space back, with no call site to forget.

Rows that outlive a deleted keyword because they are keyed by its text, not
by the Keyword row, are deleted by their own modules' cleanups, listed in
``after_keywords_deleted``. ``keywords_deleted`` (a post_delete receiver of
Keyword) runs them once per deleting transaction, after it commits, and then
``checkpoint`` writes the WAL into the file (core/database.py), so the space
leaves the disk at once.

Free-tier module: it names no other app.
"""

import logging

from django.db import DatabaseError, connections, transaction

logger = logging.getLogger(__name__)

FULL = 1  # PRAGMA auto_vacuum: 0 none, 1 full, 2 incremental


def ensure_full_auto_vacuum(cursor) -> bool:
    """Convert the database behind ``cursor`` to FULL auto-vacuum. True when
    it converted, False when it already was. Must run outside a transaction:
    VACUUM rewrites the whole file and needs it to itself."""
    cursor.execute("PRAGMA auto_vacuum")
    if cursor.fetchone()[0] == FULL:
        return False
    cursor.execute(f"PRAGMA auto_vacuum = {FULL}")
    cursor.execute("VACUUM")
    return True


def _file_size(cursor) -> int:
    cursor.execute("PRAGMA page_count")
    pages = cursor.fetchone()[0]
    cursor.execute("PRAGMA page_size")
    return pages * cursor.fetchone()[0]


def convert_after_migrate(sender, using, **kwargs):
    """post_migrate: convert an older database once. A failure (another
    process holds the database, the disk is too full for the rewrite) leaves
    it as it is, and the next launch tries again."""
    connection = connections[using]
    if connection.vendor != "sqlite" or connection.in_atomic_block:
        return
    try:
        with connection.cursor() as cursor:
            before = _file_size(cursor)
            if ensure_full_auto_vacuum(cursor):
                logger.info("Database now gives deleted data's disk space back: %.1f MB, was %.1f MB.",
                            _file_size(cursor) / 1e6, before / 1e6)
        # The rewrite went through the WAL: cut that back too.
        checkpoint(using)
    except DatabaseError as e:
        logger.warning("Could not convert the database to give disk space back; the next launch tries again: %s", e)


def checkpoint(using="default") -> None:
    """Write the WAL into the database file and cut the WAL back to nothing
    (core/database.py), so the space a deletion freed leaves the disk now,
    not at SQLite's next automatic checkpoint. If a reader is still in the
    way after the busy wait, SQLite stops short and a later automatic
    checkpoint finishes the job."""
    connection = connections[using]
    if connection.vendor != "sqlite" or connection.in_atomic_block:
        return
    with connection.cursor() as cursor:
        cursor.execute("PRAGMA wal_checkpoint(TRUNCATE)")


# The cleanups that delete what a deleted keyword leaves behind in tables
# keyed by its text: aso.day_reads.forget_untracked (aso/apps.py) and, in
# Pro, aso_pro.rivals.ranks.forget_untracked (aso_pro/rivals/hooks.py).
after_keywords_deleted: list = []


def keywords_deleted(sender, **kwargs):
    """post_delete of Keyword (aso/apps.py): once the deleting transaction
    commits, run every cleanup, then hand the space back to the disk."""
    after_commit_once(_clean_up_after_keywords, using=kwargs.get("using") or "default")


def _clean_up_after_keywords():
    for cleanup in after_keywords_deleted[:]:
        try:
            cleanup()
        except Exception:  # one cleanup's failure must not stop the others or the checkpoint
            logger.exception("Cleaning up after deleted keywords failed")
    checkpoint()


def after_commit_once(fn, using="default") -> None:
    """Run ``fn`` once after the current transaction commits, however many
    rows of it asked (a post_delete receiver is called for every row of a
    deletion). Outside a transaction it runs at once. A failure is logged and
    never undoes the deletion that asked for it.

    "Once" is looked up in Django's own list of the transaction's pending
    callbacks, so a rolled back transaction, which empties that list, leaves
    nothing behind that would stop the next one."""
    connection = connections[using]
    if any(getattr(entry[1], "after_commit_once", None) is fn for entry in connection.run_on_commit):
        return

    def run():
        try:
            fn()
        except Exception:  # a cleanup must never fail the deletion that asked for it
            logger.exception("Cleaning up after a deletion failed")

    run.after_commit_once = fn
    transaction.on_commit(run, using=using)
