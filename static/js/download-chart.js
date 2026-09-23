/*
 * The estimated-downloads-by-position chart.
 *
 * One renderer, loaded by the two pages that draw it: the dashboard's
 * expanded keyword row and the Country Opportunity Finder. It used to be
 * copied into both, and the copies drifted: the honest "under 1 search a day"
 * answer reached one of them and not the other, so the dashboard kept drawing
 * twenty bars of 0.0 for storefronts with no searches.
 *
 * Twin of aso_tags.download_cell and formatDownloadCell in ai-tabs-shared.js
 * for the small-market case, per scoring-consistency.instructions.md.
 */
(function (global) {
    'use strict';

    function fmt(n) {
        if (n >= 1000) return (n / 1000).toFixed(1).replace(/\.0$/, '') + 'K';
        if (n < 1) return n.toFixed(1);
        if (n < 10) return n.toFixed(1).replace(/\.0$/, '');
        return Math.round(n).toString();
    }

    function renderDownloadChart(estimates, appRank) {

    if (!estimates || !estimates.positions) return '';
    if (estimates.below_threshold) {
        // A market this small sees under one search a day for the keyword.
        // Twenty bars of 0.0 would read as a broken chart rather than as
        // the fact it is, so say the fact. Same rule as the download cell
        // in aso_tags.download_cell and ai-tabs-shared.js.
        return '<div class="mb-4">' +
            '<h4 class="text-xs font-medium text-slate-400 mb-2">Estimated downloads by position</h4>' +
            '<div class="bg-slate-900/60 border border-white/5 rounded-xl p-4">' +
            '<p class="text-sm text-slate-300">Under 1 search a day in this storefront.</p>' +
            '<p class="text-xs text-slate-500 mt-1 leading-relaxed">This keyword is too quiet here for a ' +
            'download range to mean anything. The difficulty and the competitor list above still describe ' +
            'the market.</p>' +
            '</div></div>';
    }
    const positions = estimates.positions;
    const maxDl = Math.max(...positions.map(p => p.downloads_high), 1);
    const dailySearches = estimates.daily_searches || 0;

    const W = 680, H = 220, PAD_L = 55, PAD_R = 15, PAD_T = 25, PAD_B = 35;
    const chartW = W - PAD_L - PAD_R;
    const chartH = H - PAD_T - PAD_B;
    const barW = Math.floor(chartW / 20) - 3;
    const barGap = Math.floor(chartW / 20);

    let yTicks = [];
    const yStep = maxDl / 4;
    for (let i = 0; i <= 4; i++) {
        const val = Math.round(yStep * i);
        const y = PAD_T + chartH - (chartH * val / maxDl);
        yTicks.push(`<text x="${PAD_L - 8}" y="${y + 3}" fill="rgba(148,163,184,0.7)" text-anchor="end" font-size="10">${fmt(val)}</text>`);
        yTicks.push(`<line x1="${PAD_L}" x2="${W - PAD_R}" y1="${y}" y2="${y}" stroke="rgba(255,255,255,0.05)" stroke-dasharray="3,3"/>`);
    }

    const zoneX1 = PAD_L, zoneX2 = PAD_L + 5 * barGap, zoneX3 = PAD_L + 10 * barGap, zoneX4 = PAD_L + 20 * barGap;
    const zones = `
        <rect x="${zoneX1}" y="${PAD_T}" width="${zoneX2 - zoneX1}" height="${chartH}" fill="rgba(139,92,246,0.06)" rx="4"/>
        <rect x="${zoneX2}" y="${PAD_T}" width="${zoneX3 - zoneX2}" height="${chartH}" fill="rgba(59,130,246,0.04)" rx="4"/>
        <rect x="${zoneX3}" y="${PAD_T}" width="${zoneX4 - zoneX3}" height="${chartH}" fill="rgba(100,116,139,0.03)" rx="4"/>`;
    const zoneLabels = `
        <text x="${(zoneX1 + zoneX2) / 2}" y="${PAD_T + 11}" font-size="9" fill="rgba(167,139,250,0.6)" text-anchor="middle" font-weight="600">TOP 5</text>
        <text x="${(zoneX2 + zoneX3) / 2}" y="${PAD_T + 11}" font-size="9" fill="rgba(96,165,250,0.5)" text-anchor="middle" font-weight="600">TOP 6\u201310</text>
        <text x="${(zoneX3 + zoneX4) / 2}" y="${PAD_T + 11}" font-size="9" fill="rgba(148,163,184,0.4)" text-anchor="middle" font-weight="600">TOP 11\u201320</text>`;

    let bars = '';
    positions.forEach((p, i) => {
        const x = PAD_L + i * barGap + (barGap - barW) / 2;
        const hHigh = (p.downloads_high / maxDl) * chartH;
        const hLow = (p.downloads_low / maxDl) * chartH;
        const yHigh = PAD_T + chartH - hHigh;
        const yLow = PAD_T + chartH - hLow;
        const hRange = hHigh - hLow;
        const isAppPos = appRank && appRank === p.pos;
        let barColor, solidOpacity, rangeOpacity;
        if (p.pos <= 5) { barColor = 'rgb(139,92,246)'; solidOpacity = isAppPos ? 1 : 0.8; rangeOpacity = isAppPos ? 0.35 : 0.22; }
        else if (p.pos <= 10) { barColor = 'rgb(59,130,246)'; solidOpacity = isAppPos ? 1 : 0.7; rangeOpacity = isAppPos ? 0.3 : 0.18; }
        else { barColor = 'rgb(100,116,139)'; solidOpacity = isAppPos ? 0.9 : 0.5; rangeOpacity = isAppPos ? 0.25 : 0.12; }

        // Optimistic range segment (downloads_low → downloads_high) — lighter top portion
        if (hRange > 0.5) {
            bars += `<rect x="${x}" y="${yHigh}" width="${barW}" height="${hRange}" fill="${barColor}" opacity="${rangeOpacity}" rx="2"/>`;
        }
        // Conservative base segment (0 → downloads_low) — solid bottom portion
        if (hLow > 0.5) {
            bars += `<rect x="${x}" y="${yLow}" width="${barW}" height="${hLow}" fill="${barColor}" opacity="${solidOpacity}" rx="2"/>`;
        }

        // Download value label above bar for key positions
        if (p.downloads_high > 0 && (p.pos <= 5 || p.pos === 10 || p.pos === 15 || p.pos === 20 || isAppPos)) {
            const labelY = yHigh - (isAppPos ? 16 : 5);
            bars += `<text x="${x + barW/2}" y="${labelY}" font-size="7.5" fill="rgba(255,255,255,0.5)" text-anchor="middle">${fmt(p.downloads_low)}\u2013${fmt(p.downloads_high)}</text>`;
        }

        // "YOU" marker for your app position
        if (isAppPos) {
            bars += `<rect x="${x - 2}" y="${yHigh - 2}" width="${barW + 4}" height="${hHigh + 4}" rx="3" fill="none" stroke="rgb(250,204,21)" stroke-width="1.5" stroke-dasharray="3,2"/>`;
            bars += `<text x="${x + barW/2}" y="${yHigh - 6}" font-size="9" font-weight="700" fill="rgb(250,204,21)" text-anchor="middle">YOU</text>`;
        }

        // Position number on x-axis
        bars += `<text x="${x + barW/2}" y="${PAD_T + chartH + 14}" font-size="9" fill="${isAppPos ? 'rgb(250,204,21)' : 'rgba(148,163,184,0.7)'}" ${isAppPos ? 'font-weight="bold"' : ''} text-anchor="middle">${p.pos}</text>`;
    });

    const axisLabels = `
        <text x="${PAD_L + chartW/2}" y="${H - 2}" font-size="10" fill="rgba(148,163,184,0.7)" text-anchor="middle">Ranking Position</text>
        <text x="12" y="${PAD_T + chartH/2}" font-size="10" fill="rgba(148,163,184,0.7)" text-anchor="middle" transform="rotate(-90, 12, ${PAD_T + chartH/2})">Downloads / day</text>`;

    // Per-position tier cards (not averages — show what each position actually gets)
    function posRow(pos, p, isBold) {
        const cls = isBold ? 'font-semibold' : 'opacity-80';
        return '<div style="display:flex;justify-content:space-between;font-size:0.75rem;"><span style="color:#94a3b8;">#' + pos + '</span><span class="' + cls + '">' + fmt(p.downloads_low) + '\u2013' + fmt(p.downloads_high) + '/day</span></div>';
    }
    const p1 = positions[0], p3 = positions[2], p5 = positions[4];
    const p6 = positions[5], p8 = positions[7], p10 = positions[9];
    const p11 = positions[10], p15 = positions[14], p20 = positions[19];

    const tierCards = `
        <div class="grid grid-cols-3 gap-3 mt-3">
            <div class="bg-purple-500/10 border border-purple-500/20 rounded-lg p-2.5">
                <p class="text-[10px] text-purple-400 uppercase tracking-wide font-semibold text-center mb-1.5">Top 5 — per position</p>
                <div class="space-y-1 text-purple-300">
                    ${posRow(1, p1, true)}
                    ${posRow(3, p3, false)}
                    ${posRow(5, p5, false)}
                </div>
            </div>
            <div class="bg-blue-500/10 border border-blue-500/20 rounded-lg p-2.5">
                <p class="text-[10px] text-blue-400 uppercase tracking-wide font-semibold text-center mb-1.5">Top 6\u201310 — per position</p>
                <div class="space-y-1 text-blue-300">
                    ${posRow(6, p6, true)}
                    ${posRow(8, p8, false)}
                    ${posRow(10, p10, false)}
                </div>
            </div>
            <div class="bg-slate-500/10 border border-slate-500/20 rounded-lg p-2.5">
                <p class="text-[10px] text-slate-400 uppercase tracking-wide font-semibold text-center mb-1.5">Top 11\u201320 — per position</p>
                <div class="space-y-1 text-slate-300">
                    ${posRow(11, p11, true)}
                    ${posRow(15, p15, false)}
                    ${posRow(20, p20, false)}
                </div>
            </div>
        </div>`;

    return `
        <div class="mb-4">
            <div class="flex items-center gap-2 mb-2">
                <h4 class="text-xs font-semibold text-slate-300 uppercase tracking-wide">Estimated Downloads by Position</h4>
                <span class="relative group/dlinfo">
                    <svg class="w-3.5 h-3.5 text-slate-500 cursor-help" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
                    <span class="absolute bottom-full left-1/2 -translate-x-1/2 mb-1.5 w-72 bg-slate-900 border border-white/10 rounded-lg p-2.5 text-[10px] text-slate-300 leading-snug opacity-0 pointer-events-none group-hover/dlinfo:opacity-100 transition-opacity z-50 shadow-lg">Based on \u2248${fmt(dailySearches)} estimated daily searches for this keyword. Solid bars show the low end (5% CVR — unknown indie app), lighter extensions show the high end (20% CVR — established category leader). Where your app falls in this range depends on your ratings, screenshots, icon, and brand recognition.</span>
                </span>
            </div>
            <div class="bg-slate-900/60 border border-white/5 rounded-xl p-3 overflow-x-auto">
                <div class="mb-2 text-[10px] text-slate-500">
                    <span>Estimated daily searches: <span class="text-slate-300 font-medium">\u2248${fmt(dailySearches)}</span></span>
                </div>
                <svg viewBox="0 0 ${W} ${H}" class="w-full" style="min-width:500px;max-width:700px">
                    ${zones}
                    ${zoneLabels}
                    ${yTicks.join('')}
                    ${bars}
                    <line x1="${PAD_L}" x2="${W - PAD_R}" y1="${PAD_T + chartH}" y2="${PAD_T + chartH}" stroke="rgba(255,255,255,0.1)"/>
                    ${axisLabels}
                </svg>
                <div class="flex items-center justify-center gap-5 mt-2 text-[10px] text-slate-500">
                    <span class="flex items-center gap-1.5"><span class="inline-block w-3 h-2 rounded-sm" style="background:rgb(139,92,246);opacity:0.8"></span> Conservative</span>
                    <span class="flex items-center gap-1.5"><span class="inline-block w-3 h-2 rounded-sm" style="background:rgb(139,92,246);opacity:0.22"></span> Optimistic</span>
                    <span class="flex items-center gap-1.5"><span class="inline-block w-6 h-2 rounded-sm" style="border:1px dashed rgb(250,204,21);"></span> Your Position</span>
                </div>
                <p class="mt-2 text-[10px] text-slate-500">
                    These are directional ranges for keyword contribution, not precise planning numbers.
                </p>
                ${tierCards}
            </div>
        </div>`;
    }

    global.renderDownloadChart = renderDownloadChart;
})(window);
