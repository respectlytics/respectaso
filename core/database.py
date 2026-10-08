"""How every RespectASO process opens its one SQLite database.

Several writers share it: the daily refresh, the run queue's searches and AI
runs, the hourly upkeep, page requests, the Rival Tracker, and the MCP server,
which is a second process. With SQLite's defaults a writer that met another
writer failed with "database is locked": at once when its transaction had
started by reading (SQLite refuses to wait there, to avoid a deadlock), or
after 5 seconds behind a long write such as the hourly upkeep deleting a
backlog of old rows. A keyword then missed its daily update (2026-10-08).

- WAL journal: pages read while a writer writes, and a writer never waits for
  readers.
- IMMEDIATE transactions: an atomic block takes the write lock when it begins,
  so a second writer waits its turn instead of failing at once.
- A 30 second wait: the longest write left (a user deleting thousands of
  keywords at once) takes a few seconds; the upkeep writes in short batches
  (aso/db_writes.py).
- journal_size_limit: after a checkpoint the WAL is cut back to 64 MB, so it
  never keeps the disk space a large deletion freed (aso/disk_space.py).

WAL needs the database on a local disk: the Mac app keeps it in Application
Support, the Docker image in a named volume (docker-compose.yml), never a
network share. Both editions' settings use these options.
"""

SQLITE_OPTIONS = {
    "init_command": "PRAGMA journal_mode=WAL;PRAGMA journal_size_limit=67108864",
    "transaction_mode": "IMMEDIATE",
    "timeout": 30,
}
