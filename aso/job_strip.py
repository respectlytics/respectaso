"""The global progress strip: one line about whatever long job is running.

The app now has two of them, a keyword search and a country scan, and the
strip shows whichever one is actually going. The sentence is composed HERE and
not in JavaScript, because it depends on data: how many countries, how many
keywords, what state. static/js/keyword-search-job.js already said as much in
its own docstring while composing the strip sentence itself; this fixes that.

Never raises. A strip that cannot render must not take a page down with it.
"""

from __future__ import annotations

from django.urls import reverse

from . import opportunity_scans, search_jobs


def _keyword_line(job) -> tuple[str, str, str]:
    """(text, link label, icon) for a keyword search."""
    done = search_jobs.fmt(job.keywords_done)
    total = search_jobs.fmt(job.total_keywords)
    if job.status == "running":
        return f"Keyword research running: {done} of {total} keywords", "Open", "spinner"
    if job.status == "queued":
        return (f"Keyword research queued: {search_jobs.keywords_text(job.total_keywords)}",
                "Open", "spinner")
    if job.status == "completed":
        return (f"Keyword research finished: {search_jobs.keywords_text(job.total_keywords)}",
                "See results", "done")
    if job.status == "cancelled":
        return (f"Keyword research stopped at {done} of {total} keywords",
                "See results", "done")
    return (f"Keyword research paused at {done} of {total} keywords",
            "Open" if job.auto_resume else "Resume", "pause")


def _scan_line(scan) -> tuple[str, str, str]:
    """(text, link label, icon) for a country scan."""
    done = search_jobs.fmt(scan.done_count)
    total = search_jobs.fmt(scan.total_countries)
    noun = "country" if scan.total_countries == 1 else "countries"
    if scan.status == "running":
        return f"Country scan running: {done} of {total} {noun}", "Open", "spinner"
    if scan.status == "queued":
        return f"Country scan queued: {total} {noun}", "Open", "spinner"
    if scan.status == "completed":
        return f"Country scan finished: {total} {noun}", "See results", "done"
    if scan.status == "cancelled":
        return f"Country scan stopped at {done} of {total} {noun}", "See results", "done"
    return (f"Country scan paused at {done} of {total} {noun}",
            "Open" if scan.auto_resume else "Resume", "pause")


def _is_active(row) -> bool:
    return row.status in ("running", "queued") or bool(row.auto_resume)


def strip_state() -> dict | None:
    """What the strip shows, or None when there is nothing to say.

    The running row wins whichever feature it belongs to, then the newest
    active row, then the newest finished row the user has not dismissed.
    """
    job = search_jobs.strip_job()
    scan = opportunity_scans.strip_scan()

    candidates = []
    if job is not None:
        candidates.append(("keyword_search", job))
    if scan is not None:
        candidates.append(("opportunity_scan", scan))
    if not candidates:
        return None

    def rank(entry):
        _kind, row = entry
        running = row.status == "running"
        active = _is_active(row)
        finished_at = row.finished_at.timestamp() if row.finished_at else 0
        return (running, active, finished_at, row.created_at.timestamp())

    kind, row = max(candidates, key=rank)

    if kind == "keyword_search":
        text, link_label, icon = _keyword_line(row)
        url = reverse("aso:dashboard")
        percent = row.progress_percent
    else:
        text, link_label, icon = _scan_line(row)
        url = reverse("aso:opportunity")
        percent = row.progress_percent

    return {
        "kind": kind,
        "text": text,
        "link_label": link_label,
        "link_url": url,
        "icon": icon,
        "progress_percent": percent,
        "show_bar": row.status == "running",
        "active": _is_active(row),
    }
