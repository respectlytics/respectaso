/**
 * popularity-display.js - the ONE client-side renderer for dual-source
 * popularity cells.
 *
 * Server-side counterpart: {% popularity_cell %} in aso/templatetags/aso_tags.py.
 * The two MUST stay visually identical (guarded by test_scoring_consistency).
 *
 * Every payload rendered here carries:
 *   popularity            - the effective value (feeds all calculations)
 *   popularity_internal   - RespectASO estimate
 *   popularity_apple      - Apple Ads value (null when Apple has none)
 *   popularity_source     - "internal" | "apple" (source of `popularity`)
 *   popularity_fallback   - true when Apple is selected but had no value
 *
 * Layout (centered): effective value + source badge (EST / ASA / amber EST*
 * fallback), with the other source's value on a second line when it exists.
 * When the Apple integration is connected (window.APPLE_POP_CONFIGURED,
 * set by base.html) but has no value for a keyword, an explicit muted
 * "no Apple data" line is shown - visible on screen, not tooltip-only.
 * When Apple was never connected, no second line renders.
 */
(function (global) {
    'use strict';

    var BADGES = {
        est: {
            cls: 'bg-slate-700/40 text-slate-300 border border-white/10',
            label: 'EST',
            tip: 'RespectASO estimate - the active source powering your scores.',
        },
        asa: {
            cls: 'bg-sky-900/40 text-sky-300 border border-sky-500/30',
            label: 'ASA',
            tip: 'Apple Ads search popularity - the active source powering your scores.',
        },
        fallback: {
            cls: 'bg-amber-900/40 text-amber-300 border border-amber-500/30',
            label: 'EST*',
            tip: 'Apple Ads has no value for this keyword in this storefront, '
                + 'so the RespectASO estimate is used instead.',
        },
    };

    function esc(value) {
        return value === null || value === undefined ? '—' : String(value);
    }

    /**
     * Resolve badge + optional secondary line for a scored row.
     * Older stored sessions without the dual fields degrade to a plain
     * EST badge with no secondary line.
     */
    function resolveParts(row) {
        var internal = ('popularity_internal' in row) ? row.popularity_internal : row.popularity;
        var apple = ('popularity_apple' in row) ? row.popularity_apple : null;
        var source = row.popularity_source || 'internal';
        var appleConfigured = !!global.APPLE_POP_CONFIGURED;

        if (row.popularity_fallback) {
            return { badge: BADGES.fallback, secondary: 'no Apple data', muted: true };
        }
        if (source === 'apple') {
            return {
                badge: BADGES.asa,
                secondary: (internal !== null && internal !== undefined) ? 'EST: ' + internal : '',
                muted: false,
            };
        }
        if (apple !== null && apple !== undefined) {
            return { badge: BADGES.est, secondary: 'ASA: ' + apple, muted: false };
        }
        return {
            badge: BADGES.est,
            secondary: appleConfigured ? 'no Apple data' : '',
            muted: true,
        };
    }

    function badgeHtml(badge) {
        return '<span class="text-[8px] font-semibold uppercase tracking-wide rounded px-0.5 py-px '
            + badge.cls + ' cursor-help" title="' + badge.tip + '">' + badge.label + '</span>';
    }

    function secondaryHtml(parts, sizeCls) {
        if (!parts.secondary) return '';
        var tone = parts.muted ? 'text-slate-600 italic' : 'text-slate-500';
        return '<span class="block ' + sizeCls + ' mt-0.5 ' + tone + '">' + parts.secondary + '</span>';
    }

    /** Standard centered two-line popularity cell. `extraHtml` appends to line 1. */
    function formatPopularityCell(row, extraHtml) {
        var parts = resolveParts(row);
        return '<div class="leading-tight inline-block text-center">'
            + '<span class="inline-flex items-center justify-center gap-1.5">'
            + '<span class="text-sm font-semibold text-purple-400">' + esc(row.popularity) + '</span>'
            + badgeHtml(parts.badge)
            + (extraHtml || '')
            + '</span>'
            + secondaryHtml(parts, 'text-[10px]')
            + '</div>';
    }

    /**
     * Chip-style variant for the AI tab tables: wraps the tab's existing
     * color-coded chip HTML with the source badge, keeping each tab's chip
     * styling. Secondary line only when the other source has a value.
     */
    function formatPopularityChipCell(row, chipHtml) {
        var parts = resolveParts(row);
        return '<div class="leading-tight inline-block text-center">'
            + '<span class="inline-flex items-center justify-center gap-1">' + chipHtml
            + badgeHtml(parts.badge)
            + '</span>'
            + secondaryHtml(parts, 'text-[9px]')
            + '</div>';
    }

    /**
     * Run-level source note for stored AI analyses. Reports are frozen
     * snapshots: their numbers keep matching the AI's written analysis, so
     * instead of retroactively changing them, the report STATES which
     * popularity source produced it - and flags when that differs from the
     * user's current selection (new and refined runs use the current one).
     */
    function formatRunSourceNote(rows) {
        if (!rows || !rows.length) return '';
        var usedApple = rows.some(function (r) {
            return r && (r.popularity_source === 'apple' || r.popularity_fallback);
        });
        var used = usedApple ? 'apple' : 'internal';
        var label = usedApple ? 'Apple Ads popularity' : 'RespectASO estimate';
        var current = global.POPULARITY_SOURCE || 'internal';
        var html = '<span class="text-slate-500">Popularity source used in this analysis:</span> '
            + '<span class="text-slate-300 font-medium">' + label + '</span>';
        if (current !== used) {
            var currentLabel = current === 'apple' ? 'Apple Ads popularity' : 'the RespectASO estimate';
            html += ' <span class="ml-1.5 text-[10px] text-amber-300/90 bg-amber-900/25 border border-amber-500/25 rounded px-1.5 py-px">'
                + 'differs from your current selection - new runs use ' + currentLabel + '</span>';
        }
        return '<p class="text-xs mb-2">' + html + '</p>';
    }

    /**
     * Tiny standalone source badge for saved-run list items: which
     * popularity source a stored analysis used ('apple' | 'internal').
     * Same visual language as the table badges; '' for unknown/running.
     */
    function formatRunSourceBadge(source) {
        if (source !== 'apple' && source !== 'internal') return '';
        var badge = source === 'apple' ? BADGES.asa : BADGES.est;
        var label = source === 'apple' ? 'Apple Ads popularity' : 'RespectASO estimate';
        return '<span class="text-[8px] font-semibold uppercase tracking-wide rounded px-0.5 py-px '
            + badge.cls + ' cursor-help" title="Popularity source used in this analysis: '
            + label + '">' + badge.label + '</span>';
    }

    /** Compact single-line variant for tickers and tight layouts: "48 ASA". */
    function formatPopularityCompact(row) {
        var source = row.popularity_source || 'internal';
        var badge = row.popularity_fallback
            ? BADGES.fallback
            : (source === 'apple' ? BADGES.asa : BADGES.est);
        return esc(row.popularity) + ' ' + badge.label;
    }

    // ── Source-context advisory ─────────────────────────────────────────
    // Data-driven Overview callout explaining when the OTHER popularity
    // source would paint a different picture. Symmetric: ASA runs warn
    // about Apple's scale-minimum clusters; EST runs warn about sharp
    // divergence from available Apple data. States only observable facts
    // about the data's resolution - never why Apple reports what it does,
    // and never that either source is the truth.
    var ADVISORY_MIN_ROWS = 5;       // both triggers: minimum affected keywords
    var FLOOR_SHARE = 0.4;           // ASA runs: share of Apple-scored rows at 5
    var DIVERGENCE_POINTS = 30;      // EST runs: |apple - internal| threshold
    var DIVERGENCE_SHARE = 0.3;      // EST runs: share of dual-value rows diverging
    var APPLE_SCALE_MIN = 5;

    function advisoryHtml(paragraphs) {
        return '<div class="mb-4 rounded-xl border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-200">'
            + paragraphs.map(function (p, i) {
                return '<p class="text-xs leading-relaxed text-amber-100/90' + (i ? ' mt-2' : '') + '">' + p + '</p>';
            }).join('')
            + '</div>';
    }

    function altReadinessSentence(opts, label) {
        if (!opts || typeof opts.altReadiness !== 'number') return '';
        return 'Scored with ' + label + ' instead, this metadata would rate roughly '
            + '<strong class="text-amber-100">' + Math.round(opts.altReadiness) + '/100</strong>. '
            + 'Neither figure is more true - they measure differently.';
    }

    /**
     * Returns advisory HTML for a stored run's rows, or '' when there is
     * nothing worth saying. opts (Simulator only): {altReadiness, altSource}
     * - the frozen other-source readiness computed at run time.
     */
    function formatSourceContextAdvisory(rows, opts) {
        if (!rows || !rows.length) return '';
        var usedApple = rows.some(function (r) {
            return r && (r.popularity_source === 'apple' || r.popularity_fallback);
        });

        if (usedApple) {
            var appleRows = rows.filter(function (r) { return r && r.popularity_source === 'apple'; });
            var floorRows = appleRows.filter(function (r) { return r.popularity_apple === APPLE_SCALE_MIN; });
            if (floorRows.length < ADVISORY_MIN_ROWS
                || floorRows.length / appleRows.length < FLOOR_SHARE) return '';
            var paras = [
                '<strong class="text-amber-100">Apple reported its minimum popularity value ('
                    + APPLE_SCALE_MIN + ') for ' + floorRows.length + ' of the ' + appleRows.length
                    + ' keywords it scored in this analysis.</strong> '
                    + APPLE_SCALE_MIN + ' is the lowest value on Apple\'s popularity scale, and '
                    + 'Apple\'s data does not differentiate between keywords reported at '
                    + APPLE_SCALE_MIN + ' - they may differ substantially in real search interest, '
                    + 'but the data cannot show it. When many keywords cluster at the minimum, '
                    + 'opportunity scores - and any aggregate score built on them - are capped by '
                    + 'that limited resolution. A low score here reflects Apple\'s numbers, not '
                    + 'necessarily misaligned metadata.',
                'The RespectASO estimate beside each keyword rates search interest from observable '
                    + 'App Store signals, and the two sources can disagree - sometimes strongly. '
                    + 'Where they do, neither number is automatically right: treat a large gap as a '
                    + 'keyword worth checking by hand, for example by searching it in the App Store.',
            ];
            var altA = altReadinessSentence(opts, 'the RespectASO estimate');
            if (altA) paras.push(altA);
            paras.push('To analyze under the estimate, switch the source in Settings - new runs '
                + 'and re-simulations use your current selection.');
            return advisoryHtml(paras);
        }

        var dualRows = rows.filter(function (r) {
            return r && typeof r.popularity_apple === 'number' && typeof r.popularity_internal === 'number';
        });
        var divergent = dualRows.filter(function (r) {
            return Math.abs(r.popularity_apple - r.popularity_internal) >= DIVERGENCE_POINTS;
        });
        if (divergent.length < ADVISORY_MIN_ROWS
            || divergent.length / dualRows.length < DIVERGENCE_SHARE) return '';
        var atFloor = divergent.filter(function (r) { return r.popularity_apple === APPLE_SCALE_MIN; }).length;
        var parasB = [
            '<strong class="text-amber-100">Apple Ads reports substantially different popularity for '
                + divergent.length + ' of the ' + dualRows.length
                + ' keywords with Apple data in this analysis'
                + (atFloor ? ', often at Apple\'s scale minimum of ' + APPLE_SCALE_MIN : '')
                + '.</strong> Under the Apple Ads source, opportunity and aggregate scores would '
                + 'look different. Both values are shown beside each keyword; where they disagree '
                + 'strongly, neither is automatically right - such keywords are worth checking by hand.',
        ];
        var altB = altReadinessSentence(opts, 'Apple Ads popularity');
        if (altB) parasB.push(altB);
        parasB.push('To analyze under Apple Ads popularity, switch the source in Settings - new '
            + 'runs and re-simulations use your current selection.');
        return advisoryHtml(parasB);
    }

    global.formatPopularityCell = formatPopularityCell;
    global.formatRunSourceNote = formatRunSourceNote;
    global.formatRunSourceBadge = formatRunSourceBadge;
    global.formatPopularityChipCell = formatPopularityChipCell;
    global.formatPopularityCompact = formatPopularityCompact;
    global.formatSourceContextAdvisory = formatSourceContextAdvisory;
})(window);
