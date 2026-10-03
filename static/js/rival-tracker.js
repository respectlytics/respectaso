/**
 * The Rival Tracker page (aso_pro/templates/aso_pro/rival_tracker/page.html).
 *
 * Keeps the cards current: the page polls the status endpoint every five
 * seconds while visible and swaps #rival-sections in place when the data
 * changed (aso/history_revision.py counts every change, whoever makes it).
 * Opens the rank history chart of a keyword and a rival's App Store update
 * history, adds a new top 10 app as a rival, starts a refresh and a summary.
 * Every number comes from the server (aso_pro/rivals/report.py); this file
 * only draws.
 */
(function () {
    'use strict';

    var page = document.getElementById('rival-page');
    if (!page) return;
    var csrf = page.dataset.csrf;
    var country = page.dataset.country;
    var aheadOnly = false;
    var rankTerm = '';
    var rankDays = 90;

    function el(id) { return document.getElementById(id); }

    function esc(text) {
        return String(text == null ? '' : text).replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    function post(url, body) {
        return fetch(url, {
            method: 'POST',
            headers: {'X-CSRFToken': csrf, 'Content-Type': 'application/json'},
            body: JSON.stringify(body || {}),
        }).then(function (resp) {
            return resp.json().catch(function () { return {}; }).then(function (data) {
                return {ok: resp.ok, data: data};
            });
        });
    }

    function message(text) {
        var box = el('rival-message');
        box.textContent = text || '';
        box.classList.toggle('hidden', !text);
    }

    function openDialog(id) {
        var dialog = el(id);
        dialog.classList.remove('hidden');
        dialog.classList.add('flex');
    }

    function closeDialog(dialog) {
        dialog.classList.add('hidden');
        dialog.classList.remove('flex');
    }

    function applyAheadFilter() {
        var box = el('rival-ahead-only');
        if (box) box.checked = aheadOnly;
        Array.prototype.forEach.call(document.querySelectorAll('.rival-row'), function (row) {
            row.classList.toggle('hidden', aheadOnly && !row.classList.contains('rival-ahead'));
        });
    }

    // ---- sorting the keyword table ------------------------------------------

    // By any column, from its header (the owner, 2026-10-02): keywords A to Z,
    // popularity highest first, an app's rank best first, and the other way on
    // a second click. A dash (no popularity, not in the top 200) stays at the
    // bottom either way. The order holds while the page updates itself; the
    // page arrives sorted by popularity, highest first.
    var sortBy = {key: 'popularity', dir: 'desc'};
    var FIRST_DIR = {keyword: 'asc', popularity: 'desc'};   // an app's rank: 'asc', #1 first

    function sortValue(row, key) {
        if (key === 'keyword') return row.dataset.term || '';
        var raw = key === 'popularity' ? row.dataset.popularity
            : (row.querySelectorAll('td[data-rank]')[parseInt(key.slice(4), 10)] || {dataset: {}}).dataset.rank;
        return raw === '' || raw == null ? null : Number(raw);
    }

    function applySort() {
        var body = document.querySelector('#rival-keywords tbody');
        if (!body) return;
        var sign = sortBy.dir === 'asc' ? 1 : -1;
        var rows = Array.prototype.slice.call(body.querySelectorAll('tr.rival-row'));
        rows.sort(function (a, b) {
            var va = sortValue(a, sortBy.key), vb = sortValue(b, sortBy.key);
            if (va === null || vb === null) {
                if (va !== vb) return va === null ? 1 : -1;
            } else {
                var order = typeof va === 'string' ? va.localeCompare(vb) : va - vb;
                if (order) return order * sign;
            }
            return (a.dataset.term || '').localeCompare(b.dataset.term || '');
        });
        rows.forEach(function (row) { body.appendChild(row); });
        Array.prototype.forEach.call(document.querySelectorAll('#rival-keywords th[data-sort]'), function (th) {
            var on = th.dataset.sort === sortBy.key;
            th.setAttribute('aria-sort', on ? (sortBy.dir === 'asc' ? 'ascending' : 'descending') : 'none');
            var arrow = th.querySelector('.sort-indicator');
            if (arrow) arrow.textContent = on ? (sortBy.dir === 'asc' ? '▲' : '▼') : '';
        });
    }

    function sortOn(key) {
        sortBy = key === sortBy.key
            ? {key: key, dir: sortBy.dir === 'asc' ? 'desc' : 'asc'}
            : {key: key, dir: FIRST_DIR[key] || 'asc'};
        applySort();
    }

    // ---- keeping the page current ------------------------------------------

    var checking = false;
    function check() {
        if (checking || document.hidden) return;
        checking = true;
        fetch(page.dataset.statusUrl).then(function (resp) {
            return resp.ok ? resp.json() : null;
        }).then(function (data) {
            if (!data) return;
            var status = el('rival-status');
            if (data.run && data.run.running && status) {
                status.innerHTML = '<span class="text-sky-300">Checking now: ' + esc(data.run.message) + '</span>';
            }
            var sections = el('rival-sections');
            if (sections && data.history_revision && sections.dataset.revision !== data.history_revision) {
                return InPlace.load(window.location.href, {
                    background: true,
                    history: 'none',
                    swap: function (doc) { InPlace.swapSection(doc, 'rival-sections'); },
                }).then(function () {
                    applySort();
                    applyAheadFilter();
                    if (window.TimeAgo) TimeAgo.render(el('rival-sections'));
                });
            }
        }).catch(function () {
            // Offline or restarting: the next check tries again.
        }).finally(function () { checking = false; });
    }

    // ---- the rank history chart --------------------------------------------

    function drawChart(data) {
        var W = 860, H = 320, L = 48, R = 16, T = 14, B = 30;
        var n = data.days.length;
        var max = data.max_rank || 10;
        function x(i) { return L + (n <= 1 ? (W - L - R) / 2 : i * (W - L - R) / (n - 1)); }
        function y(rank) { return T + (rank - 1) * (H - T - B) / Math.max(1, max - 1); }
        var svg = ['<svg viewBox="0 0 ' + W + ' ' + H + '" class="w-full h-auto" role="img" aria-label="Rank over time">'];
        [1, 10, 25, 50, 100, 200].forEach(function (tick) {
            if (tick > max) return;
            svg.push('<line x1="' + L + '" x2="' + (W - R) + '" y1="' + y(tick) + '" y2="' + y(tick) + '" stroke="rgba(255,255,255,0.06)"/>');
            svg.push('<text x="' + (L - 8) + '" y="' + (y(tick) + 4) + '" text-anchor="end" font-size="11" fill="#64748b">#' + tick + '</text>');
        });
        [0, Math.floor((n - 1) / 2), n - 1].forEach(function (i, k, list) {
            if (i < 0 || list.indexOf(i) !== k) return;
            var d = new Date(data.days[i] + 'T12:00:00');
            var label = d.toLocaleDateString(undefined, {month: 'short', day: 'numeric'});
            var anchor = n > 1 && i === 0 ? 'start' : (n > 1 && i === n - 1 ? 'end' : 'middle');
            svg.push('<text x="' + x(i) + '" y="' + (H - 8) + '" text-anchor="' + anchor + '" font-size="11" fill="#64748b">' + esc(label) + '</text>');
        });
        data.series.slice().reverse().forEach(function (s) {
            var path = '', open = false;
            s.ranks.forEach(function (rank, i) {
                if (rank == null) { open = false; return; }
                path += (open ? 'L' : 'M') + x(i).toFixed(1) + ' ' + y(rank).toFixed(1) + ' ';
                open = true;
            });
            if (path) {
                svg.push('<path d="' + path + '" fill="none" stroke="' + s.color + '" stroke-width="' + (s.is_tracked_app ? 2.75 : 1.75) + '" stroke-linejoin="round" stroke-linecap="round"/>');
            }
            s.ranks.forEach(function (rank, i) {
                var alone = rank != null && (i === 0 || s.ranks[i - 1] == null) && (i === n - 1 || s.ranks[i + 1] == null);
                if (alone) svg.push('<circle cx="' + x(i) + '" cy="' + y(rank) + '" r="2.5" fill="' + s.color + '"/>');
            });
        });
        // The day under the pointer (hoverChart): a guide line and a dot on each line.
        svg.push('<line id="rank-hover-line" y1="' + T + '" y2="' + (H - B) + '" stroke="rgba(255,255,255,0.25)" stroke-dasharray="3 3" visibility="hidden"/>');
        svg.push('<g id="rank-hover-dots"></g>');
        svg.push('</svg>');
        var chart = el('rank-dialog-chart');
        chart.innerHTML = svg.join('') +
            '<div id="rank-hover-tip" class="hidden pointer-events-none absolute z-10 w-60 rounded-lg border border-white/10 bg-slate-900/95 px-3 py-2 shadow-xl"></div>';
        hoverChart(chart, data, x, y, W);
        el('rank-dialog-legend').innerHTML = data.series.map(function (s) {
            var last = null;
            for (var i = s.ranks.length - 1; i >= 0; i--) { if (s.ranks[i] != null) { last = s.ranks[i]; break; } }
            return '<span class="inline-flex items-center gap-1.5 ' + (s.is_tracked_app ? 'text-purple-200 font-medium' : 'text-slate-300') + '">' +
                '<span class="w-2.5 h-2.5 rounded-full" style="background:' + s.color + '"></span>' + appIcon(s) + esc(s.short_name) +
                (last ? ' <span class="text-slate-400">#' + last + '</span>' : ' <span class="text-slate-400">not in the top 200</span>') + '</span>';
        }).join('');
        Array.prototype.forEach.call(document.querySelectorAll('#rank-dialog-range button'), function (b) {
            var on = parseInt(b.dataset.days, 10) === rankDays;
            b.classList.toggle('bg-purple-600/20', on);
            b.classList.toggle('text-purple-200', on);
        });
    }

    function appIcon(s) {
        return s.icon_url ? '<img src="' + esc(s.icon_url) + '" alt="" class="w-4 h-4 rounded shrink-0">'
                          : '<span class="w-4 h-4 rounded bg-slate-700 shrink-0" aria-hidden="true"></span>';
    }

    // Every app's rank on the day under the pointer, best first, each with its
    // logo, beside a guide line through the chart (the owner, 2026-10-02).
    function hoverChart(chart, data, x, y, W) {
        var svgEl = chart.querySelector('svg'), tip = el('rank-hover-tip');
        var line = el('rank-hover-line'), dots = el('rank-hover-dots');
        var n = data.days.length, shown = -1;

        function fill(i) {
            line.setAttribute('x1', x(i));
            line.setAttribute('x2', x(i));
            line.setAttribute('visibility', 'visible');
            dots.innerHTML = data.series.map(function (s) {
                var rank = s.ranks[i];
                return rank == null ? '' : '<circle cx="' + x(i) + '" cy="' + y(rank) + '" r="' + (s.is_tracked_app ? 4 : 3.5) +
                    '" fill="' + s.color + '" stroke="#1e293b" stroke-width="1.5"/>';
            }).join('');
            var rows = data.series.slice().sort(function (a, b) {
                var ra = a.ranks[i], rb = b.ranks[i];
                if (ra == null) return rb == null ? 0 : 1;
                return rb == null ? -1 : ra - rb;
            });
            var day = new Date(data.days[i] + 'T12:00:00').toLocaleDateString(undefined, {weekday: 'short', month: 'short', day: 'numeric'});
            tip.innerHTML = '<p class="mb-1.5 text-xs font-medium text-white">' + esc(day) + '</p>' + rows.map(function (s) {
                var rank = s.ranks[i];
                return '<div class="flex items-center gap-2 py-0.5 text-xs">' +
                    '<span class="w-2 h-2 rounded-full shrink-0" style="background:' + s.color + '"></span>' + appIcon(s) +
                    '<span class="min-w-0 flex-1 truncate ' + (s.is_tracked_app ? 'text-purple-200 font-medium' : 'text-slate-300') + '">' + esc(s.short_name) + '</span>' +
                    '<span class="tabular-nums ' + (rank == null ? 'text-slate-500' : 'text-white') + '">' + (rank == null ? '—' : '#' + rank) + '</span></div>';
            }).join('');
            tip.classList.remove('hidden');
        }

        svgEl.addEventListener('mousemove', function (e) {
            var box = svgEl.getBoundingClientRect();
            var at = (e.clientX - box.left) * W / box.width;
            var i = n <= 1 ? 0 : Math.max(0, Math.min(n - 1, Math.round((at - x(0)) / (x(1) - x(0)))));
            if (i !== shown) { shown = i; fill(i); }
            // Beside the pointer, on the side with room, inside the chart.
            var frame = chart.getBoundingClientRect();
            var left = e.clientX - frame.left, top = e.clientY - frame.top - tip.offsetHeight / 2;
            tip.style.left = (frame.width - left > tip.offsetWidth + 24 ? left + 16 : left - tip.offsetWidth - 16) + 'px';
            tip.style.top = Math.max(0, Math.min(frame.height - tip.offsetHeight, top)) + 'px';
        });
        svgEl.addEventListener('mouseleave', function () {
            shown = -1;
            line.setAttribute('visibility', 'hidden');
            dots.innerHTML = '';
            tip.classList.add('hidden');
        });
    }

    function openRankHistory(term) {
        rankTerm = term;
        var url = page.dataset.rankHistoryUrl + '?' + new URLSearchParams({term: term, country: country, days: rankDays});
        fetch(url).then(function (r) { return r.json(); }).then(function (data) {
            if (data.error) { message(data.error); return; }
            el('rank-dialog-title').textContent = '“' + data.keyword + '” in ' + data.store_name;
            drawChart(data);
            openDialog('rank-dialog');
        });
    }

    function openAppHistory(trackId) {
        var url = page.dataset.appHistoryUrl + '?' + new URLSearchParams({track_id: trackId, country: country});
        fetch(url).then(function (r) { return r.ok ? r.text() : Promise.reject(); }).then(function (html) {
            el('history-dialog-body').innerHTML = html;
            openDialog('history-dialog');
        }).catch(function () { message('That history could not be loaded. Please try again.'); });
    }

    // ---- actions ------------------------------------------------------------

    document.addEventListener('click', function (e) {
        var close = e.target.closest('[data-close]');
        if (close) { closeDialog(close.closest('[role="dialog"]')); return; }
        if (e.target.classList && e.target.getAttribute('role') === 'dialog') { closeDialog(e.target); return; }

        var range = e.target.closest('#rank-dialog-range button');
        if (range) { rankDays = parseInt(range.dataset.days, 10); openRankHistory(rankTerm); return; }

        var add = e.target.closest('.rival-add');
        if (add) {
            add.disabled = true;
            post(page.dataset.addRivalUrl, {
                track_id: parseInt(add.dataset.trackId, 10), name: add.dataset.name,
                icon_url: add.dataset.icon, seller_name: add.dataset.seller,
            }).then(function (res) {
                if (!res.ok) { add.disabled = false; message(res.data.error || 'That did not go through.'); return; }
                add.textContent = 'Added';
                message(res.data.name + ' is now a rival. Its ranks show at once; its ratings, updates and reviews arrive with the check that just started.');
            });
            return;
        }
        var sorter = e.target.closest('#rival-keywords th[data-sort]');
        if (sorter) { sortOn(sorter.dataset.sort); return; }

        var changes = e.target.closest('.rival-changes');
        if (changes) { openAppHistory(changes.dataset.trackId); return; }

        if (e.target.closest('.rival-scores')) return;
        var row = e.target.closest('.rival-row');
        if (row) { openRankHistory(row.dataset.term); return; }

        if (e.target.closest('#rival-refresh')) {
            var button = el('rival-refresh');
            button.disabled = true;
            post(page.dataset.refreshUrl).then(function (res) {
                button.disabled = false;
                if (res.ok && window.ActivityIndicator && ActivityIndicator.announce) {
                    // The note that flies into the Activity pill says what started.
                    var picker = window.CountryPicker && CountryPicker.get('rival-app-switch');
                    var picked = picker ? picker.item(picker.getSelected()[0]) : null;
                    var name = picked ? picked.label : '';
                    ActivityIndicator.announce({queued: false, label: 'Rival Tracker' + (name ? ': ' + name : ''), eta: null});
                } else {
                    message(res.data.message || res.data.error || '');
                }
                check();
            });
            return;
        }
        if (e.target.closest('#rival-summarize')) {
            var summarize = el('rival-summarize');
            summarize.disabled = true;
            summarize.textContent = 'Summarizing…';
            post(page.dataset.summaryUrl, {country: country}).then(function (res) {
                if (!res.ok) {
                    summarize.disabled = false;
                    summarize.textContent = 'Summarize now';
                    message(res.data.error || 'That did not go through.');
                }
            });
        }
    });

    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') {
            Array.prototype.forEach.call(document.querySelectorAll('[role="dialog"]:not(.hidden)'), closeDialog);
        }
        if (e.key === 'Enter' && e.target.classList && e.target.classList.contains('rival-row')) {
            openRankHistory(e.target.dataset.term);
        }
        if ((e.key === 'Enter' || e.key === ' ') && e.target.matches && e.target.matches('#rival-keywords th[data-sort]')) {
            e.preventDefault();
            sortOn(e.target.dataset.sort);
        }
    });

    document.addEventListener('change', function (e) {
        if (e.target.id === 'rival-ahead-only') { aheadOnly = e.target.checked; applyAheadFilter(); }
        // The app chip: each option's value is the page (or setup) to open.
    });

    // The app switcher: each app opens its page, or its setup when not followed.
    if (window.CountryPicker && CountryPicker.get('rival-app-switch')) {
        CountryPicker.onChange('rival-app-switch', function (values) {
            if (values[0]) window.location.href = values[0];
        });
    }

    if (window.TimeAgo) TimeAgo.start();
    check();
    setInterval(check, 5000);
    document.addEventListener('visibilitychange', function () { if (!document.hidden) check(); });
})();
