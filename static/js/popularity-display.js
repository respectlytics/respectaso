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
 *   popularity_cap        - fallback rows: the cap applied to the estimate
 *                           (genre floor - 1); null when the storefront has
 *                           no Apple dataset (the estimate stands uncapped)
 *   popularity_genre      - fallback rows: display label of the category
 *                           the cap came from ("" when uninferred)
 *
 * Layout: ONE number per cell - the effective value plus a source badge
 * (EST / ASA / EST* fallback). Everything else lives in the badge's hover
 * popover: what the source is, the other source's value for comparison,
 * and on fallback rows the full cap story with the row's own numbers
 * (raw estimate, cap, category floor) - the cap is explained, never
 * hidden, but no longer competes with the score in the table.
 * Rows from sessions stored before the cap fields existed degrade to a
 * generic fallback explanation.
 */
(function (global) {
    'use strict';

    var BADGES = {
        est: {
            cls: 'bg-slate-700/40 text-slate-300 border border-white/10',
            label: 'EST',
        },
        asa: {
            cls: 'bg-sky-900/40 text-sky-300 border border-sky-500/30',
            label: 'ASA',
        },
        // Deliberately as quiet as EST: fallback is the NORMAL case for
        // long-tail keywords under the Apple source, not a warning. Blue
        // ASA is the scannable "official value" signal.
        fallback: {
            cls: 'bg-slate-700/40 text-slate-300 border border-white/10',
            label: 'EST*',
        },
    };

    function esc(value) {
        return value === null || value === undefined ? '—' : String(value);
    }

    /**
     * Badge + popover content for a scored row. Returns
     * {badge, heading, paragraphs, note} - `note` is the muted footer line
     * (the other source's value for comparison, when one exists).
     */
    function resolveTip(row) {
        var internal = ('popularity_internal' in row) ? row.popularity_internal : row.popularity;
        var apple = ('popularity_apple' in row) ? row.popularity_apple : null;
        var source = row.popularity_source || 'internal';
        var appleConfigured = !!global.APPLE_POP_CONFIGURED;
        var hasInternal = internal !== null && internal !== undefined;

        if (row.popularity_fallback) {
            if (!('popularity_cap' in row)) {
                // Row stored before the cap fields existed.
                return {
                    badge: BADGES.fallback,
                    heading: 'Not in Apple\'s top terms',
                    paragraphs: [
                        'This keyword is outside Apple\'s published top search terms '
                        + 'for this storefront, so RespectASO\'s estimate powers the '
                        + 'score - calibrated to the same 1-100 scale as Apple\'s values.',
                    ],
                    note: '',
                };
            }
            var cap = row.popularity_cap;
            if (cap === null || cap === undefined) {
                return {
                    badge: BADGES.fallback,
                    heading: 'No Apple data for this storefront',
                    paragraphs: [
                        'Apple publishes no search-popularity data for this '
                        + 'storefront, so RespectASO\'s estimate powers the score directly.',
                    ],
                    note: '',
                };
            }
            var where = row.popularity_genre
                ? 'the ' + row.popularity_genre + ' category' : 'its category';
            var absentPara = 'Apple lists each category\'s ~500 most-searched terms, '
                + 'and this keyword is not among them for ' + where
                + ' in this storefront this week.';
            if (hasInternal && internal > cap) {
                return {
                    badge: BADGES.fallback,
                    heading: 'Not in Apple\'s top terms - capped',
                    paragraphs: [
                        absentPara,
                        'It cannot score above Apple\'s lowest reported value there ('
                        + (cap + 1) + '), so RespectASO\'s estimate of ' + internal
                        + ' is scored as ' + cap + '.',
                    ],
                    note: '',
                };
            }
            return {
                badge: BADGES.fallback,
                heading: 'Not in Apple\'s top terms',
                paragraphs: [
                    absentPara,
                    'RespectASO\'s estimate' + (hasInternal ? ' (' + internal + ')' : '')
                    + ' already sits below Apple\'s lowest reported value there ('
                    + (cap + 1) + '), so it powers the score unchanged.',
                ],
                note: '',
            };
        }
        if (source === 'apple') {
            return {
                badge: BADGES.asa,
                heading: 'Apple Ads popularity',
                paragraphs: [
                    'Apple\'s official search popularity for this storefront, '
                    + 'updated weekly - the active source powering your scores.',
                ],
                note: hasInternal ? 'RespectASO estimate for comparison: ' + internal : '',
            };
        }
        var note = '';
        if (apple !== null && apple !== undefined) {
            note = 'Apple\'s official value for comparison: ' + apple;
        } else if (appleConfigured) {
            note = 'Not among Apple\'s top terms in this storefront - Apple reports no value.';
        }
        return {
            badge: BADGES.est,
            heading: 'RespectASO estimate',
            paragraphs: [
                'RespectASO\'s own estimate, calibrated to Apple\'s official '
                + '1-100 popularity scale - the active source powering your scores.',
            ],
            note: note,
        };
    }

    /**
     * Source badge with its hover popover. `idx`/`total` flip the popover
     * below for top-half rows (avoids clipping against the table header)
     * and above otherwise - same rule as formatDownloadCell.
     */
    function badgeHtml(tip, idx, total) {
        var showBelow = total > 0 && idx < total / 2;
        var pos = showBelow ? 'top-full mt-2' : 'bottom-full mb-2';
        return '<span class="group/pop relative inline-flex">'
            + '<span class="text-[8px] font-semibold uppercase tracking-wide rounded px-0.5 py-px '
            + tip.badge.cls + ' cursor-help">' + tip.badge.label + '</span>'
            + '<div class="hidden group-hover/pop:block absolute z-20 ' + pos
            + ' left-1/2 -translate-x-1/2 w-64 bg-slate-800 border border-white/10 rounded-lg p-3 '
            + 'shadow-xl text-left normal-case font-normal tracking-normal whitespace-normal">'
            + '<p class="text-[10px] text-slate-500 mb-1.5 font-medium uppercase tracking-wider">'
            + tip.heading + '</p>'
            + tip.paragraphs.map(function (p, i) {
                return '<p class="text-[11px] leading-relaxed text-slate-300'
                    + (i ? ' mt-1.5' : '') + '">' + p + '</p>';
            }).join('')
            + (tip.note
                ? '<p class="text-[10px] leading-relaxed text-slate-500 mt-2 pt-1.5 border-t border-white/5">'
                    + tip.note + '</p>'
                : '')
            + '</div></span>';
    }

    // Minimum Apple week-over-week popularity move that renders a trend
    // arrow. MUST match APPLE_TREND_MIN_DELTA in aso_tags.py (twin rule).
    var APPLE_TREND_MIN_DELTA = 3;

    function trendHtml(row) {
        var delta = row.popularity_apple_trend;
        if (typeof delta !== 'number' || Math.abs(delta) < APPLE_TREND_MIN_DELTA) return '';
        var arrow = delta > 0 ? '▲' : '▼';
        var tone = delta > 0 ? 'text-emerald-400' : 'text-red-400';
        var signed = (delta > 0 ? '+' : '') + delta;
        return '<span class="text-[9px] ' + tone + ' cursor-help" '
            + 'title="Apple popularity vs previous week: ' + signed + '">' + arrow + '</span>';
    }

    /**
     * Standard centered popularity cell: effective value + source badge
     * with its explanatory popover. `extraHtml` appends inline content.
     * Pass `idx`/`total` (0-based row index, row count) so the popover
     * flips away from the nearest table edge.
     */
    function formatPopularityCell(row, extraHtml, idx, total) {
        var tip = resolveTip(row);
        return '<div class="leading-tight inline-block text-center">'
            + '<span class="inline-flex items-center justify-center gap-1.5">'
            + '<span class="text-sm font-semibold text-purple-400">' + esc(row.popularity) + '</span>'
            + badgeHtml(tip, idx || 0, total || 0)
            + trendHtml(row)
            + (extraHtml || '')
            + '</span>'
            + '</div>';
    }

    /**
     * Chip-style variant for the AI tab tables: wraps the tab's existing
     * color-coded chip HTML with the source badge + popover, keeping each
     * tab's chip styling.
     */
    function formatPopularityChipCell(row, chipHtml) {
        var tip = resolveTip(row);
        return '<div class="leading-tight inline-block text-center">'
            + '<span class="inline-flex items-center justify-center gap-1">' + chipHtml
            + badgeHtml(tip, 0, 0)
            + '</span>'
            + '</div>';
    }

    /**
     * Standalone source badge (with popover) for big-number layouts where
     * the value is rendered by the caller - e.g. the Opportunity detail
     * panel's "50 / 100" block.
     */
    function formatPopularityBadge(row) {
        return badgeHtml(resolveTip(row), 0, 0);
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
    // source would paint a different picture. Symmetric: ASA runs explain
    // below-threshold coverage (Apple only publishes its top search terms,
    // roughly 500+ weekly searches); EST runs warn about sharp divergence
    // from available Apple data. States only observable facts - never why
    // Apple reports what it does, and never that either source is truth.
    var ADVISORY_MIN_ROWS = 5;       // both triggers: minimum affected keywords
    var FALLBACK_SHARE = 0.5;        // ASA runs: share of rows below Apple's bar
    var DIVERGENCE_POINTS = 30;      // EST runs: |apple - internal| threshold
    var DIVERGENCE_SHARE = 0.3;      // EST runs: share of dual-value rows diverging

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
            var fallbackRows = rows.filter(function (r) { return r && r.popularity_fallback; });
            if (fallbackRows.length < ADVISORY_MIN_ROWS
                || fallbackRows.length / rows.length < FALLBACK_SHARE) return '';
            var paras = [
                '<strong class="text-amber-100">' + fallbackRows.length + ' of the ' + rows.length
                    + ' keywords in this analysis are outside Apple\'s published top '
                    + 'terms.</strong> Apple publishes each category\'s 500 most-searched terms '
                    + 'per storefront; keywords outside that group are scored with '
                    + 'RespectASO\'s estimate, calibrated against Apple\'s official values and '
                    + 'aligned to the same 1-100 scale - one consistent ruler across the '
                    + 'whole analysis.',
                'A modest aggregate score under the Apple source therefore reflects where '
                    + 'these keywords sit against Apple\'s reporting bar - not necessarily '
                    + 'misaligned metadata.',
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
        var parasB = [
            '<strong class="text-amber-100">Apple Ads reports substantially different popularity for '
                + divergent.length + ' of the ' + dualRows.length
                + ' keywords with Apple data in this analysis.</strong> '
                + 'Under the Apple Ads source, opportunity and aggregate scores would '
                + 'look different. Hover a keyword\'s source badge to see both values; where they '
                + 'disagree strongly, neither is automatically right - such keywords are worth '
                + 'checking by hand.',
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
    global.formatPopularityBadge = formatPopularityBadge;
    global.formatPopularityCompact = formatPopularityCompact;
    global.formatSourceContextAdvisory = formatSourceContextAdvisory;
})(window);
