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
        keyword_search: 'Keyword Research'
    };

    // Complete class strings - Tailwind only extracts whole literals.
    var FEATURE_BADGE_CLASSES = {
        researcher: 'bg-purple-900/30 text-purple-300',
        competitor: 'bg-amber-900/30 text-amber-300',
        simulator: 'bg-sky-900/30 text-sky-300',
        keyword_search: 'bg-teal-900/30 text-teal-300'
    };

    var ICON_UP = '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 15l7-7 7 7"/></svg>';
    var ICON_DOWN = '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/></svg>';
    var ICON_REMOVE = '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>';

    var cfg = null;
    var timer = null;
    var payload = null;          // the latest queue status
    var busy = false;            // anything running or queued, anywhere
    var lastRun = null;          // {id, label} of the run this tab last showed
    var handledIds = {};         // runs whose outcome has already been surfaced
    var tickerRunId = null;      // run id the progress ticker was started for
    var cancelBtnHtml = null;    // pristine Cancel button markup
    var cancelling = false;
    var noticeRun = null;        // the finished run shown in the queue notice
    var lastSignature = null;    // running + queued ids, to fire onChanged

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
    function displayLabel(item) {
        if (item.quote_label === false) return item.label || '';
        return '"' + (item.label || '') + '"';
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
        return '<button type="button" class="' + cls + ' shrink-0 inline-flex items-center justify-center rounded-md border border-white/5 text-slate-400 hover:text-white hover:border-white/20 disabled:opacity-30 disabled:hover:text-slate-400 disabled:hover:border-white/5 transition-colors p-1.5"' +
            ' title="' + title + '" aria-label="' + title + '"' + (disabled ? ' disabled' : '') + '>' + inner + '</button>';
    }

    function pillHtml(cls, text, title) {
        return '<button type="button" class="' + cls + ' shrink-0 text-xs px-2.5 py-1 rounded-full border border-purple-500/30 text-purple-300 hover:bg-purple-600/20 transition-colors"' +
            ' title="' + title + '">' + text + '</button>';
    }

    function queueRowHtml(item, index, count) {
        var badgeClass = FEATURE_BADGE_CLASSES[item.feature] || 'bg-slate-700/30 text-slate-300';
        var badgeLabel = FEATURE_BADGE_LABELS[item.feature] || item.feature_label;
        var label = item.is_refinement
            ? 'Refinement of "' + (item.label || '') + '"'
            : (item.label || '');
        var data = ' data-feature="' + esc(item.feature) + '" data-id="' + item.id + '"';
        var controls = '' +
            controlHtml('queue-move-up', 'Move up', ICON_UP, index === 0) +
            controlHtml('queue-move-down', 'Move down', ICON_DOWN, index === count - 1) +
            (index > 0 ? pillHtml('queue-run-next', 'Run next', 'Put this run first in line') : '') +
            (item.can_run_now ? pillHtml('queue-run-now', 'Run now', 'Start this run now; the running keyword search pauses and resumes right after') : '') +
            controlHtml('queue-remove-btn text-slate-600 hover:text-red-400', 'Remove from queue', ICON_REMOVE, false);
        return '' +
            '<div class="bg-slate-800/30 border border-white/5 rounded-lg px-4 py-3 flex flex-col sm:flex-row sm:items-center gap-2 sm:gap-3"' + data + '>' +
                '<div class="flex items-center gap-3 min-w-0 flex-1">' +
                    '<span class="text-xs text-slate-500 font-mono tabular-nums w-4 shrink-0">' + item.position + '</span>' +
                    '<span class="text-xs px-2 py-0.5 rounded-full whitespace-nowrap shrink-0 ' + badgeClass + '">' + esc(badgeLabel) + '</span>' +
                    '<div class="min-w-0 flex-1">' +
                        '<div class="text-sm text-white truncate">' + esc(label) + '</div>' +
                        '<div class="text-xs text-slate-500 truncate">' + esc(item.detail) + '</div>' +
                    '</div>' +
                '</div>' +
                '<div class="flex items-center gap-1.5 pl-7 sm:pl-0 shrink-0">' + controls + '</div>' +
            '</div>';
    }

    function renderQueue() {
        var section = byId('queue-section');
        if (!section) return;
        var queued = (payload && payload.queued) || [];
        var elsewhereRun = payload && payload.running_elsewhere;
        var windingDown = !!payload && payload.lane_state === 'winding_down';
        var busyWith = payload && payload.busy_with;

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
            var where = 'Now running in ' + elsewhereRun.feature_label + ': ' + displayLabel(elsewhereRun);
            if (elsewhereRun.country) where += ' (' + elsewhereRun.country + ')';
            setText('queue-running-elsewhere-text',
                where + ' · ' + (elsewhereRun.progress_percent || 0) + '%');
            var link = byId('queue-running-elsewhere-link');
            if (link) link.href = elsewhereRun.url || '#';
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
            !noticeRun && !elsewhereRun && !waitingText && queued.length === 0
        );
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
        moveQueued: moveQueued,
        runNow: runNow,
        clearQueue: clearQueue
    };
})();
