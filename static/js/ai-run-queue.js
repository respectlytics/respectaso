/**
 * AsoRunQueue - the run queue and progress panel for the three Pro AI tabs.
 *
 * One AI run executes at a time across the AI Niche Researcher, the AI
 * Competitor Analyzer and the ASO Score Simulator; everything else waits in
 * the order it was started. This module is the ONLY code that polls a run's
 * progress and draws #progress-section and #queue-section - each tab used to
 * carry its own near-identical copy.
 *
 * Load AFTER static/js/ai-tabs-shared.js (window.escapeHtml) and
 * static/js/progress-ticker.js (window.progressTicker).
 *
 * Usage (see any of the three AI templates):
 *   AsoRunQueue.init({feature, statusUrl, removeUrl, clearUrl, cancelUrl,
 *                     progressUrl, csrfToken, startLabel, queueLabel,
 *                     runningTitle, refiningTitle, isIdle, openSession,
 *                     onChanged});
 */
(function () {
    'use strict';

    var POLL_MS = 2000;

    // Where each feature lives - the templates hard-code the same /pro/ URLs.
    var FEATURE_URLS = {
        researcher: '/pro/ai-researcher/',
        competitor: '/pro/ai-competitor/',
        simulator: '/pro/simulator/'
    };

    var FEATURE_BADGE_LABELS = {
        researcher: 'Researcher',
        competitor: 'Competitor',
        simulator: 'Simulator'
    };

    // Complete class strings - Tailwind only extracts whole literals.
    var FEATURE_BADGE_CLASSES = {
        researcher: 'bg-purple-900/30 text-purple-300',
        competitor: 'bg-amber-900/30 text-amber-300',
        simulator: 'bg-sky-900/30 text-sky-300'
    };

    var cfg = null;
    var timer = null;
    var payload = null;          // the latest /pro/queue/ answer
    var busy = false;            // anything running or queued, anywhere
    var lastRun = null;          // {id, label} of the run this tab last showed
    var handledIds = {};         // runs whose outcome has already been surfaced
    var tickerRunId = null;      // run id the progress ticker was started for
    var cancelBtnHtml = null;    // pristine Cancel button markup
    var cancelling = false;
    var noticeRun = null;        // the finished run shown in the queue notice
    var lastSignature = null;    // running + queued ids, to fire onChanged

    function esc(value) {
        return window.escapeHtml(value === null || value === undefined ? '' : String(value));
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

    function queueRowHtml(item) {
        var badgeClass = FEATURE_BADGE_CLASSES[item.feature] || 'bg-slate-700/30 text-slate-300';
        var badgeLabel = FEATURE_BADGE_LABELS[item.feature] || item.feature_label;
        var label = item.is_refinement
            ? 'Refinement of "' + (item.label || '') + '"'
            : (item.label || '');
        return '' +
            '<div class="bg-slate-800/30 border border-white/5 rounded-lg px-4 py-3 flex items-center gap-3">' +
                '<span class="text-xs text-slate-500 font-mono tabular-nums w-4 shrink-0">' + item.position + '</span>' +
                '<span class="text-xs px-2 py-0.5 rounded-full whitespace-nowrap shrink-0 ' + badgeClass + '">' + esc(badgeLabel) + '</span>' +
                '<div class="min-w-0 flex-1">' +
                    '<div class="text-sm text-white truncate">' + esc(label) + '</div>' +
                    '<div class="text-xs text-slate-500 truncate">' + esc(item.detail) + '</div>' +
                '</div>' +
                '<button type="button" class="queue-remove-btn text-slate-600 hover:text-red-400 transition-colors p-1 shrink-0"' +
                        ' title="Remove from queue" data-feature="' + esc(item.feature) + '" data-id="' + item.id + '">' +
                    '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>' +
                '</button>' +
            '</div>';
    }

    function renderQueue() {
        var section = byId('queue-section');
        if (!section) return;
        var queued = (payload && payload.queued) || [];
        var elsewhereRun = payload && payload.running_elsewhere;
        var windingDown = !!payload && payload.lane_state === 'winding_down';

        // Notice: a run finished while the user was busy elsewhere.
        toggle('queue-notice', !!noticeRun);
        if (noticeRun) {
            var name = noticeRun.label ? '"' + noticeRun.label + '"' : 'your run';
            setText('queue-notice-text',
                (noticeRun.status === 'completed' ? 'Finished: ' : 'Failed: ') + name);
            setText('queue-notice-action',
                noticeRun.status === 'completed' ? 'View results' : 'See details');
        }

        // The lane is busy with another tab's run.
        toggle('queue-running-elsewhere', !!elsewhereRun);
        if (elsewhereRun) {
            // Short on purpose: the full detail belongs to that tab's own panel.
            var where = 'Now running in ' + elsewhereRun.feature_label + ': "' + elsewhereRun.label + '"';
            if (elsewhereRun.country) where += ' (' + elsewhereRun.country + ')';
            setText('queue-running-elsewhere-text',
                where + ' · ' + (elsewhereRun.progress_percent || 0) + '%');
            var link = byId('queue-running-elsewhere-link');
            if (link) link.href = FEATURE_URLS[elsewhereRun.feature] || '#';
        }

        toggle('queue-winding-down', windingDown);

        // Up next.
        toggle('queue-header', queued.length > 0);
        setText('queue-count',
            queued.length === 1 ? '(1 run)' : '(' + queued.length + ' runs)');
        var list = byId('queue-list');
        if (list) list.innerHTML = queued.map(queueRowHtml).join('');

        section.classList.toggle(
            'hidden',
            !noticeRun && !elsewhereRun && !windingDown && queued.length === 0
        );
    }

    function syncStartButton() {
        var btn = byId('start-btn');
        if (btn) btn.textContent = busy ? cfg.queueLabel : cfg.startLabel;
        toggle('queue-hint', busy);
    }

    // --- finished runs ------------------------------------------------------

    function handleFinished(previous) {
        if (!previous || handledIds[previous.id]) return;
        handledIds[previous.id] = true;
        fetch(cfg.progressUrl(previous.id))
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (data) {
                // A 404 means the row was deleted - nothing to report.
                if (!data) return;
                if (data.status !== 'completed' && data.status !== 'failed') return;
                var run = {
                    id: previous.id,
                    status: data.status,
                    error: data.error,
                    label: previous.label
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
        return fetch(cfg.statusUrl)
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
                var btn = event.target.closest('.queue-remove-btn');
                if (!btn) return;
                removeQueued(btn.dataset.feature, btn.dataset.id);
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
        syncStartButton: syncStartButton,
        removeQueued: removeQueued,
        clearQueue: clearQueue
    };
})();
