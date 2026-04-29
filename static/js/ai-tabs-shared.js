/* AI tabs shared helpers — used by Simulator, Researcher, and Competitor templates.
 *
 * This file is the single source of truth for:
 *   - source label text (formatSourceLabel)
 *   - source badge color (getSourceColor)
 *   - source tooltip lookup (getSourceTooltip — backed by per-template window.SOURCE_TOOLTIPS)
 *   - source badge rendering (formatSourceBadge)
 *   - download estimate cell (formatDownloadCell — uses canonical fmt())
 *   - canonical number formatter (fmt)  — must match Dashboard per .github/instructions/scoring-consistency
 *   - escapeHtml
 *
 * Per-template overrides:
 *   window.SOURCE_LABELS_OVERRIDE   = { source: 'Custom Label', ... }
 *   window.SOURCE_COLORS_OVERRIDE   = { source: 'bg-x-900/30 text-x-400', ... }
 *   window.SOURCE_TOOLTIPS          = { source: 'Per-tab tooltip text', ... }
 *
 * Loaded by: simulator.html, ai_researcher.html, ai_competitor.html
 *   <script src="{% static 'js/ai-tabs-shared.js' %}"></script>
 */
(function () {
    'use strict';

    var DEFAULT_LABELS = {
        // User metadata sources
        'keyword_field': 'Keyword Field',
        'title': 'Title',
        'subtitle': 'Subtitle',
        // Description-derived sources (Simulator)
        'app_description': 'Description Idea',
        'app_description_component': 'Description Word',
        'app_description_combo': 'Description + Metadata',
        // Cross-field combinations
        'combination': 'Combination',
        'title_combo': 'Title Phrase',
        'subtitle_combo': 'Subtitle Phrase',
        'keyword_field_combo': 'Keyword Field Combo',
        'title_subtitle': 'Title + Subtitle',
        'subtitle_title': 'Subtitle + Title',
        'title_keyword_field': 'Title + KF',
        'keyword_field_title': 'KF + Title',
        'subtitle_keyword_field': 'Subtitle + KF',
        'keyword_field_subtitle': 'KF + Subtitle',
        // Researcher-specific
        'seed': 'Seed Keyword',
        'ai_generated': 'AI Generated',
        'ai_generated_component': 'AI Generated Word',
        // Competitor-specific
        'implied': 'Implied',
        'implied_component': 'Implied Word',
        'inferred': 'Inferred',
        'inferred_component': 'Inferred Word',
        'ai_discovered': 'AI Discovered',
        'ai_discovered_component': 'AI Discovered Word',
    };

    var DEFAULT_COLORS = {
        'keyword_field': 'bg-emerald-900/30 text-emerald-400',
        'title': 'bg-blue-900/30 text-blue-400',
        'subtitle': 'bg-amber-900/30 text-amber-400',
        'app_description': 'bg-purple-900/30 text-purple-400',
        'app_description_component': 'bg-violet-900/30 text-violet-400',
        'app_description_combo': 'bg-fuchsia-900/30 text-fuchsia-400',
        'combination': 'bg-indigo-900/30 text-indigo-400',
        'title_combo': 'bg-blue-900/30 text-blue-300',
        'subtitle_combo': 'bg-amber-900/30 text-amber-300',
        'keyword_field_combo': 'bg-emerald-900/30 text-emerald-300',
        'title_subtitle': 'bg-cyan-900/30 text-cyan-400',
        'subtitle_title': 'bg-cyan-900/30 text-cyan-400',
        'title_keyword_field': 'bg-lime-900/30 text-lime-400',
        'keyword_field_title': 'bg-lime-900/30 text-lime-400',
        'subtitle_keyword_field': 'bg-pink-900/30 text-pink-400',
        'keyword_field_subtitle': 'bg-pink-900/30 text-pink-400',
        // Researcher
        'seed': 'bg-purple-900/30 text-purple-400',
        'ai_generated': 'bg-blue-900/30 text-blue-400',
        'ai_generated_component': 'bg-blue-900/30 text-blue-300',
        // Competitor
        'implied': 'bg-blue-900/30 text-blue-400',
        'implied_component': 'bg-blue-900/30 text-blue-300',
        'inferred': 'bg-amber-900/30 text-amber-400',
        'inferred_component': 'bg-amber-900/30 text-amber-300',
        'ai_discovered': 'bg-rose-900/30 text-rose-400',
        'ai_discovered_component': 'bg-rose-900/30 text-rose-300',
    };

    /* Default tooltip text for every known source value. Templates can override
     * any entry via window.SOURCE_TOOLTIPS to use their preferred voice
     * (e.g. "your title" vs "the recommended title" vs "the competitor's title").
     * Keeping a complete default map here guarantees that no source value
     * ever renders without a tooltip — even ones a template forgot to override.
     */
    var DEFAULT_TOOLTIPS = {
        'title': 'From the app title',
        'subtitle': 'From the app subtitle',
        'keyword_field': 'From the keyword field metadata',
        'app_description': 'From the app description',
        'app_description_component': 'A component word from a description-derived idea, scored separately so you can evaluate its long-tail value.',
        'app_description_combo': 'A natural long-tail phrase combining existing metadata with a description-derived concept.',
        'combination': 'Cross-field word combinations from the metadata that Apple indexes for long-tail ranking',
        'title_combo': 'Natural phrase formed from words in the title',
        'subtitle_combo': 'Natural phrase formed from words in the subtitle',
        'keyword_field_combo': 'Natural phrase formed by recombining words in the keyword field',
        'title_subtitle': 'Combination of words from the title and subtitle',
        'subtitle_title': 'Combination of words from the subtitle and title',
        'title_keyword_field': 'Combination of words from the title and keyword field',
        'keyword_field_title': 'Combination of words from the keyword field and title',
        'subtitle_keyword_field': 'Combination of words from the subtitle and keyword field',
        'keyword_field_subtitle': 'Combination of words from the keyword field and subtitle',
        // Researcher-specific
        'seed': 'Your original seed keyword used to start the research',
        'ai_generated': 'AI-discovered keyword based on analyzing top-ranking apps for your seed keyword',
        'ai_generated_component': 'A component word from a multi-word AI-generated phrase, scored separately so you can evaluate its long-tail value.',
        // Competitor-specific
        'implied': "Keywords from the app's description — features, use cases, and themes users search for",
        'implied_component': "A component word from a multi-word implied phrase, scored separately so you can evaluate its long-tail value.",
        'inferred': "Broader market keywords — what users looking for this type of app would search",
        'inferred_component': "A component word from a multi-word inferred phrase, scored separately so you can evaluate its long-tail value.",
        'ai_discovered': "AI-discovered keywords relevant to this app's niche — beyond what's in their visible metadata",
        'ai_discovered_component': "A component word from a multi-word AI-discovered phrase, scored separately so you can evaluate its long-tail value.",
    };

    function escapeHtml(str) {
        var d = document.createElement('div');
        d.textContent = (str === null || str === undefined) ? '' : String(str);
        return d.innerHTML;
    }

    function formatSourceLabel(source) {
        var overrides = window.SOURCE_LABELS_OVERRIDE || {};
        if (Object.prototype.hasOwnProperty.call(overrides, source)) return overrides[source];
        if (Object.prototype.hasOwnProperty.call(DEFAULT_LABELS, source)) return DEFAULT_LABELS[source];
        return source;
    }

    function getSourceColor(source) {
        var overrides = window.SOURCE_COLORS_OVERRIDE || {};
        if (overrides[source]) return overrides[source];
        return DEFAULT_COLORS[source] || 'bg-slate-700/30 text-slate-400';
    }

    function getSourceTooltip(source) {
        var tooltips = window.SOURCE_TOOLTIPS || {};
        if (Object.prototype.hasOwnProperty.call(tooltips, source)) return tooltips[source];
        if (Object.prototype.hasOwnProperty.call(DEFAULT_TOOLTIPS, source)) return DEFAULT_TOOLTIPS[source];
        return '';
    }

    /* Canonical number formatter.
     * Must match the Dashboard's fmt() per .github/instructions/scoring-consistency.
     * Preserves 1 decimal for values < 10 so we never display "<1" or rounded zeros.
     */
    function fmt(n) {
        if (n >= 1000) return (n / 1000).toFixed(1).replace(/\.0$/, '') + 'K';
        if (n < 1) return n.toFixed(1);
        if (n < 10) return n.toFixed(1).replace(/\.0$/, '');
        return Math.round(n).toString();
    }

    /* Download estimate cell with hover tooltip.
     * Uses per-position data (rank #1 / #5 / #10) — NOT tier averages.
     * Same display across all 3 AI tabs and the Dashboard.
     */
    function formatDownloadCell(dl, idx, total) {
        if (!dl || !dl.positions || dl.positions.length < 10) {
            return '<span class="text-xs text-slate-500">—</span>';
        }
        var p1 = dl.positions[0], p5 = dl.positions[4], p10 = dl.positions[9];
        var showBelow = idx < total / 2;
        var posClass = showBelow ? 'top-full mt-2' : 'bottom-full mb-2';
        return '<div class="group relative inline-block">' +
            '<span class="text-xs font-mono text-slate-300 cursor-help border-b border-dotted border-slate-600">' +
            fmt(p1.downloads_low) + '–' + fmt(p1.downloads_high) + '<span class="text-slate-500">/day</span></span>' +
            '<div class="hidden group-hover:block absolute z-20 ' + posClass + ' left-1/2 -translate-x-1/2 w-48 bg-slate-800 border border-white/10 rounded-lg p-3 shadow-xl text-left">' +
            '<p class="text-[10px] text-slate-500 mb-2 font-medium uppercase tracking-wider">Est. daily downloads</p>' +
            '<div class="space-y-1.5">' +
            '<div class="flex justify-between text-xs"><span class="text-emerald-400">Rank #1</span><span class="text-slate-300 font-mono">' + fmt(p1.downloads_low) + '–' + fmt(p1.downloads_high) + '</span></div>' +
            '<div class="flex justify-between text-xs"><span class="text-amber-400">Rank #5</span><span class="text-slate-300 font-mono">' + fmt(p5.downloads_low) + '–' + fmt(p5.downloads_high) + '</span></div>' +
            '<div class="flex justify-between text-xs"><span class="text-slate-400">Rank #10</span><span class="text-slate-300 font-mono">' + fmt(p10.downloads_low) + '–' + fmt(p10.downloads_high) + '</span></div>' +
            '</div>' +
            '</div></div>';
    }

    function formatSourceBadge(source, idx, total) {
        var tooltip = getSourceTooltip(source);
        var labelText = formatSourceLabel(source);
        var color = getSourceColor(source);
        if (!tooltip) {
            return '<span class="text-xs px-1.5 py-0.5 rounded ' + color + '">' + escapeHtml(labelText) + '</span>';
        }
        var showBelow = idx < total / 2;
        var posClass = showBelow ? 'top-full mt-1' : 'bottom-full mb-1';
        return '<div class="group relative inline-block">' +
            '<span class="text-xs px-1.5 py-0.5 rounded cursor-help ' + color + '">' + escapeHtml(labelText) + '</span>' +
            '<div class="hidden group-hover:block absolute z-20 ' + posClass + ' left-0 w-52 bg-slate-800 border border-white/10 rounded-lg p-2 shadow-xl text-left">' +
            '<p class="text-[11px] text-slate-300 leading-snug">' + tooltip + '</p>' +
            '</div></div>';
    }

    // Expose under a namespace AND as bare globals so existing inline template
    // code that calls e.g. formatSourceBadge() / fmt() / escapeHtml() keeps working
    // without any rename.
    window.AsoTabs = {
        SOURCE_LABELS: DEFAULT_LABELS,
        SOURCE_COLORS: DEFAULT_COLORS,
        SOURCE_TOOLTIPS: DEFAULT_TOOLTIPS,
        escapeHtml: escapeHtml,
        formatSourceLabel: formatSourceLabel,
        getSourceColor: getSourceColor,
        getSourceTooltip: getSourceTooltip,
        fmt: fmt,
        formatDownloadCell: formatDownloadCell,
        formatSourceBadge: formatSourceBadge,
    };
    window.escapeHtml = escapeHtml;
    window.formatSourceLabel = formatSourceLabel;
    window.getSourceColor = getSourceColor;
    window.getSourceTooltip = getSourceTooltip;
    window.fmt = fmt;
    window.formatDownloadCell = formatDownloadCell;
    window.formatSourceBadge = formatSourceBadge;
})();
