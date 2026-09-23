"""The Country Opportunity Finder page and its endpoints.

One for one with the keyword search endpoints in aso/views.py, which is the
point: a scan and a search are the same kind of thing (a queued, resumable job
over Apple's rate limit), so they behave the same and the same JavaScript
drives both.

No Pro gate. The Opportunity Finder is free, with the same shape of limit
keyword search uses: one active scan at a time without Pro, and no cap on the
countries either way.
"""

from __future__ import annotations

import json

from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import countries, job_strip, opportunity_scans, run_queue
from .forms import OpportunitySearchForm
from .models import App, OpportunityScan
from .pro_access import has_pro_license


def opportunity_view(request):
    """The page. The scan itself runs in the background, so this only has to
    hand over the current scan and the picker."""
    opportunity_scans.reclaim_stale()
    scan = opportunity_scans.latest_scan()
    return render(request, "aso/opportunity.html", {
        "apps": App.objects.all(),
        "form": OpportunitySearchForm(),
        "scan": opportunity_scans.scan_payload(scan, include_results=True) if scan else None,
        "seconds_per_country": round(opportunity_scans.estimate_seconds(1), 1),
        "total_storefronts": len(countries.CODES),
    })


@require_POST
def opportunity_start_view(request):
    """Start a scan: create the row and answer at once."""
    form = OpportunitySearchForm(request.POST)
    if not form.is_valid():
        return JsonResponse({"error": "Invalid form data."}, status=400)

    keyword = form.cleaned_data["keyword"].strip()
    if not keyword:
        return JsonResponse({"error": "No keyword provided."}, status=400)

    codes = form.cleaned_data.get("countries") or []
    if not codes:
        return JsonResponse({"error": "Pick at least one country to scan."}, status=400)

    is_pro = has_pro_license()
    if not is_pro and opportunity_scans.active_scan() is not None:
        return JsonResponse(
            {"error": opportunity_scans.FREE_BUSY_MESSAGE, "is_pro": False}, status=400,
        )

    app_id = form.cleaned_data.get("app_id")
    app = App.objects.filter(id=app_id).first() if app_id else None

    running = run_queue.running_run()
    queued_behind = None
    eta_seconds = None
    if running is not None:
        feature, row = running
        queued_behind = feature.label
        if feature.key == opportunity_scans.FEATURE_KEY:
            queued_behind = "the current scan"
            eta_seconds = opportunity_scans.scan_payload(row)["eta_seconds"]
    elif run_queue.busy_reason():
        queued_behind = run_queue.busy_reason()

    scan = opportunity_scans.create_scan(keyword, codes, app=app)
    return JsonResponse({
        "scan": opportunity_scans.scan_payload(scan),
        "queued_behind": queued_behind if scan.status == "queued" else None,
        "eta_seconds": eta_seconds if scan.status == "queued" else None,
    })


def _scan_or_404(scan_id):
    return get_object_or_404(OpportunityScan, pk=scan_id)


def opportunity_current_view(request):
    """The poll target: the active scan and the newest finished one."""
    opportunity_scans.reclaim_stale()
    panel = opportunity_scans.panel_scan()
    finished = opportunity_scans.finished_scan()
    return JsonResponse({
        "scan": opportunity_scans.scan_payload(panel, include_results=True) if panel else None,
        "finished": (
            opportunity_scans.scan_payload(finished, include_results=True)
            if finished else None
        ),
    })


def opportunity_detail_view(request, scan_id):
    """One scan with its light results, ranked by opportunity."""
    scan = _scan_or_404(scan_id)
    return JsonResponse({"scan": opportunity_scans.scan_payload(scan, include_results=True)})


def opportunity_country_view(request, scan_id, code):
    """One country's full result: the breakdown and the competitor list.

    Fetched when a row is expanded. Keeping the two heavy columns out of the
    polled payload is what lets a 175 country scan poll every three seconds.
    """
    scan = _scan_or_404(scan_id)
    row = scan.results.filter(country=(code or "").lower()).first()
    if row is None:
        return JsonResponse({"error": "That country is not in this scan."}, status=404)
    return JsonResponse({"country": opportunity_scans.country_payload(row, heavy=True)})


@require_POST
def opportunity_pause_view(request, scan_id):
    scan = _scan_or_404(scan_id)
    updated = OpportunityScan.objects.filter(pk=scan.pk, status="running").update(
        status="paused", auto_resume=False, throttle_state="normal",
        yielded_for_feature="", yielded_for_id=None, yielded_for_label="",
        progress_message="Paused", current_country="",
    )
    if not updated:
        return JsonResponse({"error": "This scan is not running."}, status=400)
    run_queue.kick()
    scan.refresh_from_db()
    return JsonResponse({"scan": opportunity_scans.scan_payload(scan)})


