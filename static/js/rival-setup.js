/**
 * The Rival Tracker setup screen (aso_pro/templates/aso_pro/rival_tracker/setup.html).
 *
 * One screen: the app and its countries, keywords per country, rivals and the
 * weekly summary. Every change is saved as it is made (rival_setup_save_view),
 * into a draft until Start following, so leaving the page, switching tabs or
 * starting Suggest keywords never loses a choice; the server checks every rule
 * again (aso_pro/rivals/store.save_setup) and its message is shown as it is.
 * Suggest keywords opens a window with the app's title, subtitle, keyword
 * field and phrase lengths; the run is part of the shared queue: this file
 * starts it, polls it and adds its preselected keywords as chips.
 */
(function () {
    'use strict';

    var root = document.getElementById('rival-setup');
    if (!root) return;
    var state = JSON.parse(document.getElementById('rival-setup-data').textContent);
    var csrf = root.dataset.csrf;
    var MAX_RIVALS = parseInt(root.dataset.maxRivals, 10);
    var SECONDS = parseInt(root.dataset.secondsPerCall, 10);
    var AI = root.dataset.ai === '1';
    var more = {};           // country -> suggestions not added
    var reasons = {};        // country -> {keyword: why it was suggested}
    var job = null;          // the suggestion being polled
    var searchTimer = null, ideasTimer = null;

    state.active = state.countries[0] || '';

    function el(id) { return document.getElementById(id); }
    function esc(text) {
        return String(text == null ? '' : text).replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }
    function countryName(code) { return window.CountryPicker ? CountryPicker.name(code) : code.toUpperCase(); }
    function storeName(code) { return CountryPicker.storeName(code); }
    function plural(n, word) { return n + ' ' + word + (n === 1 ? '' : 's'); }
    function send(url, body, method) {
        return fetch(url, {
            method: method || 'POST',
            headers: {'X-CSRFToken': csrf, 'Content-Type': 'application/json'},
            body: body === undefined ? undefined : JSON.stringify(body),
        }).then(function (resp) {
            return resp.json().catch(function () { return {}; }).then(function (data) { return {ok: resp.ok, data: data}; });
        });
    }
    function list(code) {
        if (!state.keywords[code]) state.keywords[code] = [];
        return state.keywords[code];
    }
    function has(code, text) {
        return list(code).some(function (k) { return k.text === text; });
    }
    function openWindow(box) { box.classList.remove('hidden'); box.classList.add('flex'); }
    function closeWindow(box) { box.classList.add('hidden'); box.classList.remove('flex'); }

    // ---- saving as it changes ----------------------------------------------------
    // Each change goes to the server at once (text fields after a short pause),
    // one request at a time; a change made while one is on its way goes next.

    var saveTimer = null, saving = false, dirty = false, saveFailed = false;

    function lengthsValue() {
        var input = el('keyword_lengths');
        return input ? input.value : (state.keyword_lengths || '1,2,3');
    }
    function fieldValue(id, fallback) {
        var input = el(id);
        return input ? input.value : (fallback || '');
    }
    function setupBody(start) {
        var keywords = {};
        state.countries.forEach(function (code) { keywords[code] = list(code).map(function (k) { return k.text; }); });
        return {
            app_id: state.app_id, countries: state.countries, keywords: keywords, rivals: state.rivals,
            subtitle: fieldValue('rival-subtitle', state.subtitle),
            keyword_field: fieldValue('rival-keyword-field', state.keyword_field),
            keyword_lengths: lengthsValue(), weekly_summary: el('rival-weekly').checked, start: !!start,
        };
    }

    var SAVED_ICON = '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">' +
        '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M5 13l4 4L19 7"></path></svg>';

    function showSaved(status, message) {
        var box = el('rival-saved');
        box.classList.remove('text-slate-400', 'text-emerald-300', 'text-red-300');
        if (status === 'saving') {
            box.classList.add('text-slate-400');
            box.textContent = 'Saving';
        } else if (status === 'saved') {
            box.classList.add('text-emerald-300');
            box.innerHTML = SAVED_ICON + 'Saved';
        } else if (status === 'error') {
            box.classList.add('text-red-300');
            box.textContent = 'Not saved: ' + (message || 'that did not go through. Try again.');
        }
    }

    function flush() {
        clearTimeout(saveTimer);
        if (saving || !dirty) return;
        dirty = false;
        saving = true;
        showSaved('saving');
        send(root.dataset.saveUrl, setupBody(false)).then(function (res) {
            saving = false;
            saveFailed = !res.ok;
            if (!res.ok) {
                showSaved('error', res.data.error);
            } else if (!dirty) {
                showSaved('saved');
            }
            if (dirty) flush();
        }).catch(function () {
            saving = false;
            saveFailed = true;
            dirty = true;
            showSaved('error');
        });
    }

    function changed(wait) {
        dirty = true;
        clearTimeout(saveTimer);
        if (wait) {
            showSaved('saving');
            saveTimer = setTimeout(flush, wait);
        } else {
            flush();
        }
    }

    // Leaving the page with a change still waiting: send it on its way.
    function flushOnLeave() {
        if (!dirty) return;
        dirty = false;
        try {
            fetch(root.dataset.saveUrl, {
                method: 'POST', keepalive: true,
                headers: {'X-CSRFToken': csrf, 'Content-Type': 'application/json'},
                body: JSON.stringify(setupBody(false)),
            });
        } catch (e) { /* the page is going; nothing else to do */ }
    }
    window.addEventListener('pagehide', flushOnLeave);
    document.addEventListener('visibilitychange', function () {
        if (document.visibilityState === 'hidden') flushOnLeave();
    });

    // ---- countries and tabs ---------------------------------------------------

    function renderTabs() {
        var tabs = el('rival-country-tabs');
        if (state.countries.indexOf(state.active) === -1) state.active = state.countries[0] || '';
        tabs.innerHTML = state.countries.map(function (code) {
            var on = code === state.active;
            return '<button type="button" role="tab" aria-selected="' + on + '" data-country="' + code + '" class="text-sm px-3 py-1.5 rounded-lg border transition-colors ' +
                (on ? 'bg-purple-600/20 text-purple-200 border-purple-500/30' : 'text-slate-400 hover:text-white hover:bg-white/5 border-transparent') + '">' +
                esc(CountryPicker.label(code)) + ' <span class="text-xs text-slate-400">' + list(code).length + '</span></button>';
        }).join('') || '<span class="text-sm text-slate-400">Pick a country above to add keywords.</span>';
        el('rival-copy-countries').classList.toggle('hidden', state.countries.length < 2 || !list(state.active).length);
        el('rival-suggest').disabled = !state.active || !!job;
        el('rival-open-history').disabled = !state.active;
        el('rival-keyword-input').disabled = !state.active;
    }

    // ---- keywords -------------------------------------------------------------

    function chip(k, index) {
        var mark = k.origin === 'suggested' ? '<span class="text-purple-300" aria-hidden="true">✦</span>' : '';
        return '<span class="inline-flex items-center gap-1.5 text-xs bg-slate-800 border border-white/10 rounded-full pl-3 pr-1 py-1 text-slate-200"' +
            (k.reason ? ' data-tip="' + esc(k.reason) + '"' : '') + '>' + mark + esc(k.text) +
            '<button type="button" class="kw-remove w-5 h-5 rounded-full text-slate-400 hover:text-white hover:bg-white/10" data-index="' + index + '" aria-label="Remove ' + esc(k.text) + '">×</button></span>';
    }

    function renderKeywords() {
        var code = state.active;
        var items = code ? list(code) : [];
        // An empty list shows only the box's placeholder, which says what to do.
        var chips = el('rival-keyword-chips');
        chips.innerHTML = items.map(chip).join('');
        chips.classList.toggle('hidden', !items.length);
        var note = el('rival-keyword-note');
        if (state.prefilled && code === state.countries[0] && items.length) {
            note.textContent = 'Prefilled with ' + state.app_short + '’s most searched tracked keywords.';
            note.classList.remove('hidden');
        } else {
            note.classList.add('hidden');
        }
        renderMore();
        renderTabs();
        renderEstimate();
    }

    function addKeywords(texts, origin, why, code) {
        code = code || state.active;
        if (!code) return 0;
        var added = 0;
        texts.forEach(function (raw, i) {
            var text = String(raw || '').replace(/\s+/g, ' ').trim().toLowerCase();
            if (!text || has(code, text)) return;
            list(code).push({text: text, origin: origin, reason: (why && why[i]) || ''});
            added += 1;
        });
        state.prefilled = state.prefilled && origin === 'history';
        renderKeywords();
        scheduleIdeas();
        if (added) changed();
        return added;
    }

    el('rival-keyword-input').addEventListener('keydown', function (e) {
        if (e.key !== 'Enter') return;
        e.preventDefault();
        addKeywords(e.target.value.split(/[,\n]/), 'manual');
        e.target.value = '';
    });
    el('rival-keyword-input').addEventListener('paste', function (e) {
        var text = (e.clipboardData || window.clipboardData).getData('text');
        if (text.indexOf(',') === -1 && text.indexOf('\n') === -1) return;
        e.preventDefault();
        addKeywords(text.split(/[,\n]/), 'manual');
    });
    el('rival-copy-countries').addEventListener('click', function () {
        var source = list(state.active);
        state.countries.forEach(function (code) {
            if (code === state.active) return;
            source.forEach(function (k) { if (!has(code, k.text)) list(code).push({text: k.text, origin: k.origin, reason: k.reason}); });
        });
        renderKeywords();
        changed();
    });

    // ---- Suggest keywords -------------------------------------------------------

    // The last finished suggestion of each country (kept on the server): its
    // marks on the chips and the suggestions not added survive a reload.
    Object.keys(state.suggested || {}).forEach(function (code) {
        var candidates = state.suggested[code] || [];
        reasons[code] = {};
        candidates.forEach(function (c) { reasons[code][c.keyword] = c.reason; });
        more[code] = candidates.filter(function (c) { return !c.preselected; });
        list(code).forEach(function (k) {
            if (reasons[code][k.text] !== undefined) { k.origin = 'suggested'; k.reason = reasons[code][k.text]; }
        });
    });

    function renderMore() {
        var rest = (more[state.active] || []).filter(function (c) { return !has(state.active, c.keyword); });
        el('rival-more-suggestions').classList.toggle('hidden', !rest.length);
        el('rival-more-toggle').textContent = 'Show all ' + rest.length + ' other suggestion' + (rest.length === 1 ? '' : 's');
        el('rival-more-list').innerHTML = rest.map(function (c) {
            return '<button type="button" class="kw-more text-xs border border-dashed border-white/20 text-slate-300 hover:text-white hover:border-white/40 rounded-full px-3 py-1" data-keyword="' + esc(c.keyword) + '" data-tip="' + esc(c.reason) + '">+ ' + esc(c.keyword) + '</button>';
        }).join('');
    }

    function showJob(data) {
        var panel = el('rival-suggest-panel');
        var running = data && (data.status === 'queued' || data.status === 'running');
        panel.classList.toggle('hidden', !running);
        if (running) {
            el('rival-suggest-status').textContent = data.status === 'queued'
                ? 'Waiting for its turn' + (data.queue_position ? ' (number ' + data.queue_position + ' in the queue)' : '') + '.'
                : 'Suggesting keywords in ' + storeName(data.country) + ': ' + (data.progress_message || 'starting') + '.';
            el('rival-suggest-bar').style.width = (data.progress_percent || 0) + '%';
        }
    }

    function showNote(text) {
        var note = el('rival-keyword-note');
        note.textContent = text;
        note.classList.remove('hidden');
    }

    function finishJob(data) {
        job = null;
        showJob(null);
        if (data.status === 'completed') {
            var code = data.country;
            var candidates = data.candidates || [];
            reasons[code] = {};
            candidates.forEach(function (c) { reasons[code][c.keyword] = c.reason; });
            more[code] = candidates.filter(function (c) { return !c.preselected; });
            var chosen = candidates.filter(function (c) { return c.preselected; });
            if (state.countries.indexOf(code) === -1) {
                // The country left the list while the run worked: its
                // keywords never land in another country's list.
                showNote('Suggest keywords finished for ' + countryName(code) + ', which is no longer one of the countries. Add it again to see them.');
                list(code);
                chosen.forEach(function (c) { if (!has(code, c.keyword)) list(code).push({text: c.keyword, origin: 'suggested', reason: c.reason}); });
                renderTabs();
                return;
            }
            state.active = code;
            var added = addKeywords(chosen.map(function (c) { return c.keyword; }), 'suggested', chosen.map(function (c) { return c.reason; }), code);
            renderKeywords();
            if (!chosen.length) {
                showNote('No suggestion had real demand where one of these apps already ranks. The others are listed below.');
            } else if (!added) {
                showNote('The suggested keywords are on the list already.');
            }
        } else if (data.status === 'failed') {
            showError(data.error || 'Suggest keywords did not finish.');
        }
        renderTabs();
    }

    function poll() {
        if (!job) return;
        fetch(root.dataset.suggestStatusUrl.replace('/0/', '/' + job + '/'))
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (data.status === 'queued' || data.status === 'running') {
                    showJob(data);
                    setTimeout(poll, 2000);
                } else {
                    finishJob(data);
                }
            }).catch(function () { setTimeout(poll, 4000); });
    }

    var suggestWindow = el('suggest-window');

    function renderSuggestWindow() {
        var title = el('suggest-title');
        if (title) title.textContent = (state.titles || {})[state.active] || state.app_name;
        var estimate = el('suggest-estimate');
        if (estimate) {
            estimate.textContent = 'Suggestions for ' + storeName(state.active) + ' take ' +
                root.dataset.suggestionEstimate + '.';
        }
    }

    el('rival-suggest').addEventListener('click', function () {
        if (job || !state.active) return;
        hideError();
        renderSuggestWindow();
        openWindow(suggestWindow);
        var first = el('rival-subtitle');
        if (first) first.focus();
    });

    if (el('suggest-start')) {
        el('suggest-start').addEventListener('click', function () {
            if (!AI || job || !state.active) return;
            var button = el('suggest-start');
            button.disabled = true;
            flush();
            send(root.dataset.suggestUrl, {
                app_id: state.app_id, country: state.active,
                subtitle: el('rival-subtitle').value, keyword_field: el('rival-keyword-field').value,
                keyword_lengths: lengthsValue(), rivals: state.rivals,
                exclude: list(state.active).map(function (k) { return k.text; }),
            }).then(function (res) {
                button.disabled = false;
                if (!res.ok) { showError(res.data.error); closeWindow(suggestWindow); return; }
                closeWindow(suggestWindow);
                if (window.ActivityIndicator && ActivityIndicator.announce) {
                    ActivityIndicator.announce({
                        queued: res.data.status === 'queued', label: 'Suggest keywords',
                        current: (window.AsoRunQueue && AsoRunQueue.busyWith) ? AsoRunQueue.busyWith() : '', eta: null
                    });
                }
                job = res.data.id;
                showJob(res.data);
                renderTabs();
                poll();
            });
        });
    }
    ['rival-subtitle', 'rival-keyword-field'].forEach(function (id) {
        var input = el(id);
        if (!input) return;
        input.addEventListener('input', function () {
            if (state.from_simulator) {
                state.from_simulator = false;
                el('rival-metadata-note').textContent = 'Apple does not publish these. Paste the app’s own from App Store Connect for better suggestions.';
            }
            changed(600);
        });
        input.addEventListener('blur', function () { if (dirty) flush(); });
    });
    if (el('keyword-length-row')) {
        el('keyword-length-row').addEventListener('change', function () { changed(); });
    }

    el('rival-suggest-cancel').addEventListener('click', function () {
        if (!job) return;
        send(root.dataset.suggestCancelUrl.replace('/0/', '/' + job + '/'), {}).then(function () {
            job = null;
            showJob(null);
            renderTabs();
        });
    });
    el('rival-more-toggle').addEventListener('click', function () {
        el('rival-more-list').classList.toggle('hidden');
    });

    // ---- Add from Search History -------------------------------------------------
    // Each keyword once per country (aso_pro/rivals/history_picker.py), with the
    // apps it is tracked for under it and the Dashboard's two numbers. A keyword
    // already on this country's list is ticked and cannot be ticked off here:
    // "Followed" when the followed tracker has it, "On the list" when it is on
    // this screen's list and not followed yet.

    var picker = el('history-picker');
    var pickerTimer = null;
    var pickerRequest = 0;   // only the newest answer is drawn

    function fillSelect(select, options) {
        var keep = select.value;
        select.length = 1;
        options.forEach(function (o) {
            var option = new Option(o.name, o.value);
            if (o.icon) option.dataset.icon = o.icon;   // the app's logo in its picker
            select.add(option);
        });
        select.value = options.some(function (o) { return String(o.value) === keep; }) ? keep : '';
    }

    function difficultyCell(r) {
        if (r.difficulty == null) return '<span class="text-slate-400 text-xs font-normal">N/A</span>';
        return '<div class="text-sm font-semibold"><span class="' + esc(r.difficulty_color) + '">' + r.difficulty + '</span></div>' +
            '<span class="text-slate-400 text-xs block">' + esc(r.difficulty_label) + '</span>';
    }

    function popularityCell(r, i, total) {
        if (r.popularity == null) return '<span class="text-slate-400 text-xs font-normal">N/A</span>';
        return formatPopularityCell(r, '', i, total);
    }

    function pickerRow(r, i, total) {
        var listed = has(state.active, r.keyword.toLowerCase());
        var hint = r.apps.join(', ') + (r.labels.length ? ' · ' + r.labels.join(', ') : '');
        var tag = listed
            ? '<span class="shrink-0 text-2xs leading-4 rounded px-1.5 bg-white/5 border border-white/10 text-slate-400">' + (r.followed ? 'Followed' : 'On the list') + '</span>'
            : '';
        return '<label class="picker-row grid grid-cols-[1rem_minmax(0,1fr)_7rem_7rem] gap-x-3 items-center -mx-2 px-2 py-1.5 rounded-lg ' +
                (listed ? 'cursor-default' : 'cursor-pointer hover:bg-white/5') + '">' +
            '<input type="checkbox" class="picker-cb w-4 h-4 accent-purple-500' + (listed ? ' opacity-60' : '') + '" value="' + esc(r.keyword) + '"' +
                (listed ? ' checked disabled' : '') + '>' +
            '<span class="min-w-0">' +
                '<span class="flex items-center gap-2 min-w-0">' +
                    '<span class="picker-trunc truncate text-sm font-medium ' + (listed ? 'text-slate-400' : 'text-white') + '" data-keyword="' + esc(r.keyword) + '">' + esc(r.keyword) + '</span>' + tag +
                '</span>' +
                '<span class="picker-trunc block truncate text-2xs text-slate-400">' + esc(hint) + '</span>' +
            '</span>' +
            '<span data-col="popularity" class="text-center">' + popularityCell(r, i, total) + '</span>' +
            '<span data-col="difficulty" class="text-center">' + difficultyCell(r) + '</span>' +
            '</label>';
    }

    function pickerEmpty(data) {
        return '<p class="py-4 text-sm text-slate-400 text-center">' +
            (data.in_country
                ? 'No tracked keywords in ' + esc(storeName(state.active)) + ' match.'
                : 'No tracked keywords in ' + esc(storeName(state.active)) + ' yet. Search keywords on the Keywords page first.') +
            '</p>';
    }

    function loadPicker() {
        var params = new URLSearchParams({country: state.active, for_app: state.app_id});
        if (el('history-picker-app').value) params.set('app', el('history-picker-app').value);
        if (el('history-picker-label').value) params.set('label', el('history-picker-label').value);
        if (el('history-picker-q').value.trim()) params.set('q', el('history-picker-q').value.trim());
        var request = ++pickerRequest;
        fetch(root.dataset.historyUrl + '?' + params).then(function (r) { return r.json(); }).then(function (data) {
            if (request !== pickerRequest) return;
            fillSelect(el('history-picker-app'), (data.apps || []).map(function (a) { return {name: a.name, value: a.id, icon: a.icon}; }));
            fillSelect(el('history-picker-label'), (data.labels || []).map(function (name) { return {name: name, value: name}; }));
            var rows = data.rows || [];
            var rest = data.total > rows.length
                ? '<p class="py-3 text-xs text-slate-400 text-center">The ' + rows.length + ' most popular of ' + data.total +
                  ' keywords are shown. Search to find the others.</p>'
                : '';
            var body = el('history-picker-rows');
            body.innerHTML = rows.length
                ? rows.map(function (r, i) { return pickerRow(r, i, rows.length); }).join('') + rest
                : pickerEmpty(data);
            el('history-picker-list').scrollTop = 0;
            // The full text on hover, only where it does not fit.
            Array.prototype.forEach.call(body.querySelectorAll('.picker-trunc'), function (span) {
                if (span.scrollWidth > span.clientWidth) span.setAttribute('data-tip', span.textContent);
            });
        });
    }

    function pickerCheckboxes() {
        return Array.prototype.filter.call(document.querySelectorAll('#history-picker-rows .picker-cb'), function (cb) {
            return !cb.disabled;
        });
    }

    if (window.keepPopoversIn) keepPopoversIn(el('history-picker-list'));
    el('rival-open-history').addEventListener('click', function () {
        if (!state.active) return;
        el('history-picker-title').textContent = 'Add from Tracked Keywords: ' + countryName(state.active);
        el('history-picker-app').value = '';
        if (window.CountryPicker) CountryPicker.syncSelect('history-picker-app');
        el('history-picker-label').value = '';
        el('history-picker-q').value = '';
        el('history-picker-rows').innerHTML = '';
        openWindow(picker);
        loadPicker();
    });
    ['history-picker-app', 'history-picker-label'].forEach(function (id) { el(id).addEventListener('change', loadPicker); });
    el('history-picker-q').addEventListener('input', function () {
        clearTimeout(pickerTimer);
        pickerTimer = setTimeout(loadPicker, 300);
    });
    el('history-picker-all').addEventListener('click', function () {
        pickerCheckboxes().forEach(function (cb) { cb.checked = true; });
    });
    el('history-picker-add').addEventListener('click', function () {
        var picked = pickerCheckboxes().filter(function (cb) { return cb.checked; })
            .map(function (cb) { return cb.value; });
        addKeywords(picked, 'history');
        closeWindow(picker);
    });

    // ---- rivals -------------------------------------------------------------------

    function renderRivals() {
        el('rival-chips').innerHTML = state.rivals.length ? state.rivals.map(function (r, i) {
            return '<span class="inline-flex items-center gap-2 text-sm bg-slate-900 border border-white/10 rounded-full pl-1.5 pr-1 py-1 text-slate-200">' +
                (r.icon_url ? '<img src="' + esc(r.icon_url) + '" alt="" class="w-6 h-6 rounded-md">' : '<span class="w-6 h-6 rounded-md bg-slate-700"></span>') +
                esc(r.name) + '<button type="button" class="rival-remove w-6 h-6 rounded-full text-slate-400 hover:text-white hover:bg-white/10" data-index="' + i + '" aria-label="Remove ' + esc(r.name) + '">×</button></span>';
        }).join('') : '<span class="text-sm text-slate-400">No rival yet.</span>';
        el('rival-search').disabled = state.rivals.length >= MAX_RIVALS;
        el('rival-search').placeholder = state.rivals.length >= MAX_RIVALS
            ? 'Rival Tracker compares with up to ' + MAX_RIVALS + ' rivals. Remove one to add another.'
            : 'Add an app by name or App Store link';
        renderEstimate();
    }

    function addRival(r) {
        if (state.rivals.length >= MAX_RIVALS) return;
        if (r.track_id === state.app_track_id) { showError(state.app_short + ' is the app this tracker follows, so it cannot be its own rival.'); return; }
        if (state.rivals.some(function (x) { return x.track_id === r.track_id; })) return;
        state.rivals.push(r);
        renderRivals();
        scheduleIdeas();
        changed();
    }

    function renderIdeas(ideas) {
        var have = state.rivals.map(function (r) { return r.track_id; });
        ideas = ideas.filter(function (i) { return have.indexOf(i.track_id) === -1; });
        el('rival-ideas').innerHTML = ideas.length ? '<p class="text-xs uppercase tracking-wide text-slate-400 mb-2">Ideas</p><ul class="space-y-1.5">' + ideas.map(function (i) {
            return '<li class="flex items-center justify-between gap-3 text-sm"><span class="flex items-center gap-2 min-w-0 text-slate-200">' +
                (i.icon_url ? '<img src="' + esc(i.icon_url) + '" alt="" class="w-6 h-6 rounded-md shrink-0">' : '<span class="w-6 h-6 rounded-md bg-slate-700 shrink-0"></span>') +
                '<span class="truncate">' + esc(i.name) + '</span> <span class="text-xs text-slate-400 truncate">' + esc(i.reason) + '</span></span>' +
                '<button type="button" class="idea-add shrink-0 text-xs text-sky-200 border border-sky-500/30 bg-sky-900/20 hover:bg-sky-900/40 rounded-lg px-2.5 py-1" data-idea="' + esc(JSON.stringify(i)) + '">Add</button></li>';
        }).join('') + '</ul>' : '';
    }

    function loadIdeas() {
        var keywords = {};
        state.countries.forEach(function (code) { keywords[code] = list(code).map(function (k) { return k.text; }); });
        send(root.dataset.rivalsUrl, {
            app_id: state.app_id, keywords: keywords,
            exclude_ids: state.rivals.map(function (r) { return r.track_id; }),
        }).then(function (res) { if (res.ok) renderIdeas(res.data.ideas || []); });
    }
    function scheduleIdeas() {
        clearTimeout(ideasTimer);
        ideasTimer = setTimeout(loadIdeas, 500);
    }

    el('rival-search').addEventListener('input', function (e) {
        clearTimeout(searchTimer);
        var q = e.target.value.trim();
        var box = el('rival-search-results');
        if (q.length < 2) { box.classList.add('hidden'); return; }
        searchTimer = setTimeout(function () {
            fetch(root.dataset.lookupUrl + '?q=' + encodeURIComponent(q)).then(function (r) { return r.json(); }).then(function (data) {
                var apps = data.apps || [];
                box.innerHTML = apps.length ? apps.map(function (a) {
                    var own = a.trackId === state.app_track_id;
                    return '<button type="button" class="search-pick w-full flex items-center gap-3 px-3 py-2 text-left text-sm hover:bg-white/5 disabled:opacity-50" ' + (own ? 'disabled ' : '') +
                        'data-app="' + esc(JSON.stringify({track_id: a.trackId, name: a.trackName, icon_url: a.artworkUrl100, seller_name: a.sellerName, source: 'manual'})) + '">' +
                        '<img src="' + esc(a.artworkUrl100) + '" alt="" class="w-8 h-8 rounded-lg"><span class="min-w-0"><span class="block text-slate-100 truncate">' + esc(a.trackName) + '</span>' +
                        '<span class="block text-xs text-slate-400 truncate">' + esc(a.sellerName) + (own ? ' · the app this tracker follows' : '') + '</span></span></button>';
                }).join('') : '<p class="px-3 py-2 text-sm text-slate-400">' + esc(data.error || 'No App Store app matches that.') + '</p>';
                box.classList.remove('hidden');
            });
        }, 350);
    });

    // ---- estimate, Start following, stop -------------------------------------------

    function missing() {
        var keywords = state.countries.reduce(function (n, code) { return n + list(code).length; }, 0);
        var parts = [];
        if (!state.countries.length) parts.push('a country');
        if (!keywords) parts.push('a keyword');
        if (!state.rivals.length) parts.push('a rival');
        return parts;
    }

    function renderEstimate() {
        var keywords = state.countries.reduce(function (n, code) { return n + list(code).length; }, 0);
        var lacking = missing();
        var start = el('rival-save');
        if (start) start.disabled = !!lacking.length;
        if (lacking.length) {
            var last = lacking.pop();
            var what = (lacking.length ? lacking.join(', ') + ' and ' : '') + last;
            el('rival-estimate').textContent = state.following
                ? 'Add ' + what + ': Rival Tracker compares at least one keyword and one rival.'
                : 'Add ' + what + ' to start following.';
            return;
        }
        var calls = keywords + state.countries.length + state.rivals.length * state.countries.length;
        var countriesText = state.countries.length === 1 ? '1 country' : state.countries.length + ' countries';
        el('rival-estimate').textContent = 'Every day: ' + plural(keywords, 'keyword') + ' and ' + plural(state.rivals.length, 'rival') +
            ' in ' + countriesText + ', ' + CountryPicker.durationText(calls * SECONDS) + ' at most.';
    }

    function showError(text) {
        var box = el('rival-save-error');
        box.textContent = text || 'That did not go through. Please try again.';
        box.classList.remove('hidden');
    }
    function hideError() { el('rival-save-error').classList.add('hidden'); }

    if (el('rival-save')) {
        el('rival-save').addEventListener('click', function () {
            hideError();
            var button = el('rival-save');
            button.disabled = true;
            clearTimeout(saveTimer);
            dirty = false;
            send(root.dataset.saveUrl, setupBody(true)).then(function (res) {
                if (!res.ok) { button.disabled = false; showError(res.data.error); return; }
                window.location.href = res.data.redirect;
            });
        });
    }

    if (el('rival-delete')) {
        el('rival-delete').addEventListener('click', function () {
            showConfirm('Stop following ' + state.app_short + '? Its rivals and Rival Tracker history go. Its keywords stay in Tracked Keywords.',
                {title: 'Stop following', confirmLabel: 'Stop following', confirmStyle: 'danger'}).then(function (ok) {
                if (!ok) return;
                clearTimeout(saveTimer);
                dirty = false;
                send(root.dataset.deleteUrl, {}).then(function (res) { if (res.ok) window.location.href = res.data.redirect; });
            });
        });
    }

    // ---- delegated clicks ----------------------------------------------------------------

    document.addEventListener('click', function (e) {
        var tab = e.target.closest('#rival-country-tabs [data-country]');
        if (tab) { state.active = tab.dataset.country; renderKeywords(); return; }
        var remove = e.target.closest('.kw-remove');
        if (remove) { list(state.active).splice(parseInt(remove.dataset.index, 10), 1); renderKeywords(); scheduleIdeas(); changed(); return; }
        var moreBtn = e.target.closest('.kw-more');
        if (moreBtn) {
            var cand = (more[state.active] || []).filter(function (c) { return c.keyword === moreBtn.dataset.keyword; })[0];
            addKeywords([moreBtn.dataset.keyword], 'suggested', [cand ? cand.reason : '']);
            return;
        }
        var idea = e.target.closest('.idea-add');
        if (idea) { addRival(JSON.parse(idea.dataset.idea)); return; }
        var pick = e.target.closest('.search-pick');
        if (pick && !pick.disabled) {
            addRival(JSON.parse(pick.dataset.app));
            el('rival-search').value = '';
            el('rival-search-results').classList.add('hidden');
            return;
        }
        var rivalRemove = e.target.closest('.rival-remove');
        if (rivalRemove) {
            state.rivals.splice(parseInt(rivalRemove.dataset.index, 10), 1);
            renderRivals();
            scheduleIdeas();
            changed();
            return;
        }
        if (e.target.closest('#history-picker [data-close]') || e.target === picker) { closeWindow(picker); return; }
        if (e.target.closest('#suggest-window [data-close]') || e.target === suggestWindow) { closeWindow(suggestWindow); return; }
        if (!e.target.closest('#rival-search') && !e.target.closest('#rival-search-results')) {
            el('rival-search-results').classList.add('hidden');
        }
    });
    document.addEventListener('keydown', function (e) {
        if (e.key !== 'Escape') return;
        [suggestWindow, picker].forEach(function (box) { if (!box.classList.contains('hidden')) closeWindow(box); });
    });

    // ---- start ----------------------------------------------------------------------------

    if (el('rival-subtitle')) el('rival-subtitle').value = state.subtitle || '';
    if (el('rival-keyword-field')) el('rival-keyword-field').value = state.keyword_field || '';
    if (state.from_simulator && el('rival-metadata-note')) {
        el('rival-metadata-note').textContent = 'Filled in from the last ASO Simulator run for ' + state.app_short + '. Check they match the live ones.';
    }
    if (window.setKeywordLengths) setKeywordLengths(state.keyword_lengths);
    el('rival-weekly').checked = AI && !!state.weekly_summary;
    el('rival-weekly').addEventListener('change', function () { changed(); });

    var countryPicker = CountryPicker.get('rival-countries');
    countryPicker.set(state.countries);
    CountryPicker.onChange('rival-countries', function (codes) {
        var added = codes.filter(function (code) { return state.countries.indexOf(code) === -1; });
        state.countries = codes.slice();
        state.countries.forEach(list);
        // A country just added is the one to fill next.
        if (added.length) state.active = added[added.length - 1];
        if (state.countries.indexOf(state.active) === -1) state.active = state.countries[0] || '';
        renderKeywords();
        scheduleIdeas();
        changed();
    });
    CountryPicker.get('rival-app');
    CountryPicker.onChange('rival-app', function (values) {
        if (!values.length) return;
        // The change on its way first, then the other app's setup.
        flushOnLeave();
        window.location.href = values[0];
    });

    renderRivals();
    renderKeywords();
    loadIdeas();
    if (state.pending_job) {
        job = state.pending_job.id;
        showJob(state.pending_job);
        renderTabs();
        poll();
    }
})();
