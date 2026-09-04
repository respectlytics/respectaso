/**
 * SearchJob - keyword searches as background jobs (aso/search_jobs.py).
 *
 * On the Keyword Research tab it owns the live counter under the keyword
 * field, the status panel for the search that is running, paused or queued
 * (#search-job-panel), the results of the newest finished search
 * (#results-container) and the polling that keeps both current. On every
 * other page it draws the fixed-bottom strip (#search-job-strip).
 *
 * The server decides every state and every sentence that depends on data;
 * this file only lays them out. Load static/js/clipboard.js before it on
 * the dashboard (the "Copy the remaining keywords" button).
 *
 * Dashboard:  SearchJob.init({currentUrl, resultsUrl, pauseUrl, resumeUrl,
 *                 discardUrl, retryFailedUrl, dismissUrl, queueRunNowUrl,
 *                 queueMoveUrl, csrfToken, isPro, isNative, bootstrap,
 *                 renderResults, onProgress, onFinished})
 *             URL templates carry a 0 where the job id goes.
 * Other pages: SearchJob.initStrip({currentUrl, job})
 */
(function () {
    'use strict';

    var POLL_MS = 3000;            // while a search runs or waits
    var PAUSED_POLL_MS = 15000;    // while it is paused (another window may resume it)
    var STRIP_POLL_MS = 5000;
    var HISTORY_REFRESH_MS = 15000;
    var QUEUED_NOTE_MS = 8000;

    var cfg = null;
    var timer = null;
    var state = {job: null, finished: null, others: []};
    var renderedFinishedId = null;
    var lastDoneCount = null;
    var historyDirty = false;
    var lastHistoryRefresh = 0;
    var noteTimer = null;
    var counterEls = null;

    // --- helpers ------------------------------------------------------------

    function esc(value) {
        var text = value === null || value === undefined ? '' : String(value);
        if (window.escapeHtml) return window.escapeHtml(text);
        var div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    function fmt(n) {
        return Number(n || 0).toLocaleString('en-US');
    }

    function plural(n, word) {
        return fmt(n) + ' ' + word + (Number(n) === 1 ? '' : 's');
    }

    function durationText(seconds) {
        var minutes = Math.round(seconds / 60);
        if (minutes < 1) return 'less than a minute';
        if (minutes < 60) return 'about ' + minutes + ' min';
        var hours = Math.floor(minutes / 60);
        minutes = minutes % 60;
        return 'about ' + hours + ' h' + (minutes ? ' ' + minutes + ' min' : '');
    }

    function etaText(seconds) {
        return durationText(seconds) + ' left';
    }

    function byId(id) {
        return document.getElementById(id);
    }

    function setText(id, text) {
        var el = byId(id);
        if (el) el.textContent = text || '';
    }

    function toggle(id, visible) {
        var el = byId(id);
        if (el) el.classList.toggle('hidden', !visible);
    }

    function urlFor(template, id) {
        return template.replace('/0/', '/' + id + '/');
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
        }).then(function (r) {
            return r.json().then(function (data) {
                if (!r.ok) throw new Error(data.error || 'Request failed');
                return data;
            });
        });
    }

    // Mirrors aso.search_jobs.parse_keywords: commas and newlines split,
    // whitespace collapses, duplicates (case-insensitive) keep the first.
    function parseKeywords(raw) {
        var seen = {};
        var out = [];
        String(raw || '').replace(/\r/g, '\n').replace(/,/g, '\n').split('\n').forEach(function (chunk) {
            var text = chunk.trim().split(/\s+/).join(' ');
            if (!text) return;
            var key = text.toLowerCase();
            if (seen[key]) return;
            seen[key] = true;
            out.push(text);
        });
        return out;
    }

    function countKeywords(raw) {
        return parseKeywords(raw).length;
    }

    // --- the keyword field: counter, limit, auto-grow -----------------------

    var MAX_FIELD_LINES = 5;   // the field grows this far, then scrolls inside

    // One line by default; grows with the content (a pasted list) up to
    // MAX_FIELD_LINES, then the user scrolls inside the field.
    function autogrow(el) {
        if (!el.value) {
            // Empty: the one-line height from rows="1". (A wrapped placeholder
            // would otherwise count as content on a narrow screen.)
            el.style.height = '';
            el.style.overflowY = 'hidden';
            return;
        }
        var style = getComputedStyle(el);
        var lineHeight = parseFloat(style.lineHeight) || 20;
        var border = (parseFloat(style.borderTopWidth) || 0) + (parseFloat(style.borderBottomWidth) || 0);
        var padding = (parseFloat(style.paddingTop) || 0) + (parseFloat(style.paddingBottom) || 0);
        var max = lineHeight * MAX_FIELD_LINES + padding + border;
        el.style.height = 'auto';
        // scrollHeight excludes the border; the box (border-box) must include it.
        var wanted = Math.min(el.scrollHeight + border, max);
        el.style.height = wanted + 'px';
        el.style.overflowY = el.scrollHeight + border > max ? 'auto' : 'hidden';
    }

    function updateCounter() {
        if (!counterEls) return;
        var count = countKeywords(counterEls.field.value);
        var limit = cfg.limit;
        var over = count > limit;
        var html;
        if (count === 0) {
            html = cfg.isPro
                ? esc('Up to ' + fmt(limit) + ' keywords per search')
                : esc('Up to ' + limit + ' keywords per search') +
                  ' <span class="text-slate-600">·</span> <a href="' + esc(cfg.upgradeUrl) + '"' +
                  (cfg.upgradeNewTab ? ' target="_blank" rel="noopener"' : '') +
                  ' class="text-purple-300 hover:text-purple-200">Pro runs up to 1,000 per search</a>';
        } else if (cfg.isPro) {
            html = over
                ? esc(plural(count, 'keyword') + ' - a search holds up to ' + fmt(limit) + '. Start a second search for the rest.')
                : esc(plural(count, 'keyword')) + ' <span class="text-slate-600">· up to ' + fmt(limit) + ' per search</span>';
        } else {
            html = over
                ? esc(plural(count, 'keyword') + ' - the free version runs up to ' + limit + ' per search.')
                : esc(count + ' of ' + limit + ' keywords') +
                  ' <span class="text-slate-600">·</span> <a href="' + esc(cfg.upgradeUrl) + '"' +
                  (cfg.upgradeNewTab ? ' target="_blank" rel="noopener"' : '') +
                  ' class="text-purple-300 hover:text-purple-200">Pro runs up to 1,000 per search</a>';
        }
        counterEls.counter.innerHTML = html;
        counterEls.counter.classList.toggle('text-red-300', over);
        counterEls.counter.classList.toggle('text-slate-500', !over);
        if (counterEls.button) {
            counterEls.button.disabled = over;
            counterEls.button.classList.toggle('opacity-50', over);
            counterEls.button.classList.toggle('cursor-not-allowed', over);
        }
        if (!cfg.isPro) toggle('keyword-limit-nudge', over);
        return over;
    }

    function overLimit() {
        return counterEls ? countKeywords(counterEls.field.value) > cfg.limit : false;
    }

    function initField() {
        var field = byId('id_keywords');
        var counter = byId('keyword-counter');
        if (!field || !counter) return;
        counterEls = {field: field, counter: counter, button: byId('search-btn')};
        field.addEventListener('input', function () {
            autogrow(field);
            updateCounter();
            hideError();
        });
        // Enter searches (the habit every user has); Shift+Enter starts a new line.
        field.addEventListener('keydown', function (event) {
            if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault();
                var form = field.form;
                if (form && form.requestSubmit) form.requestSubmit();
                else if (form) form.dispatchEvent(new Event('submit', {cancelable: true}));
            }
        });
        autogrow(field);
        updateCounter();
    }

    function showError(data) {
        var box = byId('search-error');
        if (!box) return;
        box.textContent = (data && data.error) || 'Search failed';
        box.classList.remove('hidden');
        if (data && data.upgrade_url) toggle('keyword-limit-nudge', true);
    }

    function hideError() {
        toggle('search-error', false);
        if (!overLimit()) toggle('keyword-limit-nudge', false);
    }

    function showQueuedNote(data) {
        var note = byId('search-queued-note');
        if (!note || !data.queued_behind) return;
        var job = data.job;
        var text = 'Added to the queue' + (job.queue_position ? ' at position ' + job.queue_position : '') +
            '. It starts after ' + data.queued_behind + ' finishes' +
            (data.eta_seconds !== null && data.eta_seconds !== undefined ? ' (' + durationText(data.eta_seconds) + ')' : '') +
            (job.can_run_now ? ', or press Run now below.' : '.');
        note.textContent = text;
        note.classList.remove('hidden');
        clearTimeout(noteTimer);
        noteTimer = setTimeout(function () { note.classList.add('hidden'); }, QUEUED_NOTE_MS);
    }

    // --- the status panel ---------------------------------------------------

    function button(action, label, style, extra) {
        var cls = {
            primary: 'bg-purple-600 hover:bg-purple-700 text-white text-sm font-medium px-4 py-2 rounded-lg transition-colors',
            secondary: 'bg-slate-700/50 hover:bg-slate-700 text-slate-200 text-sm px-3 py-2 rounded-lg transition-colors',
            quiet: 'text-xs text-red-400 hover:text-red-300 px-2 py-2 transition-colors'
        }[style];
        return '<button type="button" data-action="' + action + '" class="' + cls + '"' + (extra || '') + '>' + esc(label) + '</button>';
    }

    function titleFor(job) {
        var at = 'Paused at ' + fmt(job.keywords_done) + ' of ' + fmt(job.total_keywords) + ' keywords';
        switch (job.status) {
            case 'queued':
                return 'Keyword search queued';
            case 'running':
                return 'Researching ' + plural(job.total_keywords, 'keyword') + ' in ' + job.countries_text;
            default:
                return (job.status === 'failed' || job.error_message) ? at + ' after an error' : at;
        }
    }

    function lineFor(job) {
        if (job.status === 'queued') {
            return job.waiting_for
                ? 'Waiting for ' + job.waiting_for + ' to finish. Your search starts automatically.'
                : 'Starting now...';
        }
        if (job.status === 'running') {
            if (job.eta_seconds !== null && job.eta_seconds !== undefined) {
                return fmt(job.keywords_done) + ' of ' + fmt(job.total_keywords) + ' keywords done · ' + etaText(job.eta_seconds);
            }
            return job.keywords_done > 0
                ? fmt(job.keywords_done) + ' of ' + fmt(job.total_keywords) + ' keywords done'
                : 'Starting...';
        }
        if (job.auto_resume) {
            return 'Letting ' + (job.yielded_for ? '"' + job.yielded_for + '"' : 'another run') + ' go first. This search resumes by itself right after.';
        }
        if (job.status === 'failed' || job.error_message) {
            return job.error_message || 'Something went wrong. Press Resume to continue from where it stopped.';
        }
        if (job.throttle_state === 'paused') {
            return job.progress_message;
        }
        return 'Resume when you are ready. Everything researched so far is already in your Search History, ' +
            'and this search stays here until you resume or discard it - quitting the app is fine.';
    }

    function actionsFor(job) {
        var html = '';
        if (job.status === 'queued') {
            if (cfg.isPro && job.can_run_now) html += button('run-now', 'Run now', 'primary');
            if (cfg.isPro && job.queue_position > 1) html += button('run-next', 'Run next', 'secondary');
            html += button('remove', 'Remove', 'quiet');
        } else if (job.status === 'running') {
            html += button('pause', 'Pause', 'secondary');
        } else {
            html += button(job.auto_resume ? 'resume-now' : 'resume', job.auto_resume ? 'Resume now' : 'Resume', 'primary');
            html += button('copy-remaining', 'Copy the ' + plural(job.remaining_count, 'remaining keyword'), 'secondary');
            html += button('discard', 'Discard the rest', 'quiet');
        }
        return html;
    }

    function tickerText(pair) {
        if (!pair) return '';
        var at = pair.lastIndexOf(' (');
        if (at === -1) return 'Checking "' + pair + '"';
        return 'Checking "' + pair.slice(0, at) + '"' + pair.slice(at);
    }

    function renderPanel(job) {
        var panel = byId('search-job-panel');
        if (!panel) return;
        if (!job) {
            panel.classList.add('hidden');
            return;
        }
        panel.classList.remove('hidden');
        var running = job.status === 'running';
        var queued = job.status === 'queued';
        toggle('sjp-spinner', running);
        toggle('sjp-clock-icon', queued);
        toggle('sjp-pause-icon', !running && !queued);
        setText('sjp-title', titleFor(job));
        setText('sjp-line', lineFor(job));
        var position = queued && job.queue_position > 1 ? 'Position ' + job.queue_position + ' in the queue' : '';
        toggle('sjp-position', !!position);
        setText('sjp-position', position);
        byId('sjp-actions').innerHTML = actionsFor(job);

        toggle('sjp-progress', running);
        if (running) {
            setText('sjp-ticker', tickerText(job.current_pair));
            setText('sjp-percent', (job.progress_percent || 0) + '%');
            byId('sjp-fill').style.width = (job.progress_percent || 0) + '%';
            var throttled = job.throttle_state && job.throttle_state !== 'normal';
            toggle('sjp-throttle', throttled);
            setText('sjp-throttle', throttled ? job.progress_message : '');
        }

        var pickedUp = running && job.restart_resumes > 0
            ? 'Picked up where it left off after RespectASO was closed.' : '';
        toggle('sjp-picked-up', !!pickedUp);
        setText('sjp-picked-up', pickedUp);

        var note = '';
        if (running) {
            note = cfg.isNative
                ? 'Feel free to use other tabs. The search runs while RespectASO is open; if you quit, it continues where it left off next time you open the app.'
                : 'Feel free to use other tabs. The search keeps running in the background.';
        }
        toggle('sjp-note', !!note);
        setText('sjp-note', note);
    }

    function renderOthers(others) {
        var list = byId('sjp-others-list');
        if (!list) return;
        toggle('sjp-others', !!(others && others.length && state.job));
        if (!others || !others.length) {
            list.innerHTML = '';
            return;
        }
        list.innerHTML = others.map(function (job) {
            var text = 'Paused at ' + fmt(job.keywords_done) + ' of ' + fmt(job.total_keywords) + ' keywords';
            if (job.error_message) text += ' after an error';
            text += ' · ' + job.country_codes;
            return '<div class="flex flex-col sm:flex-row sm:items-center gap-2 bg-slate-800/30 border border-white/5 rounded-lg px-3 py-2" data-id="' + job.id + '">' +
                '<span class="text-sm text-slate-300 flex-1 min-w-0 truncate">' + esc(text) + '</span>' +
                '<div class="flex items-center gap-2 shrink-0">' +
                    '<button type="button" data-action="resume" class="text-xs px-2.5 py-1 rounded-full border border-purple-500/30 text-purple-300 hover:bg-purple-600/20 transition-colors">Resume</button>' +
                    '<button type="button" data-action="discard" class="text-xs text-red-400 hover:text-red-300 px-1 py-1 transition-colors">Discard the rest</button>' +
                '</div>' +
            '</div>';
        }).join('');
    }

    // --- the results panel --------------------------------------------------

    function clearResults() {
        var container = byId('results-container');
        if (!container) return;
        container.classList.add('hidden');
        container.innerHTML = '';
    }

    function loadFinished(id) {
        fetch(urlFor(cfg.resultsUrl, id))
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (data) {
                if (!data || !data.job || renderedFinishedId !== data.job.id) return;
                var container = byId('results-container');
                if (!container) return;
                container.innerHTML = '';
                cfg.renderResults(container, data.job);
                container.classList.remove('hidden');
                if (cfg.onFinished) cfg.onFinished(data.job);
            })
            .catch(function () { /* the poll will try again */ });
    }

    // --- actions ------------------------------------------------------------

    function confirmDiscard(job) {
        var remaining = job.remaining_count;
        var done = job.keywords_done;
        var message = 'Discard the ' + plural(remaining, 'keyword') + ' that were not searched yet? ' +
            'The ' + fmt(done) + ' already researched stay in your Search History. ' +
            'If you want to keep the list, copy the remaining keywords from the panel first.';
        return window.showConfirm(message, {
            title: 'Discard the rest',
            confirmLabel: 'Discard',
            cancelLabel: 'Keep the search',
            confirmStyle: 'danger'
        });
    }

    function confirmRemove(job) {
        if (job.keywords_done > 0) return Promise.resolve(true);   // it pauses; nothing is lost
        return window.showConfirm(
            'Remove the queued search? Its ' + plural(job.total_keywords, 'keyword') + ' will not be researched.',
            {title: 'Remove from queue', confirmLabel: 'Remove', cancelLabel: 'Keep', confirmStyle: 'danger'}
        );
    }

    function act(action, job, btn) {
        var id = job.id;
        var done;
        switch (action) {
            case 'pause':
                done = post(urlFor(cfg.pauseUrl, id));
                break;
            case 'resume':
                done = post(urlFor(cfg.resumeUrl, id));
                break;
            case 'resume-now':
                done = post(urlFor(cfg.resumeUrl, id), {now: 1});
                break;
            case 'discard':
                done = confirmDiscard(job).then(function (ok) {
                    if (ok) return post(urlFor(cfg.discardUrl, id));
                });
                break;
            case 'remove':
                done = confirmRemove(job).then(function (ok) {
                    if (ok) return post(urlFor(cfg.discardUrl, id));
                });
                break;
            case 'run-now':
                done = post(cfg.queueRunNowUrl, {feature: 'keyword_search', session_id: id});
                break;
            case 'run-next':
                done = post(cfg.queueMoveUrl, {feature: 'keyword_search', session_id: id, direction: 'top'});
                break;
            case 'retry-failed':
                done = post(urlFor(cfg.retryFailedUrl, id)).then(function (data) {
                    clearResults();
                    renderedFinishedId = null;
                    started({job: data.job});
                });
                break;
            case 'dismiss':
                clearResults();
                renderedFinishedId = null;
                state.finished = null;
                done = post(urlFor(cfg.dismissUrl, id));
                break;
            case 'copy-remaining':
                var text = (job.remaining_keywords || []).join('\n');
                if (!text) {
                    fetch(urlFor(cfg.resultsUrl, id))
                        .then(function (r) { return r.json(); })
                        .then(function (data) {
                            job.remaining_keywords = data.job.remaining_keywords;
                            act('copy-remaining', job, btn);
                        });
                    return;
                }
                copyTextToClipboard(text).then(function () {
                    showCopyToast(btn, 'Copied', true);
                }).catch(function () {
                    showCopyToast(btn, 'Copy failed', false);
                });
                return;
            default:
                return;
        }
        if (btn) btn.disabled = true;
        done.then(refresh).catch(function (err) {
            if (window.showAlert) showAlert(err.message || 'Something went wrong.', {title: 'Keyword search'});
        }).then(function () { if (btn) btn.disabled = false; });
    }

    function bindActions() {
        var panel = byId('search-job-panel');
        if (panel) {
            panel.addEventListener('click', function (event) {
                var btn = event.target.closest('button[data-action]');
                if (!btn) return;
                var row = btn.closest('[data-id]');
                var job = row ? findOther(row.dataset.id) : state.job;
                if (job) act(btn.dataset.action, job, btn);
            });
        }
        var results = byId('results-container');
        if (results) {
            results.addEventListener('click', function (event) {
                var btn = event.target.closest('button[data-action]');
                if (!btn || !state.finished) return;
                act(btn.dataset.action, state.finished, btn);
            });
        }
        document.addEventListener('keydown', function (event) {
            if (event.key === 'Escape' && state.finished && renderedFinishedId !== null) {
                act('dismiss', state.finished, null);
            }
        });
    }

    function findOther(id) {
        for (var i = 0; i < state.others.length; i++) {
            if (String(state.others[i].id) === String(id)) return state.others[i];
        }
        return null;
    }

    // --- polling ------------------------------------------------------------

    function apply(data) {
        var previous = state.job;
        state = {job: data.job || null, finished: data.finished || null, others: data.others || []};
        renderPanel(state.job);
        renderOthers(state.others);

        if (state.finished) {
            if (renderedFinishedId !== state.finished.id) {
                renderedFinishedId = state.finished.id;
                loadFinished(state.finished.id);
            }
        } else if (renderedFinishedId !== null) {
            renderedFinishedId = null;
            clearResults();
        }

        // Search History streams the results in: at most every 15 s while the
        // done count changed, and once when the search ends.
        if (state.job) {
            if (lastDoneCount !== null && state.job.done_count !== lastDoneCount) historyDirty = true;
            lastDoneCount = state.job.done_count;
        } else {
            if (previous) historyDirty = true;
            lastDoneCount = null;
        }
        if (historyDirty && (!state.job || Date.now() - lastHistoryRefresh > HISTORY_REFRESH_MS)) {
            historyDirty = false;
            lastHistoryRefresh = Date.now();
            if (cfg.onProgress) cfg.onProgress();
        }
        schedule();
    }

    function schedule() {
        clearTimeout(timer);
        timer = null;
        if (!state.job) return;
        var fast = state.job.status === 'running' || state.job.status === 'queued' || state.job.auto_resume;
        timer = setTimeout(refresh, fast ? POLL_MS : PAUSED_POLL_MS);
    }

    function refresh() {
        return fetch(cfg.currentUrl)
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (data) { if (data) apply(data); else schedule(); })
            .catch(schedule);
    }

    function started(data) {
        // The answer to a submit: show the new job at once, then keep polling.
        showQueuedNote(data);
        apply({job: data.job, finished: state.finished, others: state.others});
        refresh();
    }

    function init(options) {
        cfg = options;
        initField();
        bindActions();
        apply(cfg.bootstrap || {});
    }

    // --- the strip (every page but the dashboard) ---------------------------

    var stripCfg = null;
    var stripTimer = null;

    function stripActive(job) {
        return job && (job.status === 'running' || job.status === 'queued' || job.auto_resume);
    }

    function renderStrip(job) {
        var strip = byId('search-job-strip');
        if (!strip) return;
        if (!job) {
            strip.classList.add('hidden');
            return;
        }
        var text = '';
        var link = 'Open';
        var running = job.status === 'running';
        switch (job.status) {
            case 'running':
                text = 'Keyword research running: ' + fmt(job.keywords_done) + ' of ' + fmt(job.total_keywords) + ' keywords';
                break;
            case 'queued':
                text = 'Keyword research queued: ' + plural(job.total_keywords, 'keyword');
                break;
            case 'completed':
                text = 'Keyword research finished: ' + plural(job.total_keywords, 'keyword');
                link = 'See results';
                break;
            case 'cancelled':
                text = 'Keyword research stopped at ' + fmt(job.keywords_done) + ' of ' + fmt(job.total_keywords) + ' keywords';
                link = 'See results';
                break;
            default:
                text = 'Keyword research paused at ' + fmt(job.keywords_done) + ' of ' + fmt(job.total_keywords) + ' keywords';
                link = job.auto_resume ? 'Open' : 'Resume';
        }
        setText('sjs-text', text);
        setText('sjs-link', link);
        toggle('sjs-spinner', running || job.status === 'queued');
        toggle('sjs-pause-icon', job.status === 'paused' || job.status === 'failed');
        toggle('sjs-done-icon', job.status === 'completed' || job.status === 'cancelled');
        toggle('sjs-bar', running);
        if (running) byId('sjs-fill').style.width = (job.progress_percent || 0) + '%';
        strip.classList.remove('hidden');
    }

    function stripRefresh() {
        fetch(stripCfg.currentUrl)
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (data) {
                if (!data) return;
                var job = data.job || data.finished || null;
                renderStrip(job);
                if (stripActive(job)) stripTimer = setTimeout(stripRefresh, STRIP_POLL_MS);
            })
            .catch(function () { stripTimer = setTimeout(stripRefresh, STRIP_POLL_MS); });
    }

    function initStrip(options) {
        stripCfg = options;
        renderStrip(options.job);
        if (stripActive(options.job)) stripTimer = setTimeout(stripRefresh, STRIP_POLL_MS);
    }

    window.SearchJob = {
        init: init,
        initStrip: initStrip,
        started: started,
        refresh: refresh,
        showError: showError,
        hideError: hideError,
        overLimit: overLimit,
        parseKeywords: parseKeywords,
        countKeywords: countKeywords,
        fmt: fmt,
        plural: plural,
        esc: esc
    };
})();