@require_POST
def opportunity_resume_view(request, scan_id):
    """Resume a paused scan. With ``now=1`` it goes first."""
    scan = _scan_or_404(scan_id)
    updated = OpportunityScan.objects.filter(
        pk=scan.pk, status__in=("paused", "failed"),
    ).update(
        status="queued", auto_resume=False, queue_rank=None,
        yielded_for_feature="", yielded_for_id=None, yielded_for_label="",
        error_message="", progress_message="Resuming...",
    )
    if not updated:
        return JsonResponse({"error": "This scan is not paused."}, status=400)
    if request.POST.get("now") in ("1", "true", "on"):
        run_queue.run_now(opportunity_scans.FEATURE_KEY, scan.pk)
    else:
        run_queue.kick()
    scan.refresh_from_db()
    return JsonResponse({"scan": opportunity_scans.scan_payload(scan)})


@require_POST
def opportunity_discard_view(request, scan_id):
    """Discard the rest of a paused scan. The countries already scanned stay
    on the row, so the results you have are never thrown away."""
    scan = _scan_or_404(scan_id)
    if scan.status == "running":
        return JsonResponse({"error": "Pause the scan before discarding the rest."}, status=400)
    if scan.status == "queued":
        removed = run_queue.remove_queued(opportunity_scans.FEATURE_KEY, scan.pk)
        if not removed:
            return JsonResponse({"error": "This scan already started."}, status=400)
        run_queue.kick()
        scan = OpportunityScan.objects.filter(pk=scan.pk).first()
        return JsonResponse({
            "scan": opportunity_scans.scan_payload(scan) if scan else None,
        })
    updated = OpportunityScan.objects.filter(
        pk=scan.pk, status__in=("paused", "failed"),
    ).update(
        status="cancelled", auto_resume=False, finished_at=timezone.now(),
        yielded_for_feature="", yielded_for_id=None, yielded_for_label="",
        progress_message="Discarded the rest", current_country="",
    )
    if not updated:
        return JsonResponse({"error": "This scan already finished."}, status=400)
    run_queue.kick()
    scan.refresh_from_db()
    return JsonResponse({"scan": opportunity_scans.scan_payload(scan, include_results=True)})


@require_POST
def opportunity_retry_failed_view(request, scan_id):
    """A new scan over the countries that could not be checked."""
    scan = _scan_or_404(scan_id)
    if not scan.is_terminal or not scan.failed_items:
        return JsonResponse({"error": "Nothing to scan again."}, status=400)
    if not has_pro_license() and opportunity_scans.active_scan() is not None:
        return JsonResponse(
            {"error": opportunity_scans.FREE_BUSY_MESSAGE, "is_pro": False}, status=400,
        )
    OpportunityScan.objects.filter(pk=scan.pk).update(acknowledged=True)
    new_scan = opportunity_scans.retry_failed_scan(scan)
    return JsonResponse({"scan": opportunity_scans.scan_payload(new_scan)})


@require_POST
def opportunity_dismiss_view(request, scan_id):
    """"Done" on a finished scan: it does not come back as new on reload."""
    OpportunityScan.objects.filter(
        pk=scan_id, status__in=OpportunityScan.TERMINAL_STATUSES,
    ).update(acknowledged=True)
    return JsonResponse({"ok": True})


@require_POST
def opportunity_save_view(request):
    """Copy scan rows into Search History.

    Takes {"scan_id": 12, "countries": ["de", "fr"]} or {"scan_id": 12,
    "all": true}. The rows are read here, so the browser never sends a
    competitor payload back to us and cannot change what gets stored.
    """
    try:
        body = json.loads(request.body or "{}")
    except (ValueError, TypeError):
        return JsonResponse({"error": "Invalid request body."}, status=400)

    scan_id = body.get("scan_id")
    if not scan_id:
        return JsonResponse({"error": "No scan given."}, status=400)
    scan = OpportunityScan.objects.filter(pk=scan_id).first()
    if scan is None:
        return JsonResponse({"error": "That scan no longer exists."}, status=404)

    codes = None if body.get("all") else (body.get("countries") or [])
    if codes is not None and not codes:
        return JsonResponse({"error": "No countries selected."}, status=400)

    saved = opportunity_scans.save_to_history(scan, codes)
    if not saved:
        return JsonResponse({"error": "Nothing to save from this scan."}, status=400)
    return JsonResponse({"success": True, "saved": saved})



def job_strip_state_view(request):
    """What the global bottom strip shows, for either job type."""
    return JsonResponse({"strip": job_strip.strip_state()})
