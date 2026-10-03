/**
 * AsoRunQueue - the run queue and progress panel shared by the three Pro AI
 * tabs and the Keyword Research tab.
 *
 * One run executes at a time across the AI Niche Researcher, the AI
 * Competitor Analyzer, the ASO Score Simulator and keyword searches;
 * everything else waits in an order the user can change (move up or down,
 * run next, run now). This module is the ONLY code that polls a run's
 * progress and draws #progress-section and #queue-section.
 *
 * On the AI tabs, load AFTER static/js/ai-tabs-shared.js (window.escapeHtml)
 * and static/js/progress-ticker.js (window.progressTicker). The dashboard
 * has neither: it only uses the queue panel, and escapes on its own.
 *
 * Usage (see any of the three AI templates and the dashboard):
 *   AsoRunQueue.init({feature, statusUrl, removeUrl, clearUrl, moveUrl,
 *                     runNowUrl, cancelUrl, progressUrl, csrfToken,
 *                     startLabel, queueLabel, runningTitle, refiningTitle,
 *                     isIdle, openSession, onChanged});
 */
(function () {
    'use strict';

    var POLL_MS = 2000;

    var FEATURE_BADGE_LABELS = {
        researcher: 'Researcher',
        competitor: 'Competitor',
        simulator: 'Simulator',
        keyword_search: 'Keyword search',
        opportunity_scan: 'Country scan'
    };

    // The runs the activity panel's job row draws itself (static/js/job-strip.js).
    var JOB_ROW_FEATURES = ['keyword_search', 'opportunity_scan'];

    // Complete class strings - Tailwind only extracts whole literals.
    var FEATURE_BADGE_CLASSES = {
        researcher: 'bg-purple-900/30 text-purple-300',
        competitor: 'bg-amber-900/30 text-amber-300',
        simulator: 'bg-sky-900/30 text-sky-300',
        keyword_search: 'bg-teal-900/30 text-teal-300',
        opportunity_scan: 'bg-indigo-900/30 text-indigo-300'
    };

    var ICON_UP = '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 15l7-7 7 7"/></svg>';
    var ICON_DOWN = '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/></svg>';
    var ICON_REMOVE = '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>';

    var cfg = null;
    var timer = null;
    var payload = null;          // the latest queue status
    var busy = false;            // anything running or queued, anywhere
    var lastBusyWith = '';       // what runs now, in words ("the ranking refresh"), for the queued note
    var lastRun = null;          // {id, label} of the run this tab last showed
    var handledIds = {};         // runs whose outcome has already been surfaced
    var tickerRunId = null;      // run id the progress ticker was started for
    var cancelBtnHtml = null;    // pristine Cancel button markup
    var cancelling = false;
    var noticeRun = null;        // the finished run shown in the queue notice
    var lastSignature = null;    // running + queued ids, to fire onChanged
    var lastElsewhere = null;    // {feature, id} of the other tab's run last seen running
    var pendingFinished = null;  // that run, once it stopped running, until its outcome is known
    var ending = null;           // {feature, since}: a run stopped, its outcome not known yet
    var outcome = null;          // {feature, failed}: how the last run ended, for the top bar

    function esc(value) {
        var text = value === null || value === undefined ? '' : String(value);
        if (window.escapeHtml) return window.escapeHtml(text);
        var div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    function byId(id) {
        return document.getElementById(id);
    }

    function setText(id, text) {
        var el = byId(id);
        if (el) el.textContent = text;
    }

    function toggle(id, visible) {
        var el = byId(id);
        if (el) el.classList.toggle('hidden', !visible);
    }

    function post(url, fields) {
        var body = new URLSearchParams();
        Object.keys(fields || {}).forEach(function (key) {
            body.append(key, fields[key]);
        });
        return fetch(url, {
            method: 'POST',
            headers: {
                'X-CSRFToken': cfg.csrfToken,
                'Content-Type': 'application/x-www-form-urlencoded'
            },
            body: body.toString()
        });
    }

    // "sleep sounds" for an AI run, 1,000 keywords for a keyword search.
    // A run's kind as a small coloured badge, and its name: the same in the
    // running row and in Up next.
    function badgeHtml(item) {
        var badgeClass = FEATURE_BADGE_CLASSES[item.feature] || 'bg-slate-700/30 text-slate-300';
        var badgeLabel = FEATURE_BADGE_LABELS[item.feature] || item.feature_label;
        return '<span class="shrink-0 rounded px-1.5 py-0.5 text-2xs font-medium ' + badgeClass + '">' + esc(badgeLabel) + '</span>';
    }

    function runName(item) {
        return item.is_refinement ? 'Refinement of "' + (item.label || '') + '"' : (item.label || '');
    }

    // --- the progress panel -------------------------------------------------

    function runTitle(run) {
        if (run.is_refinement) return cfg.refiningTitle;
        if (!run.label) return cfg.runningTitle;
        return cfg.runningTitle.replace(/\.\.\.\s*$/, '') + ' "' + run.label + '"...';
    }

    function resetCancelButton() {
        var btn = byId('cancel-btn');
        cancelling = false;
        if (!btn || cancelBtnHtml === null) return;
        btn.innerHTML = cancelBtnHtml;
        btn.disabled = false;
    }

    function renderProgress(run) {
        var section = byId('progress-section');
        if (!section) return;
        if (!run) {
            if (tickerRunId !== null) {
                if (window.progressTicker) progressTicker.hide();
                if (window.hideThrottleBanner) hideThrottleBanner();
                tickerRunId = null;
                resetCancelButton();
            }
            section.classList.add('hidden');
            return;
        }
        section.classList.remove('hidden');
        setText('progress-title', runTitle(run));
        setText('progress-message', run.progress_message || '');
        var bar = byId('progress-bar');
        if (bar) bar.style.width = (run.progress_percent || 0) + '%';
        setText('progress-percent', (run.progress_percent || 0) + '%');

        if (window.progressTicker) {
            if (tickerRunId !== run.id) {
                // A different run now owns the panel - reseed the counters and
                // give it a fresh Cancel button (the previous run may have left
                // it reading "Cancelling...").
                progressTicker.start(run.elapsed_seconds || 0, run.scored_count || 0);
                tickerRunId = run.id;
                resetCancelButton();
            } else {
                progressTicker.syncElapsed(run.elapsed_seconds);
                progressTicker.syncScoredCount(run.scored_count);
            }
            if (run.progress_data) progressTicker.update(run.progress_data);
        }
        if (window.updateThrottleBanner) updateThrottleBanner(run.progress_data || {});
    }

    // --- the queue panel ----------------------------------------------------

    function controlHtml(cls, title, inner, disabled) {
        return '<button type="button" class="' + cls + ' shrink-0 inline-flex items-center justify-center rounded-md border border-white/5 text-slate-400 hover:text-white hover:border-white/20 disabled:opacity-30 disabled:hover:text-slate-400 disabled:hover:border-white/5 transition-colors p-1"' +
            ' title="' + title + '" aria-label="' + title + '"' + (disabled ? ' disabled' : '') + '>' + inner + '</button>';
    }

    function quietHtml(cls, text, title) {
        return '<button type="button" class="' + cls + ' btn-quiet shrink-0 px-1 py-0 text-xs" title="' + title + '">' + text + '</button>';
    }

    // One waiting task on two lines, so a name is never cut to nothing in the
    // activity panel (the owner, 2026-10-02): the kind and the name, then the
    // detail with Run next and Run now; moving and removing on the right.
    function queueRowHtml(item, index, count) {
        var data = ' data-feature="' + esc(item.feature) + '" data-id="' + item.id + '"';
        var actions = '' +
            (index > 0 ? quietHtml('queue-run-next', 'Run next', 'Put this run first in line') : '') +
            (item.can_run_now ? quietHtml('queue-run-now', 'Run now', 'Start this run now; the running keyword search pauses and resumes right after') : '');
        var controls = '' +
            controlHtml('queue-move-up', 'Move up', ICON_UP, index === 0) +
            controlHtml('queue-move-down', 'Move down', ICON_DOWN, index === count - 1) +
            controlHtml('queue-remove-btn text-slate-500 hover:text-red-400', 'Remove from queue', ICON_REMOVE, false);
        return '' +
            '<div class="rounded-lg border border-white/5 bg-slate-800/30 px-3 py-2"' + data + '>' +
                '<div class="flex items-center gap-2.5">' +
                    '<span class="w-3 shrink-0 text-2xs tabular-nums text-slate-500">' + item.position + '</span>' +
                    '<div class="min-w-0 flex-1">' +
                        '<div class="flex min-w-0 items-center gap-1.5">' +
                            badgeHtml(item) +
                            '<span class="truncate text-sm font-medium text-white">' + esc(runName(item)) + '</span>' +
                        '</div>' +
                        '<div class="mt-0.5 flex min-w-0 items-center gap-2">' +
                            '<span class="truncate text-xs text-slate-400">' + esc(item.detail) + '</span>' + actions +
                        '</div>' +
                    '</div>' +
                    '<div class="flex shrink-0 items-center gap-1">' + controls + '</div>' +
                '</div>' +
            '</div>';
    }

    function renderQueue() {
        var section = byId('queue-section');
        if (!section) return;
        var queued = (payload && payload.queued) || [];
        var elsewhereRun = payload && payload.running_elsewhere;
        var windingDown = !!payload && payload.lane_state === 'winding_down';
        var busyWith = payload && payload.busy_with;
        lastBusyWith = busyWith || '';

        // Notice: a run finished while the user was busy elsewhere.
        toggle('queue-notice', !!noticeRun);
        if (noticeRun) {
            var name = noticeRun.label ? '"' + noticeRun.label + '"' : 'your run';
            setText('queue-notice-text',
                (noticeRun.status === 'completed' ? 'Finished: ' : 'Failed: ') + name);
            setText('queue-notice-action',
                noticeRun.status === 'completed' ? 'View results' : 'See details');
        }

        // The run that is going, first, on every page: its own page too (the
        // owner, 2026-10-02: on the Simulator page the running simulation was
        // missing from the panel, so the queue looked as if it had dropped it).
        // The activity panel's job row (static/js/job-strip.js) already shows a
        // running keyword search or country scan, so this row shows only the
        // other runs: one task, one row.
        var runningRun = elsewhereRun || (payload && payload.running_here);
        var shownRunning = runningRun && JOB_ROW_FEATURES.indexOf(runningRun.feature) === -1 ? runningRun : null;
        toggle('queue-running', !!shownRunning);
        if (shownRunning) {
            // Drawn like a row of Up next; the full detail belongs to that
            // tab's own panel.
            var name = byId('queue-running-name');
            if (name) {
                name.innerHTML = badgeHtml(shownRunning) +
                    '<span class="truncate text-sm font-medium text-white">' + esc(runName(shownRunning)) + '</span>';
            }
            setText('queue-running-detail', shownRunning.detail || '');
            setText('queue-running-pct', (shownRunning.progress_percent || 0) + '%');
            var fill = byId('queue-running-fill');
            if (fill) fill.style.width = (shownRunning.progress_percent || 0) + '%';
            var link = byId('queue-running-link');
            if (link) link.href = shownRunning.url || '#';
        }

        // Nothing runs, but the next run cannot start yet.
        var waitingText = '';
        if (windingDown) {
            waitingText = 'Finishing the run that just stopped, then starting the next one...';
        } else if (busyWith && queued.length) {
            waitingText = 'Waiting for ' + busyWith + ' to finish, then starting the next run...';
        }
        toggle('queue-winding-down', !!waitingText);
        setText('queue-winding-down', waitingText);

        // Up next.
        toggle('queue-header', queued.length > 0);
        setText('queue-count',
            queued.length === 1 ? '(1 run)' : '(' + queued.length + ' runs)');
        var list = byId('queue-list');
        if (list) {
            list.innerHTML = queued.map(function (item, index) {
                return queueRowHtml(item, index, queued.length);
            }).join('');
        }

        section.classList.toggle(
            'hidden',
            !noticeRun && !shownRunning && !waitingText && queued.length === 0
        );
        reportActivity(runningRun, queued.length);
    }

    // Tell the activity indicator in the top bar (static/js/activity-indicator.js).
    // A run that just stopped stays "running" until its outcome is known (at
    // most a few seconds), so the top bar says Done or Stopped truthfully.
    function reportActivity(run, waiting) {
        if (!window.ActivityIndicator) return;
        if (ending && Date.now() - ending.since > 6000) ending = null;
        var state = {waiting: waiting};
        if (run) {
            state.running = {label: FEATURE_BADGE_LABELS[run.feature] || run.feature_label || 'Run',
                             pct: run.progress_percent || 0};
        } else if (ending) {
            state.running = {label: FEATURE_BADGE_LABELS[ending.feature] || 'Run', pct: 100};
        }
        if (outcome) {
            state.done = {label: FEATURE_BADGE_LABELS[outcome.feature] || 'Run', failed: outcome.failed};
        }
        window.ActivityIndicator.report('queue', state.running || waiting || outcome ? state : null);
        if (!state.running && !waiting) outcome = null;   // said once
    }

    function syncStartButton() {
        var btn = byId('start-btn');
        if (btn) btn.textContent = busy ? cfg.queueLabel : cfg.startLabel;
        toggle('queue-hint', busy);
    }

    // --- finished runs ------------------------------------------------------

    function handleFinished(previous) {
        // The dashboard has its own panel for keyword searches and no
        // progress endpoint to ask.
        if (!previous || handledIds[previous.id] || !cfg.progressUrl) return;
        handledIds[previous.id] = true;
        ending = {feature: cfg.feature, since: Date.now()};
        fetch(cfg.progressUrl(previous.id))
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (data) {
                // A 404 means the row was deleted - nothing to report.
                if (!data) return;
                ending = null;
                outcome = {feature: cfg.feature, failed: data.status === 'failed'};
                reportActivity(null, (payload && payload.queued || []).length);
                if (data.status !== 'completed' && data.status !== 'failed') return;
                var run = {
                    id: previous.id,
                    status: data.status,
                    error: data.error,
                    failure: data.failure,
                    label: previous.label,
                    feature: cfg.feature
                };
                if (cfg.isIdle && cfg.isIdle()) {
                    cfg.openSession(run);
                } else {
                    noticeRun = run;
                    renderQueue();
                }
            })
            .catch(function () { /* ignore transient errors */ });
    }

    // --- polling ------------------------------------------------------------

    function apply(data) {
        payload = data;

        // A run from another tab ended: ask once how, then show it as done.
        var elsewhere = data.running_elsewhere;
        if (lastElsewhere && (!elsewhere || elsewhere.id !== lastElsewhere.id || elsewhere.feature !== lastElsewhere.feature)) {
            pendingFinished = lastElsewhere;
            ending = {feature: lastElsewhere.feature, since: Date.now()};
        }
        lastElsewhere = elsewhere ? {feature: elsewhere.feature, id: elsewhere.id} : null;
        if (data.finished && pendingFinished && data.finished.id === pendingFinished.id
                && data.finished.feature === pendingFinished.feature) {
            pendingFinished = null;
            ending = null;
            outcome = {feature: data.finished.feature, failed: data.finished.status === 'failed'};
            if (data.finished.status !== 'cancelled') {
                noticeRun = {id: data.finished.id, status: data.finished.status, label: data.finished.label,
                             feature: data.finished.feature, url: data.finished.url};
            }
        }
        busy = data.lane_state !== 'idle' || data.queued.length > 0;

        var here = data.running_here;
        if (lastRun && (!here || here.id !== lastRun.id)) handleFinished(lastRun);
        lastRun = here ? {id: here.id, label: here.label} : null;

        renderProgress(here);
        renderQueue();
        syncStartButton();

        var signature = (here ? here.id : '-') + ':' +
            data.queued.map(function (item) { return item.id; }).join(',');
        if (lastSignature !== null && signature !== lastSignature && cfg.onChanged) {
            cfg.onChanged();
        }
        lastSignature = signature;
    }

    function refresh() {
        var url = cfg.statusUrl;
        if (pendingFinished) {
            url += (url.indexOf('?') === -1 ? '?' : '&') + 'finished=' +
                encodeURIComponent(pendingFinished.feature + ':' + pendingFinished.id);
        }
        return fetch(url)
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (data) { if (data && !data.error) apply(data); })
            .catch(function () { /* ignore transient errors */ });
    }

    // --- public API ---------------------------------------------------------

    function removeQueued(feature, id) {
        return post(cfg.removeUrl, {feature: feature, session_id: id})
            .then(refresh)
            .catch(function () {});
    }

    function moveQueued(feature, id, direction) {
        if (!cfg.moveUrl) return Promise.resolve();
        // Re-fetch the status instead of reordering the DOM by hand.
        return post(cfg.moveUrl, {feature: feature, session_id: id, direction: direction})
            .then(refresh)
            .catch(function () {});
    }

    function runNow(feature, id) {
        if (!cfg.runNowUrl) return Promise.resolve();
        return post(cfg.runNowUrl, {feature: feature, session_id: id})
            .then(refresh)
            .catch(function () {});
    }

    function clearQueue() {
        var count = (payload && payload.queued.length) || 0;
        if (!count) return Promise.resolve();
        var message = count === 1
            ? 'Remove the queued run?'
            : 'Remove all ' + count + ' queued runs?';
        return window.showConfirm(message, {
            title: 'Clear queue',
            confirmLabel: 'Remove',
            cancelLabel: 'Keep',
            confirmStyle: 'danger'
        }).then(function (ok) {
            if (!ok) return;
            return post(cfg.clearUrl, {}).then(refresh);
        });
    }

    function init(options) {
        cfg = options;
        if (timer) clearInterval(timer);
        payload = null;
        busy = false;
        lastRun = null;
        handledIds = {};
        tickerRunId = null;
        noticeRun = null;
        lastSignature = null;

        var cancelBtn = byId('cancel-btn');
        if (cancelBtn && cancelBtnHtml === null) cancelBtnHtml = cancelBtn.innerHTML;

        var list = byId('queue-list');
        if (list) {
            list.addEventListener('click', function (event) {
                var btn = event.target.closest('button');
                if (!btn || btn.disabled) return;
                var row = btn.closest('[data-feature]');
                if (!row) return;
                var feature = row.dataset.feature;
                var id = row.dataset.id;
                if (btn.classList.contains('queue-remove-btn')) removeQueued(feature, id);
                else if (btn.classList.contains('queue-move-up')) moveQueued(feature, id, 'up');
                else if (btn.classList.contains('queue-move-down')) moveQueued(feature, id, 'down');
                else if (btn.classList.contains('queue-run-next')) moveQueued(feature, id, 'top');
                else if (btn.classList.contains('queue-run-now')) runNow(feature, id);
            });
        }
        var clearBtn = byId('queue-clear-btn');
        if (clearBtn) clearBtn.addEventListener('click', clearQueue);

        var noticeAction = byId('queue-notice-action');
        if (noticeAction) {
            noticeAction.addEventListener('click', function () {
                var run = noticeRun;
                noticeRun = null;
                renderQueue();
                if (run && run.url && (!cfg.openSession || run.feature !== cfg.feature)) { window.location.href = run.url; return; }
                if (run && cfg.openSession) cfg.openSession(run);
            });
        }
        var noticeDismiss = byId('queue-notice-dismiss');
        if (noticeDismiss) {
            noticeDismiss.addEventListener('click', function () {
                noticeRun = null;
                renderQueue();
            });
        }

        syncStartButton();
        refresh();
        timer = setInterval(refresh, POLL_MS);
    }

    // The shared progress partial's Cancel button calls this by name.
    window.cancelSession = function () {
        var run = payload && payload.running_here;
        if (!run || cancelling) return;
        cancelling = true;
        var btn = byId('cancel-btn');
        if (btn) {
            btn.disabled = true;
            btn.textContent = 'Cancelling...';
        }
        post(cfg.cancelUrl(run.id))
            .then(refresh)
            .catch(function () { resetCancelButton(); });
    };

    window.AsoRunQueue = {
        init: init,
        refresh: refresh,
        isBusy: function () { return busy; },
        busyWith: function () { return lastBusyWith; },
        started: function () { return cfg !== null; },
        syncStartButton: syncStartButton,
        removeQueued: removeQueued,
        moveQueued: moveQueued,
        runNow: runNow,
        clearQueue: clearQueue
    };
})();
