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
 *                 renderResults, onFinished})
 *             URL templates carry a 0 where the job id goes.
 *             Search History follows the search by itself: the Dashboard
 *             fetches its sections whenever the server's change count moves
 *             (aso/history_revision.py).
 * The global bottom strip lives in job-strip.js: it shows either job type.
 */
(function () {
    'use strict';

    var POLL_MS = 3000;            // while a search runs or waits
    var PAUSED_POLL_MS = 15000;    // while it is paused (another window may resume it)

    var cfg = null;
    var timer = null;
    var state = {job: null, finished: null, others: []};
    var renderedFinishedId = null;
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

    // A wait in words, from the one copy every page loads first
    // (static/js/country-picker.js), so the Dashboard and the Opportunity
    // page word a wait the same way.
    function durationText(seconds) {
        return window.CountryPicker.durationText(seconds);
    }

    function etaText(seconds) {
        var text = durationText(seconds);
        return text ? text + ' left' : '';
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
        // The free edition's one Pro line (UI_REDESIGN_PLAN.md 11.4).
        // The Pro button looks the same everywhere (btn-pro, aso/tests/test_pro_button_style.py).
        var pro = ' <a href="' + esc(cfg.upgradeUrl) + '" target="_blank" rel="noopener"' +
                  ' class="btn-pro ml-2 px-3 py-1 text-xs">' + esc(cfg.upgradeLabel || 'Get Pro') + '</a>';
        var html;
        if (cfg.isPro) {
            if (count === 0) html = '';
            else html = over
                ? esc(plural(count, 'keyword') + ': a search holds up to ' + fmt(limit) + '. Start a second search for the rest.')
                : esc(plural(count, 'keyword'));
        } else if (over) {
            html = esc(plural(count, 'keyword') + ': the free version checks ' + limit + ' at a time.') + pro;
        } else {
            html = esc((count ? count + ' of ' + limit + ' keywords. ' : limit + ' keywords at a time. ') +
                       'Pro checks up to 1,000.') + pro;
        }
        counterEls.counter.innerHTML = html;
        counterEls.counter.classList.toggle('text-red-300', over);
        counterEls.counter.classList.toggle('text-slate-300', !over);
        if (counterEls.button) {
            counterEls.button.disabled = over;
            counterEls.button.classList.toggle('opacity-50', over);
            counterEls.button.classList.toggle('cursor-not-allowed', over);
        }
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
    }

    function hideError() {
        toggle('search-error', false);
    }

    // The note that flies into the Activity pill (activity-indicator.js):
    // what was started, and when it starts.
    function announceStart(data) {
        if (!window.ActivityIndicator || !ActivityIndicator.announce || !data || !data.job) return;
        var job = data.job;
        ActivityIndicator.announce({
            queued: !!data.queued_behind,
            label: plural(job.total_keywords, 'keyword') + ' in ' + (job.countries_text || ''),
            current: data.queued_behind || '',
            eta: data.eta_seconds || null
        });
    }

    // --- the status panel ---------------------------------------------------

    function button(action, label, style, extra) {
        var cls = {
            primary: 'btn-primary px-3 py-1.5',
            secondary: 'btn-quiet',
            quiet: 'link-danger text-sm px-2 py-1'
        }[style];
        return '<button type="button" data-action="' + action + '" class="' + cls + '"' + (extra || '') + '>' + esc(label) + '</button>';
    }

    function titleFor(job) {
        var at = 'Paused at ' + fmt(job.keywords_done) + ' of ' + fmt(job.total_keywords) + ' keywords';
        switch (job.status) {
            case 'queued':
                return 'Waiting to start';
            case 'running':
                return 'Checking ' + plural(job.total_keywords, 'keyword') + ' in ' + job.countries_text;
            default:
                return at;
        }
    }

    function lineFor(job) {
        if (job.status === 'queued') {
            return job.waiting_for ? 'Starts after ' + job.waiting_for + '.' : 'Starts next.';
        }
        if (job.status === 'running') {
            if (job.keywords_done > 0 && etaText(job.eta_seconds)) {
                return fmt(job.keywords_done) + ' of ' + fmt(job.total_keywords) + ' · ' + etaText(job.eta_seconds);
            }
            return job.keywords_done > 0
                ? fmt(job.keywords_done) + ' of ' + fmt(job.total_keywords)
                : 'Starting…';
        }
        if (job.auto_resume) {
            return (job.yielded_for ? '"' + job.yielded_for + '"' : 'Another run') + ' goes first. This search carries on right after.';
        }
        if (job.status === 'failed' || job.error_message) {
            return job.error_message || 'Something went wrong. Press Resume to carry on.';
        }
        if (job.throttle_state === 'paused') {
            return job.progress_message;
        }
        return 'Everything checked so far is in your table.';
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
        if (!running) { toggle('sjp-percent', false); toggle('sjp-throttle', false); toggle('sjp-line', true); }
        if (running) {
            setText('sjp-percent', (job.progress_percent || 0) + '%');
            toggle('sjp-percent', true);
            byId('sjp-fill').style.width = (job.progress_percent || 0) + '%';
            var throttled = job.throttle_state && job.throttle_state !== 'normal';
            toggle('sjp-throttle', throttled);
            toggle('sjp-line', !throttled);
            setText('sjp-throttle', throttled ? job.progress_message : '');
        }

        var pickedUp = running && job.restart_resumes > 0
            ? 'Picked up where it left off after RespectASO was closed.' : '';
        toggle('sjp-picked-up', !!pickedUp);
        setText('sjp-picked-up', pickedUp);

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
                    '<button type="button" data-action="discard" class="link-danger text-xs px-1 py-1">Discard the rest</button>' +
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
        var message = 'Discard the ' + plural(remaining, 'keyword') + ' not searched yet? ' +
            'The ' + fmt(done) + ' already checked stay in your tracked keywords.';
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
        announceStart(data);
        apply({job: data.job, finished: state.finished, others: state.others});
        refresh();
    }

    function init(options) {
        cfg = options;
        initField();
        bindActions();
        apply(cfg.bootstrap || {});
    }

    window.SearchJob = {
        init: init,
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
