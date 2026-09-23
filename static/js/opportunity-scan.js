/*
 * The Country Opportunity Finder.
 *
 * The scan itself is a background job on the server (aso/opportunity_scans.py)
 * that the run queue executes one country at a time. This file starts one,
 * polls it, draws the panel and the ranked table as countries come in, and
 * fetches a country's full detail only when a row is expanded.
 *
 * It used to be a loop in the page: one fetch per country with a fixed pause,
 * partial results in sessionStorage, and a warning that leaving would cancel
 * the scan. At 175 storefronts that is not a feature anyone could use.
 *
 * Generic pieces (formatting, the poll loop) come from job-polling.js, which
 * the keyword search client shares, so both pages say "about 3 minutes left"
 * the same way. Country names and flags come from country-picker.js.
 */
(function () {
    'use strict';

    var JP = window.JobPolling;
    var POLL_MS = 3000;
    var PAUSED_POLL_MS = 15000;

    var cfg = null;          // urls and tokens from the template
    var state = null;        // the last scan payload
    var rows = [];           // the ranked rows the table draws
    var poll = null;
    var heavyCache = {};     // country code -> the expanded payload
    var selected = {};       // country code -> true, for Save selected
    var hasApp = false;      // a scan without an app has no rank to show
    // The column the table is sorted by, and which way. Undeclared, strict
    // mode threw on the first header click, so no column ever sorted.
    var oppSortCol = null;
    var oppSortAsc = true;

    function fmt(n) {
        if (n >= 1000) return (n/1000).toFixed(1).replace(/\.0$/,'') + 'K';
        if (n < 1) return n.toFixed(1);
        if (n < 10) return n.toFixed(1).replace(/\.0$/,'');
        return Math.round(n).toString();
    }


    function oppBg(score) {
        if (score >= 50) return 'bg-green-900/30';
        if (score >= 35) return 'bg-emerald-900/20';
        if (score >= 20) return 'bg-yellow-900/20';
        return '';
    }

    // renderDownloadChart lives in static/js/download-chart.js


    function renderRankingTiers(tiers) {
        if (!tiers) return '';
        const tierDefs = [
            { key: 'top_5', label: 'Top 5' },
            { key: 'top_10', label: 'Top 10' },
            { key: 'top_20', label: 'Top 20' },
        ];
        const labelColors = {
            'Very Easy': 'bg-emerald-500/20 text-emerald-400',
            'Easy': 'bg-green-500/20 text-green-400',
            'Moderate': 'bg-yellow-500/20 text-yellow-400',
            'Hard': 'bg-orange-500/20 text-orange-400',
            'Very Hard': 'bg-red-500/20 text-red-400',
            'Extreme': 'bg-red-700/20 text-red-300',
        };
        function tierBarColor(score) {
            if (score <= 15) return 'bg-emerald-500';
            if (score <= 35) return 'bg-green-500';
            if (score <= 55) return 'bg-yellow-500';
            if (score <= 75) return 'bg-orange-500';
            if (score <= 90) return 'bg-red-500';
            return 'bg-red-700';
        }
        function tierTextColor(score) {
            if (score <= 15) return 'text-emerald-400';
            if (score <= 35) return 'text-green-400';
            if (score <= 55) return 'text-yellow-400';
            if (score <= 75) return 'text-orange-400';
            if (score <= 90) return 'text-red-400';
            return 'text-red-300';
        }
        let cards = '';
        for (const { key, label } of tierDefs) {
            const t = tiers[key];
            if (!t) continue;
            const score = t.tier_score || 0;
            const lc = labelColors[t.label] || 'bg-slate-500/20 text-slate-400';
            const barColor = tierBarColor(score);
            const txtColor = tierTextColor(score);
            const highlightItems = (t.highlights || []).map(h =>
                `<li class="flex items-start gap-1.5"><span class="text-slate-600 mt-0.5">•</span><span>${h}</span></li>`
            ).join('');
            cards += `
                <div class="bg-slate-900/60 border border-white/5 rounded-lg p-3">
                    <div class="flex items-center justify-between mb-1">
                        <div class="flex items-center gap-2">
                            <span class="text-sm font-bold text-white">${label}</span>
                            <span class="text-lg font-bold ${txtColor}">${score}</span>
                            <span class="text-slate-600 text-xs">/100</span>
                        </div>
                        <span class="text-[10px] px-1.5 py-0.5 rounded font-semibold ${lc}">${t.label}</span>
                    </div>
                    <div class="w-full bg-slate-700/50 rounded-full h-1.5 mb-2.5">
                        <div class="h-1.5 rounded-full ${barColor}" style="width:${Math.min(score,100)}%"></div>
                    </div>
                    <ul class="space-y-1.5 text-xs text-slate-400">${highlightItems}</ul>
                </div>`;
        }
        if (!cards) return '';
        return `
            <div class="mb-4">
                <h4 class="text-xs font-semibold text-slate-300 uppercase tracking-wide mb-2">How Hard Is It to Rank?</h4>
                <div class="grid grid-cols-1 md:grid-cols-3 gap-3">${cards}</div>
            </div>`;
    }

    function sortOppTable(colIndex, type, th) {
        const tbody = document.getElementById('opp-tbody');
        if (!tbody) return;

        const dataRows = [];
        const allRows = Array.from(tbody.children);
        for (let i = 0; i < allRows.length; i++) {
            const row = allRows[i];
            if (row.classList.contains('opp-detail-row')) continue;
            const next = allRows[i + 1];
            const detailRow = (next && next.classList.contains('opp-detail-row')) ? next : null;
            dataRows.push({ row, detailRow });
        }

        if (oppSortCol === colIndex) {
            oppSortAsc = !oppSortAsc;
        } else {
            oppSortCol = colIndex;
            oppSortAsc = true;
        }

        document.querySelectorAll('#opp-table .sort-indicator').forEach(s => s.textContent = '');
        const indicator = th.querySelector('.sort-indicator');
        if (indicator) indicator.textContent = oppSortAsc ? ' ▲' : ' ▼';

        dataRows.sort((a, b) => {
            const cellA = a.row.children[colIndex];
            const cellB = b.row.children[colIndex];
            // A cell that carries more than its number (the line under a
            // score, "not ranked") says what it sorts by; reading its whole
            // text sorted "8" with "~#13 on average" under it as 813.
            let valA = (cellA ? (cellA.dataset.sortValue ?? cellA.textContent) : '').trim();
            let valB = (cellB ? (cellB.dataset.sortValue ?? cellB.textContent) : '').trim();
            if (type === 'num') {
                const isEmptyA = !valA || valA === '—' || valA === 'N/A';
                const isEmptyB = !valB || valB === '—' || valB === 'N/A';
                if (isEmptyA && !isEmptyB) return 1;
                if (!isEmptyA && isEmptyB) return -1;
                if (isEmptyA && isEmptyB) return 0;
                const numA = parseFloat(valA.replace(/[^0-9.\-]/g, '')) || 0;
                const numB = parseFloat(valB.replace(/[^0-9.\-]/g, '')) || 0;
                return oppSortAsc ? numA - numB : numB - numA;
            } else {
                valA = valA.toLowerCase();
                valB = valB.toLowerCase();
                if (valA < valB) return oppSortAsc ? -1 : 1;
                if (valA > valB) return oppSortAsc ? 1 : -1;
                return 0;
            }
        });

        dataRows.forEach(({ row, detailRow }) => {
            tbody.appendChild(row);
            if (detailRow) tbody.appendChild(detailRow);
        });
    }

    // ---- the scan panel -------------------------------------------------

    function statusTitle(scan) {
        if (scan.status === 'queued') {
            var waiting = scan.waiting_for ? ' behind ' + JP.esc(scan.waiting_for) : '';
            return 'Queued' + waiting;
        }
        if (scan.status === 'running') { return 'Scanning ' + JP.esc(scan.keyword); }
        if (scan.status === 'paused') { return 'Paused'; }
        if (scan.status === 'failed') { return 'Stopped'; }
        if (scan.status === 'cancelled') { return 'Stopped early'; }
        return 'Scan finished: ' + JP.esc(scan.keyword);
    }

    function statusLine(scan) {
        var parts = [];
        parts.push(JP.fmt(scan.done_count) + ' of ' + JP.fmt(scan.total_countries) +
                   ' ' + JP.plural(scan.total_countries, 'country', 'countries'));
        if (scan.status === 'running' && scan.current_country_name) {
            parts.push('now ' + JP.esc(scan.current_country_name));
        }
        if (scan.eta_seconds) { parts.push(JP.etaText(scan.eta_seconds)); }
        if (scan.failed_count) {
            parts.push(JP.fmt(scan.failed_count) + ' could not be checked');
        }
        return parts.join(' · ');
    }

    function renderPanel(scan) {
        JP.toggle('opp-panel', !!scan);
        if (!scan) { return; }
        JP.setText('osp-title', statusTitle(scan));
        JP.setText('osp-line', statusLine(scan));
        JP.setText('osp-message', scan.progress_message || '');
        var bar = JP.byId('osp-bar');
        if (bar) { bar.style.width = (scan.progress_percent || 0) + '%'; }

        var running = scan.status === 'running';
        var queued = scan.status === 'queued';
        var paused = scan.status === 'paused' || scan.status === 'failed';
        var done = scan.status === 'completed' || scan.status === 'cancelled';

        JP.toggle('osp-spinner', running);
        JP.toggle('osp-pause', running);
        JP.toggle('osp-resume', paused);
        JP.toggle('osp-discard', paused || queued);
        JP.toggle('osp-dismiss', done);
        JP.toggle('osp-retry', done && scan.failed_count > 0);
        JP.toggle('osp-progress-wrap', !done);

        var note = '';
        if (queued && scan.waiting_for) {
            note = 'Waiting for ' + JP.esc(scan.waiting_for) + '.';
            if (scan.queue_position) { note += ' Position ' + scan.queue_position + ' in the queue.'; }
        } else if (running) {
            note = scan.is_native
                ? 'Keep working elsewhere in RespectASO. Quitting is fine too, the scan picks up where it left off.'
                : 'You can leave this page. The scan keeps running.';
        } else if (scan.auto_resume && scan.yielded_for) {
            note = 'Stepped aside for ' + JP.esc(scan.yielded_for) + ', and comes back by itself.';
        } else if (scan.restart_resumes) {
            note = 'Picked up where it left off after RespectASO was closed.';
        } else if (paused) {
            note = 'Paused. The countries already scanned are kept.';
        }
        JP.setText('osp-note', note);
        JP.setText('osp-failed', scan.failed_text || '');
        JP.toggle('osp-failed-wrap', !!scan.failed_text);
        JP.setText('osp-error', scan.error_message || '');
        JP.toggle('osp-error-wrap', !!scan.error_message);
    }

    // ---- the ranked table -----------------------------------------------

    function rowHtml(r, index) {
        var rank = index + 1;
        var flag = r.country_flag || '';
        var popularity = (r.popularity === null || r.popularity === undefined)
            ? '<span class="text-slate-500">N/A</span>'
            : formatPopularityCell(r);
        return '<tr class="border-b border-white/5 hover:bg-white/5 cursor-pointer transition-colors" ' +
            'data-country="' + JP.esc(r.country) + '" data-index="' + index + '">' +
            '<td class="px-3 py-2 text-center" onclick="event.stopPropagation()">' +
            '<input type="checkbox" class="opp-row-cb rounded border-white/20 bg-slate-600 text-purple-500 focus:ring-purple-500 focus:ring-offset-0" ' +
            'value="' + JP.esc(r.country) + '"' + (selected[r.country] ? ' checked' : '') + '></td>' +
            '<td class="px-3 py-2 text-center text-slate-500 text-xs">' + rank + '</td>' +
            '<td class="px-3 py-2 text-white whitespace-nowrap">' + flag + ' ' + JP.esc(r.country_name) +
            '<span class="text-slate-500 text-xs ml-1">' + JP.esc((r.country || '').toUpperCase()) + '</span></td>' +
            '<td class="px-3 py-2 text-center bg-cyan-500/5" data-sort-value="' + r.opportunity + '"><span class="' +
            JP.esc(r.opportunity_css || 'text-slate-400') + ' text-base font-bold cursor-help" data-tip="' +
            JP.esc(r.opportunity_tip || '') + '">' + r.opportunity + '</span></td>' +
            '<td class="px-3 py-2 text-center">' + popularity + '</td>' +
            '<td class="px-3 py-2 text-center"><span class="' + JP.esc(r.difficulty_color) + '">' +
            r.difficulty + '</span><span class="text-slate-600 text-xs ml-1">' +
            JP.esc(r.difficulty_label) + '</span></td>' +
            atFirstCell(r) +
            '<td class="px-3 py-2" data-sort-value="' + window.ClassificationBadge.rank(r.classification) + '">' +
            window.ClassificationBadge.chipHtml(r.classification, r.classification_tip) + '</td>' +
            '<td class="px-3 py-2 text-center text-slate-400">' + JP.fmt(r.competitor_count) + '</td>' +
            '<td class="px-3 py-2 text-slate-400 text-xs max-w-[14rem] truncate">' +
            JP.esc(r.top_competitor || '') + '</td>' +
            (hasApp ? '<td class="px-3 py-2 text-center text-slate-400" data-sort-value="' + (r.app_rank || '') + '">' +
            (r.app_rank ? '#' + r.app_rank : '<span class="text-slate-600">not ranked</span>') + '</td>' : '') +
            '</tr>';
    }

    // What #1 pays here, as every Downloads at #1 column shows it. Sorted
    // by the high end, like the other tables.
    function atFirstCell(r) {
        var range = r.downloads_at_first || [0, 0];
        return '<td class="px-3 py-2 text-center text-slate-300 whitespace-nowrap font-mono text-xs" data-sort-value="' +
            range[1] + '">' + fmt(range[0]) + '\u2013' + fmt(range[1]) +
            '<span class="text-slate-500">/day</span></td>';
    }

    function renderBest(r) {
        var box = JP.byId('opp-best');
        if (!box) { return; }
        if (!r) { box.classList.add('hidden'); return; }
        box.classList.remove('hidden');
        box.innerHTML =
            '<p class="text-[10px] uppercase tracking-wide text-green-400/70 mb-1">Best opportunity</p>' +
            '<p class="text-2xl font-bold text-white">' + (r.country_flag || '') + ' ' +
            JP.esc(r.country_name) + '</p>' +
            '<p class="text-sm text-slate-300 mt-1">Opportunity <span class="' +
            JP.esc(r.opportunity_css || 'text-slate-400') + ' font-semibold cursor-help" data-tip="' +
            JP.esc(r.opportunity_tip || '') + '">' + r.opportunity + '</span>' +
            ' · Popularity ' + (r.popularity === null ? 'N/A' : r.popularity) +
            ' · Difficulty ' + r.difficulty + ' (' + JP.esc(r.difficulty_label) + ')</p>';
    }

    function renderCoverageNote(list) {
        var el = JP.byId('opp-coverage');
        if (!el) { return; }
        el.innerHTML = (window.formatCoverageAdvisory
            ? window.formatCoverageAdvisory(list) : '');
    }

    // The line under the score, the same the Dashboard draws: whose rank,
    // and on hover why. Composed on the server; drawn here.
    function renderResults(scan) {
        var scoredFor = JP.byId('opp-scored-for');
        if (scoredFor) {
            scoredFor.textContent = scan.scored_for || '';
            scoredFor.classList.toggle('hidden', !scan.scored_for);
        }
        // The words under the Opportunity heading name the scan's app.
        document.querySelectorAll('.opp-subline').forEach(function (el) {
            if (scan.opportunity_subline) el.textContent = scan.opportunity_subline;
        });
        // Your rank is a column only when the scan names an app: without
        // one there is no app whose rank it could show.
        hasApp = !!scan.app_id;
        JP.toggle('opp-rank-head', hasApp);
        rows = scan.results || [];
        var tbody = JP.byId('opp-tbody');
        if (!tbody) { return; }
        tbody.innerHTML = rows.map(rowHtml).join('');
        JP.toggle('opp-results', rows.length > 0);
        JP.toggle('opp-actions', rows.length > 0);
        renderBest(scan.best);
        renderCoverageNote(rows);
        JP.setText('opp-count', rows.length + ' ' +
                   JP.plural(rows.length, 'country', 'countries') + ' scanned');
        updateSelectedButton();
    }

    // ---- expanding a row -------------------------------------------------

    function expand(rowEl, index) {
        var next = rowEl.nextElementSibling;
        if (next && next.classList.contains('opp-detail-row')) {
            next.classList.toggle('hidden');
            rowEl.classList.toggle('bg-purple-900/10');
            return;
        }
        var light = rows[index];
        if (!light) { return; }
        var cached = heavyCache[light.country];
        if (cached) { drawDetail(rowEl, cached); return; }
        fetch(JP.urlFor(cfg.countryUrl, cfg.scanId).replace('__code__', light.country))
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (!data.country) { return; }
                heavyCache[light.country] = data.country;
                drawDetail(rowEl, data.country);
            });
    }

    function drawDetail(rowEl, r) {
        // The heavy payload names the two big columns differently from the
        // old client-side shape; map them once here so the markup below is
        // the same one this page has always drawn.
        r.difficulty_breakdown = r.difficulty_breakdown || {};
        r.competitors_data = r.competitors || [];
        lastKeyword = state ? state.keyword : '';
        const tr = document.createElement('tr');
        tr.className = 'opp-detail-row border-b border-white/5';
        const td = document.createElement('td');
        td.colSpan = hasApp ? 11 : 10;
        td.className = 'px-4 py-4 bg-slate-800/50';

        // Targeting advice
        const adviceHtml = window.ClassificationBadge.render(r.targeting);

        // Ranking tiers
        const tiersHtml = renderRankingTiers(r.difficulty_breakdown?.ranking_tiers);

        // Download estimates chart
        const dlHtml = window.renderDownloadChart(r.difficulty_breakdown?.download_estimates, r.app_rank);

        // Score breakdown details, drawn by the shared renderer.
        const bd = r.difficulty_breakdown || {};
        const subScoreHtml = window.DifficultyFactors.gridHtml(r.difficulty_breakdown, 'mb-4');

        // Competitor table
        const competitors = r.competitors_data || [];
        let compRows = '';
        if (competitors.length) {
            compRows = competitors.map((app, i) => {
                const stars = app.averageUserRating ? '⭐ ' + app.averageUserRating.toFixed(1) : '—';
                const ratings = app.userRatingCount ? app.userRatingCount.toLocaleString() : '0';
                const releaseDate = app.releaseDate ? new Date(app.releaseDate).toLocaleDateString('en-US', {month:'short', year:'numeric'}) : '—';
                const updatedDate = (app.currentVersionReleaseDate || app.releaseDate) ? new Date(app.currentVersionReleaseDate || app.releaseDate).toLocaleDateString('en-US', {month:'short', year:'numeric'}) : '—';
                const highlighted = highlightKeyword(app.trackName, lastKeyword);
                return `
                    <tr class="border-b border-white/5 hover:bg-white/5 transition-colors">
                        <td class="px-2 py-1.5 text-slate-500 text-sm whitespace-nowrap">${i + 1}</td>
                        <td class="px-2 py-1.5">
                            <div class="flex items-center gap-2">
                                ${app.artworkUrl100 ? `<img src="${app.artworkUrl100}" alt="" class="w-7 h-7 rounded-lg flex-shrink-0" onerror="this.style.display='none'">` : ''}
                                <div class="min-w-0">
                                    <a href="${app.trackViewUrl}" target="_blank" rel="noopener" class="text-white text-sm hover:text-purple-400 transition-colors">${highlighted}</a>
                                    <span class="text-slate-500 block text-xs">${app.sellerName || ''}</span>
                                </div>
                            </div>
                        </td>
                        <td class="px-2 py-1.5 text-sm text-slate-300 whitespace-nowrap">${stars}</td>
                        <td class="px-2 py-1.5 text-sm text-slate-300 whitespace-nowrap">${ratings}</td>
                        <td class="px-2 py-1.5 text-sm text-slate-400 whitespace-nowrap">${app.primaryGenreName || ''}</td>
                        <td class="px-2 py-1.5 text-sm text-slate-400 whitespace-nowrap">${app.formattedPrice || ''}</td>
                        <td class="px-2 py-1.5 text-sm text-slate-500 whitespace-nowrap">${releaseDate}</td>
                        <td class="px-2 py-1.5 text-sm text-slate-500 whitespace-nowrap">${updatedDate}</td>
                    </tr>`;
            }).join('');
        }

        const compTableHtml = competitors.length ? `
            <div class="overflow-x-auto">
                <h4 class="text-xs font-medium text-slate-400 mb-2">Top ${competitors.length} Competitors</h4>
                <table class="w-full text-sm text-left">
                    <thead>
                        <tr class="border-b border-white/10">
                            <th class="px-2 py-1.5 text-xs text-slate-500 whitespace-nowrap">#</th>
                            <th class="px-2 py-1.5 text-xs text-slate-500">App</th>
                            <th class="px-2 py-1.5 text-xs text-slate-500 whitespace-nowrap">Rating</th>
                            <th class="px-2 py-1.5 text-xs text-slate-500 whitespace-nowrap">Ratings</th>
                            <th class="px-2 py-1.5 text-xs text-slate-500 whitespace-nowrap">Genre</th>
                            <th class="px-2 py-1.5 text-xs text-slate-500 whitespace-nowrap">Price</th>
                            <th class="px-2 py-1.5 text-xs text-slate-500 whitespace-nowrap">Released</th>
                            <th class="px-2 py-1.5 text-xs text-slate-500 whitespace-nowrap">Updated</th>
                        </tr>
                    </thead>
                    <tbody>${compRows}</tbody>
                </table>
            </div>` : '';

        // Insights
        const insights = bd.insights || [];
        let insightsHtml = '';
        if (insights.length) {
            insightsHtml = '<div class="space-y-1.5 mb-4">' + insights.map(ins =>
                `<div class="flex items-start gap-2 text-xs ${ins.type === 'barrier' ? 'text-red-400' : ins.type === 'opportunity' ? 'text-green-400' : 'text-slate-400'}">
                    <span class="flex-shrink-0">${ins.icon}</span><span>${ins.text}</span>
                </div>`
            ).join('') + '</div>';
        }

        // Opportunity signals
        const oppSignals = bd.opportunity_signals || [];
        let oppSigHtml = '';
        if (oppSignals.length) {
            oppSigHtml = `<div class="bg-green-900/20 border border-green-500/20 rounded-lg p-3 mb-4">
                <p class="text-xs font-medium text-green-400 mb-2">Opportunity Signals</p>
                <div class="space-y-2">${oppSignals.map(sig =>
                    `<div class="flex items-start gap-2 text-xs">
                        <span class="flex-shrink-0">${sig.icon}</span>
                        <div><span class="text-green-300 font-medium">${sig.signal}</span>
                        <span class="text-slate-500 ml-1">(${sig.strength})</span>
                        <p class="text-slate-400 mt-0.5">${sig.detail}</p></div>
                    </div>`
                ).join('')}</div>
            </div>`;
        }

        td.innerHTML = `
            <div class="flex flex-col lg:flex-row gap-4">
                <div class="lg:w-1/3 space-y-2">
                    <div class="bg-slate-800 rounded-lg p-2.5">
                        <p class="text-xs text-slate-400 mb-0.5 uppercase tracking-wide">Popularity</p>
                        <span class="text-2xl font-bold text-purple-400">${r.popularity ?? 'N/A'}</span>
                        ${r.popularity ? '<span class="text-slate-500 text-xs ml-1">/ 100</span> ' + formatPopularityBadge(r) : ''}
                    </div>
                    <div class="bg-slate-800 rounded-lg p-2.5">
                        <p class="text-xs text-slate-400 mb-0.5 uppercase tracking-wide">Overall Difficulty</p>
                        <span class="text-2xl font-bold ${r.difficulty_color}">${r.difficulty}</span>
                        <span class="text-slate-500 text-xs ml-1">/ 100</span>
                        <p class="${r.difficulty_color} text-xs mt-0.5 font-medium">${r.difficulty_label}</p>
                    </div>
                    ${r.app_rank ? `<div class="bg-yellow-900/20 border border-yellow-500/20 rounded-lg p-2.5">
                        <p class="text-xs text-slate-400 mb-0.5 uppercase tracking-wide">Your App Rank</p>
                        <span class="text-2xl font-bold text-yellow-400">#${r.app_rank}</span>
                    </div>` : ''}
                    ${adviceHtml}
                </div>
                <div class="flex-1 overflow-x-auto">
                    ${tiersHtml}
                    ${dlHtml}
                    <details class="mb-4">
                        <summary class="text-xs text-slate-500 cursor-pointer hover:text-slate-300 transition-colors">Difficulty breakdown &amp; insights</summary>
                        <div class="mt-2 space-y-3">
                            ${insightsHtml}
                            ${oppSigHtml}
                            ${subScoreHtml}
                        </div>
                    </details>
                    ${compTableHtml}
                </div>
            </div>`;

        tr.appendChild(td);
        rowEl.after(tr);
        rowEl.classList.add('bg-purple-900/10');
    }

    var lastKeyword = '';

    // ---- actions ---------------------------------------------------------

    function updateSelectedButton() {
        var n = Object.keys(selected).filter(function (c) { return selected[c]; }).length;
        var btn = JP.byId('add-selected-btn');
        if (!btn) { return; }
        btn.disabled = n === 0;
        btn.classList.toggle('opacity-50', n === 0);
        btn.classList.toggle('cursor-not-allowed', n === 0);
        JP.setText('add-selected-text', n ? 'Save ' + n + ' to history' : 'Save selected to history');
    }

    function save(body) {
        return fetch(cfg.saveUrl, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': cfg.csrfToken },
            body: JSON.stringify(Object.assign({ scan_id: cfg.scanId }, body))
        }).then(function (r) { return r.json().then(function (d) {
            if (!r.ok) { throw new Error(d.error || 'Could not save'); }
            return d;
        }); }).then(function (data) {
            var banner = JP.byId('opp-saved-banner');
            JP.setText('opp-saved-text', data.saved + ' ' +
                JP.plural(data.saved, 'country', 'countries') + ' saved to Search History.');
            if (banner) { banner.classList.remove('hidden'); }
        }).catch(function (err) {
            showAlert(err.message);
        });
    }

    function showAlert(message) {
        if (window.showConfirm) {
            window.showConfirm(message, { type: 'alert', title: 'Country scan' });
        } else {
            window.alert(message);
        }
    }

    function act(url, fields) {
        return JP.post(url, fields || {}, cfg.csrfToken)
            .then(function () { return poll.refresh(); })
            .catch(function (err) { showAlert(err.message); });
    }

    function exportCsv() {
        var header = ['Rank', 'Country', 'Code', 'Region', 'Opportunity', 'Popularity',
                      'Difficulty', 'Difficulty label', 'Classification', 'Competitors',
                      'Top competitor'];
        if (hasApp) { header.push('Your rank'); }
        var lines = [header.join(',')];
        rows.forEach(function (r, i) {
            lines.push([
                i + 1, '"' + (r.country_name || '').replace(/"/g, '""') + '"',
                (r.country || '').toUpperCase(), '"' + (r.region || '') + '"',
                r.opportunity, r.popularity === null ? '' : r.popularity,
                r.difficulty, '"' + r.difficulty_label + '"', '"' + r.classification + '"',
                r.competitor_count, '"' + (r.top_competitor || '').replace(/"/g, '""') + '"'
            ].concat(hasApp ? [r.app_rank || ''] : []).join(','));
        });
        var csv = lines.join('\n');
        var name = 'opportunity-' + (state ? state.keyword.replace(/[^a-z0-9]+/gi, '-') : 'scan') + '.csv';
        if (window.pywebview && window.pywebview.api && window.pywebview.api.save_file) {
            window.pywebview.api.save_file(name, csv);
            return;
        }
        var blob = new Blob([csv], { type: 'text/csv' });
        var link = document.createElement('a');
        link.href = URL.createObjectURL(blob);
        link.download = name;
        link.click();
        URL.revokeObjectURL(link.href);
    }

    // ---- wiring ----------------------------------------------------------

    function apply(data) {
        var scan = data.scan || data.finished || null;
        state = scan;
        cfg.scanId = scan ? scan.id : null;
        renderPanel(scan);
        if (scan) { renderResults(scan); }
        var startBtn = JP.byId('opp-btn');
        if (startBtn) {
            var busy = scan && (scan.status === 'running' || scan.status === 'queued');
            startBtn.disabled = !!busy;
            startBtn.classList.toggle('opacity-50', !!busy);
            startBtn.classList.toggle('cursor-not-allowed', !!busy);
        }
    }

    function intervalFor(data) {
        var scan = data && (data.scan || data.finished);
        if (!scan) { return 0; }
        if (scan.status === 'running' || scan.status === 'queued') { return POLL_MS; }
        if (scan.status === 'paused' || scan.status === 'failed') { return PAUSED_POLL_MS; }
        return 0;
    }

    function updateCost() {
        var picker = window.CountryPicker && CountryPicker.get('scan-countries');
        if (!picker) { return; }
        var n = picker.getSelected().length;
        var seconds = n * (cfg.secondsPerCountry || 3.6);
        var label = JP.byId('opp-btn-text');
        if (label) {
            label.textContent = n
                ? 'Scan ' + n + ' ' + JP.plural(n, 'country', 'countries') +
                  (seconds ? ' (' + JP.durationText(seconds) + ')' : '')
                : 'Pick countries to scan';
        }
    }

    function init(options) {
        cfg = options;
        poll = JP.poller({
            url: cfg.currentUrl,
            intervalFor: intervalFor,
            onData: apply
        });

        var form = JP.byId('opp-form');
        if (form) {
            form.addEventListener('submit', function (e) {
                e.preventDefault();
                var body = new FormData(form);
                var appSelect = JP.byId('opp-app');
                if (appSelect) { body.set('app_id', appSelect.value); }
                fetch(cfg.startUrl, {
                    method: 'POST',
                    headers: { 'X-CSRFToken': cfg.csrfToken },
                    body: body
                }).then(function (r) { return r.json().then(function (d) {
                    if (!r.ok) { throw new Error(d.error || 'Could not start the scan'); }
                    return d;
                }); }).then(function () {
                    heavyCache = {};
                    selected = {};
                    poll.start();
                }).catch(function (err) { showAlert(err.message); });
            });
        }

        var tbody = JP.byId('opp-tbody');
        if (tbody) {
            tbody.addEventListener('click', function (e) {
                var row = e.target.closest('tr[data-index]');
                if (!row) { return; }
                expand(row, parseInt(row.dataset.index, 10));
            });
            tbody.addEventListener('change', function (e) {
                if (!e.target.classList.contains('opp-row-cb')) { return; }
                selected[e.target.value] = e.target.checked;
                updateSelectedButton();
            });
        }

        var all = JP.byId('opp-select-all');
        if (all) {
            all.addEventListener('change', function () {
                rows.forEach(function (r) { selected[r.country] = all.checked; });
                document.querySelectorAll('.opp-row-cb').forEach(function (cb) {
                    cb.checked = all.checked;
                });
                updateSelectedButton();
            });
        }

        var actions = {
            'osp-pause': function () { return act(JP.urlFor(cfg.pauseUrl, cfg.scanId)); },
            'osp-resume': function () { return act(JP.urlFor(cfg.resumeUrl, cfg.scanId)); },
            'osp-discard': function () { return act(JP.urlFor(cfg.discardUrl, cfg.scanId)); },
            'osp-retry': function () { return act(JP.urlFor(cfg.retryUrl, cfg.scanId)); },
            'osp-dismiss': function () { return act(JP.urlFor(cfg.dismissUrl, cfg.scanId)); },
            'add-selected-btn': function () {
                var codes = Object.keys(selected).filter(function (c) { return selected[c]; });
                return codes.length ? save({ countries: codes }) : null;
            },
            'add-all-btn': function () { return save({ all: true }); },
            'opp-export-btn': function () { exportCsv(); }
        };
        Object.keys(actions).forEach(function (id) {
            var el = JP.byId(id);
            if (el) { el.addEventListener('click', function (e) { e.preventDefault(); actions[id](); }); }
        });

        if (window.CountryPicker) {
            CountryPicker.onChange('scan-countries', updateCost);
        }
        updateCost();

        if (cfg.bootstrap) { apply({ scan: cfg.bootstrap }); }
        poll.start();

        // A pre-upgrade session may still hold a half finished scan here.
        // The row is the only state now, so clear it once and never again.
        try {
            sessionStorage.removeItem('opp_results');
            sessionStorage.removeItem('opp_keyword');
            sessionStorage.removeItem('opp_app_id');
        } catch (e) { /* private mode */ }
    }

    function initWhenReady(options) {
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', function () { init(options); });
        } else {
            init(options);
        }
    }

    window.OpportunityScan = { init: initWhenReady, sortTable: sortOppTable };
    window.sortOppTable = sortOppTable;   // the table headers call it inline
})();
