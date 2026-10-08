/**
 * TrackKeywords - track keywords for one of the user's apps, or for All apps
 * (no app), through a keyword search (aso:search): fresh App Store numbers,
 * and the picked app's rank. One copy for Top Search Terms (one keyword at a
 * time) and the Keywords and Suggested Metadata tabs of the AI Researcher,
 * the AI Competitor and the ASO Score Simulator (the rows ticked there).
 *
 * The page includes aso_pro/_includes/track_keywords.html: the apps for the
 * menu (#track-apps), the search address and the CSRF token (#track-root),
 * the bar under the page and its confirmation.
 *
 *   TrackKeywords.choose(anchor, onPick)   the "Track for..." menu; onPick({id, name}),
 *                                          id '' for All apps; no menu without apps
 *   TrackKeywords.start(keywords, country, appId, {runNow})
 *                                          -> Promise of the search's answer
 *   TrackKeywords.forRun(runId, country)   an AI tool shows a run: ticks are kept
 *                                          while it stays the same run
 *   TrackKeywords.cell(term)               the tick box cell of a table row
 *   TrackKeywords.refresh()                after rows were drawn or filtered
 */
(function () {
    'use strict';

    var selected = {};          // lower-cased term -> the term as shown
    var order = [];             // lower-cased terms, in the order they were ticked
    var runKey;                 // the run the ticks belong to
    var runCountry = 'us';
    var menu = null;
    var toastTimer = null;

    function byId(id) { return document.getElementById(id); }

    function esc(value) {
        return String(value === null || value === undefined ? '' : value)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function apps() {
        var node = byId('track-apps');
        try { return node ? JSON.parse(node.textContent) || [] : []; } catch (e) { return []; }
    }

    function keywordsText(n) {
        return n.toLocaleString('en-US') + (n === 1 ? ' keyword' : ' keywords');
    }

    // --- the "Track for..." menu ----------------------------------------------

    function closeMenu() {
        if (menu) { menu.remove(); menu = null; }
    }

    function iconHtml(app) {
        if (app.icon) {
            return '<img src="' + esc(app.icon) + '" alt="" class="h-5 w-5 shrink-0 rounded">';
        }
        return '<span class="flex h-5 w-5 shrink-0 items-center justify-center rounded bg-slate-700 text-2xs text-slate-300">' +
            esc((app.name || '?').charAt(0).toUpperCase()) + '</span>';
    }

    function choose(anchor, onPick) {
        var list = apps();
        if (!list.length) { onPick({id: '', name: ''}); return; }
        closeMenu();
        var box = document.createElement('div');
        box.className = 'fixed z-50 w-64 rounded-lg border border-white/10 bg-slate-800 p-2 shadow-xl';
        box.setAttribute('role', 'menu');
        box.innerHTML =
            '<p class="px-1.5 pb-1.5 pt-0.5 text-2xs font-medium uppercase tracking-wider text-slate-400">Track for</p>' +
            list.map(function (app) {
                return '<button type="button" role="menuitem" data-app-id="' + esc(app.id) + '" data-app-name="' + esc(app.name) + '"' +
                    ' class="track-target flex w-full items-center gap-2 rounded px-1.5 py-1 text-left text-xs text-slate-200 hover:bg-white/5">' +
                    iconHtml(app) + '<span class="truncate">' + esc(app.name) + '</span></button>';
            }).join('') +
            '<div class="my-1 border-t border-white/5"></div>' +
            '<button type="button" role="menuitem" data-app-id="" data-app-name=""' +
            ' class="track-target flex w-full items-center gap-2 rounded px-1.5 py-1 text-left text-xs text-slate-200 hover:bg-white/5">' +
            '<span class="flex h-5 w-5 shrink-0 items-center justify-center rounded bg-slate-700 text-slate-300">' +
            '<svg class="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h16"/></svg></span>' +
            '<span>All apps</span></button>' +
            '<p class="px-1.5 pt-1.5 text-2xs leading-relaxed text-slate-400">Pick an app to see its rank on the Keywords page.</p>';
        document.body.appendChild(box);
        var rect = anchor.getBoundingClientRect();
        var top = rect.bottom + 6;
        if (top + box.offsetHeight > window.innerHeight - 8) top = rect.top - box.offsetHeight - 6;
        box.style.top = Math.max(8, top) + 'px';
        box.style.left = Math.max(8, Math.min(rect.left, window.innerWidth - box.offsetWidth - 8)) + 'px';
        box.querySelectorAll('.track-target').forEach(function (item) {
            item.addEventListener('click', function (event) {
                event.stopPropagation();
                closeMenu();
                onPick({id: item.dataset.appId, name: item.dataset.appName});
            });
        });
        menu = box;
        var first = box.querySelector('.track-target');
        if (first) first.focus();
    }

    document.addEventListener('click', function (event) {
        if (menu && !menu.contains(event.target) && !event.target.closest('[data-track-anchor]')) closeMenu();
    });
    document.addEventListener('keydown', function (event) {
        if (event.key === 'Escape') closeMenu();
    });
    // The anchor moves under the menu when the page scrolls: close it.
    document.addEventListener('scroll', closeMenu, true);

    // --- starting the search --------------------------------------------------

    function start(keywords, country, appId, options) {
        var root = byId('track-root');
        var csrf = root && root.querySelector('[name=csrfmiddlewaretoken]');
        var body = new URLSearchParams({keywords: keywords.join('\n'), countries: country || 'us'});
        if (appId) body.set('app_id', appId);
        if (options && options.runNow) body.set('run_now', '1');
        return fetch(root.dataset.searchUrl, {
            method: 'POST',
            headers: {'X-CSRFToken': csrf ? csrf.value : '', 'Content-Type': 'application/x-www-form-urlencoded'},
            body: body.toString(),
        }).then(function (r) {
            return r.json().catch(function () { return {error: 'The keywords could not be tracked. Try again.'}; });
        });
    }

    // --- tick boxes and the bar (the AI tools) --------------------------------

    function count() { return order.length; }

    function forRun(runId, country) {
        if (runId !== runKey) {
            selected = {};
            order = [];
            runKey = runId;
        }
        runCountry = (country || 'us').toLowerCase();
        refresh();
    }

    function cell(term) {
        var key = String(term || '').trim().toLowerCase();
        return '<td class="w-8 py-2 px-2 text-center"><input type="checkbox" class="track-checkbox cursor-pointer accent-purple-500"' +
            ' data-term="' + esc(String(term || '').trim()) + '" aria-label="Track ' + esc(term) + '"' +
            (selected[key] ? ' checked' : '') + '></td>';
    }

    function setTicked(term, on) {
        var text = String(term || '').trim();
        var key = text.toLowerCase();
        if (!key) return;
        if (on && !selected[key]) { selected[key] = text; order.push(key); }
        if (!on && selected[key]) {
            delete selected[key];
            order = order.filter(function (k) { return k !== key; });
        }
    }

    function visibleBoxes(body) {
        return Array.prototype.filter.call(body.querySelectorAll('tr'), function (row) {
            return row.style.display !== 'none';
        }).map(function (row) { return row.querySelector('.track-checkbox'); }).filter(Boolean);
    }

    function refresh() {
        // The same term can sit in both tables: its boxes follow the selection.
        document.querySelectorAll('.track-checkbox').forEach(function (box) {
            box.checked = !!selected[String(box.dataset.term || '').toLowerCase()];
        });
        document.querySelectorAll('.track-select-all').forEach(function (header) {
            var body = byId(header.dataset.body);
            var boxes = body ? visibleBoxes(body) : [];
            var ticked = boxes.filter(function (box) { return box.checked; }).length;
            header.checked = boxes.length > 0 && ticked === boxes.length;
            header.indeterminate = ticked > 0 && ticked < boxes.length;
        });
        var bar = byId('track-bar');
        if (!bar) return;
        bar.classList.toggle('hidden', count() === 0);
        var label = byId('track-bar-count');
        if (label) label.textContent = count().toLocaleString('en-US');
        if (count() > 0) hideToast();
    }

    function clear() {
        selected = {};
        order = [];
        refresh();
    }

    document.addEventListener('change', function (event) {
        var box = event.target;
        if (box.classList && box.classList.contains('track-checkbox')) {
            setTicked(box.dataset.term, box.checked);
            refresh();
        } else if (box.classList && box.classList.contains('track-select-all')) {
            var body = byId(box.dataset.body);
            if (body) visibleBoxes(body).forEach(function (each) { setTicked(each.dataset.term, box.checked); });
            refresh();
        }
    });

    function hideToast() {
        var toast = byId('track-toast');
        if (toast) toast.classList.add('hidden');
        clearTimeout(toastTimer);
    }

    function showToast(message, appId) {
        var toast = byId('track-toast');
        if (!toast) return;
        byId('track-toast-msg').textContent = message;
        var params = new URLSearchParams({app: appId || '', country: runCountry});
        byId('track-toast-link').href = '/?' + params.toString();
        toast.classList.remove('hidden');
        clearTimeout(toastTimer);
        toastTimer = setTimeout(hideToast, 6000);
    }

    function trackTicked(button) {
        var terms = order.map(function (key) { return selected[key]; });
        if (!terms.length) return;
        choose(button, function (app) {
            button.disabled = true;
            button.textContent = 'Starting...';
            start(terms, runCountry, app.id).then(function (data) {
                button.disabled = false;
                button.textContent = 'Track these keywords';
                if (data.error || !data.job) {
                    if (window.showAlert) showAlert(data.error || 'The keywords could not be tracked. Try again.', {title: 'Track keywords'});
                    return;
                }
                clear();
                var job = data.job;
                if (window.ActivityIndicator && ActivityIndicator.announce) {
                    ActivityIndicator.announce({
                        queued: job.status === 'queued',
                        label: keywordsText(job.total_keywords) + ' in ' + job.countries_text,
                        current: data.queued_behind,
                        eta: data.eta_seconds,
                    });
                }
                showToast('Tracking ' + keywordsText(job.total_keywords) + ' for ' + (app.name || 'All apps') + '.', app.id);
            }).catch(function () {
                button.disabled = false;
                button.textContent = 'Track these keywords';
                if (window.showAlert) showAlert('The keywords could not be tracked. Try again.', {title: 'Track keywords'});
            });
        });
    }

    document.addEventListener('click', function (event) {
        if (event.target.closest('#track-bar-go')) { trackTicked(byId('track-bar-go')); return; }
        if (event.target.closest('#track-bar-clear')) { closeMenu(); clear(); }
    });

    window.TrackKeywords = {
        choose: choose,
        start: start,
        forRun: forRun,
        cell: cell,
        refresh: refresh,
    };
})();
