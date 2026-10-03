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
        // A phrase in the storefront's language (every AI tab, outside English)
        'native_intent': 'Local Phrase',
        // Cross-field combinations
        'combination': 'Combination',
        'title_combo': 'Title Phrase',
        'subtitle_combo': 'Subtitle Phrase',
        // Spelled out: "KF" and "Combo" read as internal shorthand (a
        // reviewer, 2026-09-23). "Phrase" as for the title and subtitle.
        'keyword_field_combo': 'Keyword Field Phrase',
        'title_subtitle': 'Title + Subtitle',
        'subtitle_title': 'Subtitle + Title',
        'title_keyword_field': 'Title + Keyword Field',
        'keyword_field_title': 'Keyword Field + Title',
        'subtitle_keyword_field': 'Subtitle + Keyword Field',
        'keyword_field_subtitle': 'Keyword Field + Subtitle',
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
        'native_intent': 'bg-teal-900/30 text-teal-400',
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
        'app_description_component': 'One word of a search idea from the app description, scored on its own.',
        'app_description_combo': 'A natural long-tail phrase combining existing metadata with a description-derived concept.',
        'native_intent': "A phrase people search in this country's language",
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
        'ai_generated': 'Suggested by your AI provider from the top apps for your seed keyword',
        'ai_generated_component': 'One word of a phrase your AI provider suggested, scored on its own.',
        // Competitor-specific
        'implied': "Keywords from the app's description: features, use cases, and themes users search for",
        'implied_component': "One word of an implied phrase, scored on its own.",
        'inferred': "Broader market keywords: what users looking for this type of app would search",
        'inferred_component': "One word of an inferred phrase, scored on its own.",
        'ai_discovered': "AI-discovered keywords relevant to this app's niche, beyond what is in its visible metadata",
        'ai_discovered_component': "One word of an AI-discovered phrase, scored on its own.",
    };

    // Safe in text and in an attribute value (data-tip, title).
    function escapeHtml(str) {
        var d = document.createElement('div');
        d.textContent = (str === null || str === undefined) ? '' : String(str);
        return d.innerHTML.replace(/"/g, '&quot;').replace(/'/g, '&#39;');
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
            return '<span class="text-xs text-slate-400">—</span>';
        }
        var showBelow = idx < total / 2;
        var posClass = showBelow ? 'top-full mt-2' : 'bottom-full mb-2';
        if (dl.below_threshold) { return tinyMarketCell(posClass); }
        var p1 = dl.positions[0], p5 = dl.positions[4], p10 = dl.positions[9];
        // Named group ("group/dl") so the tooltip only shows on cell hover, not
        // when hovering anywhere on a parent row that also carries a "group" class.
        return '<div class="group/dl relative inline-block">' +
            '<span class="text-sm tabular-nums text-slate-300 cursor-help border-b border-dotted border-slate-600">' +
            fmt(p1.downloads_low) + '–' + fmt(p1.downloads_high) + '<span class="text-slate-400">/day</span></span>' +
            '<div class="hidden group-hover/dl:block absolute z-20 ' + posClass + ' left-1/2 -translate-x-1/2 w-48 bg-slate-800 border border-white/10 rounded-lg p-3 shadow-xl text-left">' +
            '<p class="text-2xs text-slate-400 mb-2 font-medium uppercase tracking-wider">Est. daily downloads</p>' +
            '<div class="space-y-1.5">' +
            '<div class="flex justify-between text-xs"><span class="text-emerald-400">Rank #1</span><span class="text-slate-300 tabular-nums">' + fmt(p1.downloads_low) + '–' + fmt(p1.downloads_high) + '</span></div>' +
            '<div class="flex justify-between text-xs"><span class="text-amber-400">Rank #5</span><span class="text-slate-300 tabular-nums">' + fmt(p5.downloads_low) + '–' + fmt(p5.downloads_high) + '</span></div>' +
            '<div class="flex justify-between text-xs"><span class="text-slate-400">Rank #10</span><span class="text-slate-300 tabular-nums">' + fmt(p10.downloads_low) + '–' + fmt(p10.downloads_high) + '</span></div>' +
            '</div>' +
            derivedMarketNote(dl) +
            '</div></div>';
    }

    // Twins of _derived_market_note() and _tiny_market_cell() in
    // aso/templatetags/aso_tags.py. scoring-consistency.instructions.md: the
    // two renderers must stay visually identical, and a test asserts the copy.
    var DERIVED_MARKET_NOTE = 'Market size for this storefront is derived from population and iOS share, not measured. Treat the range as indicative.';
    var TINY_MARKET_TEXT = 'Under 1 search a day';
    var TINY_MARKET_NOTE = 'This storefront sees under one search a day for this keyword, so there is no download range worth quoting.';

    function derivedMarketNote(dl) {
        if (!dl || dl.market_source !== 'derived') { return ''; }
        return '<p class="text-2xs text-slate-400 leading-snug mt-2 pt-2 border-t border-white/5">' +
            DERIVED_MARKET_NOTE + '</p>';
    }

    function tinyMarketCell(posClass) {
        return '<div class="group/dl relative inline-block">' +
            '<span class="text-xs text-slate-400 cursor-help border-b border-dotted border-slate-600">' +
            TINY_MARKET_TEXT + '</span>' +
            '<div class="hidden group-hover/dl:block absolute z-20 ' + posClass +
            ' left-1/2 -translate-x-1/2 w-52 bg-slate-800 border border-white/10 rounded-lg p-3 shadow-xl text-left">' +
            '<p class="text-2xs text-slate-300 leading-snug">' + TINY_MARKET_NOTE + '</p>' +
            '</div></div>';
    }

    function formatSourceBadge(source, idx, total) {
        var tooltip = getSourceTooltip(source);
        var labelText = formatSourceLabel(source);
        var color = getSourceColor(source);
        if (!tooltip) {
            return '<span class="text-xs px-1.5 py-0.5 rounded whitespace-nowrap ' + color + '">' + escapeHtml(labelText) + '</span>';
        }
        var showBelow = idx < total / 2;
        var posClass = showBelow ? 'top-full mt-1' : 'bottom-full mb-1';
        // Named group ("group/src") to avoid triggering on a parent row's "group".
        return '<div class="group/src relative inline-block">' +
            '<span class="text-xs px-1.5 py-0.5 rounded cursor-help whitespace-nowrap ' + color + '">' + escapeHtml(labelText) + '</span>' +
            '<div class="hidden group-hover/src:block absolute z-20 ' + posClass + ' left-0 w-52 bg-slate-800 border border-white/10 rounded-lg p-2 shadow-xl text-left">' +
            '<p class="text-2xs text-slate-300 leading-snug">' + tooltip + '</p>' +
            '</div></div>';
    }

    // ---- Run status badge (the three AI tabs' "Recent" lists) --------------
    // Complete class strings - Tailwind only extracts whole literals. A
    // finished run carries no chip: only a run that is not done says so
    // (AI_DECISION_FIRST_PLAN.md 2.7).
    var STATUS_BADGES = {
        queued:    {label: 'Waiting', color: 'bg-purple-900/30 text-purple-300'},
        running:   {label: 'Running', color: 'bg-blue-900/30 text-blue-400'},
        failed:    {label: 'Failed',  color: 'bg-red-900/30 text-red-400'},
        cancelled: {label: 'Stopped', color: 'bg-slate-700 text-slate-400'},
    };

    /**
     * The status pill for one run in a history list, '' for a finished run.
     *
     * "queued" only ever appears on a card whose run is being retried: the
     * user needs to see that their click landed, and that clicking again
     * would not add anything.
     */
    function formatRunStatusBadge(status) {
        var badge = STATUS_BADGES[status];
        if (!badge) return '';
        return '<span class="text-xs px-2 py-0.5 rounded-full whitespace-nowrap ' +
            badge.color + '">' + escapeHtml(badge.label) + '</span>';
    }

    // ---- Recent runs (AI_DECISION_FIRST_PLAN.md 2.7) -------------------------

    // Twin of aso.scoring.sentence_app_name: the brand without the App Store
    // subtitle, "Calm Minutes: Meditation & Sleep" reads "Calm Minutes".
    // aso_pro/tests/test_ai_decision_first.py holds the two to the same words.
    function shortAppName(name) {
        var full = String(name || '').trim();
        var base = full;
        [':', ' - ', ' – ', ' — ', ' |'].forEach(function (separator) {
            if (base.indexOf(separator) !== -1) base = base.split(separator)[0];
        });
        return base.trim() || full;
    }

    // "2 days ago": the largest unit of static/js/time-ago.js's words, so a
    // run list reads like every other "ago" in the app.
    function timeAgo(iso) {
        if (!window.TimeAgo) return '';
        var then = window.TimeAgo.parse(iso);
        if (!then) return '';
        return window.TimeAgo.ago(then, new Date(), true);
    }

    var DELETE_ICON = '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">' +
        '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>';

    /**
     * One row of an AI tab's recent runs. Line 1: the label, "US · for
     * Pausely" (or "for a new app") and when; line 2: opts.line2Html (the
     * suggested title, or the Simulator's readiness). On the right a status
     * chip only for a run that is not done, Retry for a failed or stopped
     * run, and the delete ×. A finished run opens on a click.
     *
     * opts: {iconUrl, forApp ('' for a new app), line2Html, onOpen(id),
     *        onDelete(id), onRetry(id, button), retryTitle}
     */
    function recentRunRow(s, opts) {
        opts = opts || {};
        var clickable = s.status === 'completed';
        var row = document.createElement('div');
        row.className = 'bg-slate-800/30 border border-white/5 rounded-lg px-4 py-3 flex items-center gap-3' +
            (clickable ? ' cursor-pointer hover:border-purple-500/30 hover:bg-slate-800/60 transition-colors' : '');
        var when = timeAgo(s.date);
        var meta = [escapeHtml(s.country), 'for ' + escapeHtml(opts.forApp || 'a new app')];
        if (when) {
            meta.push('<time datetime="' + escapeHtml(s.date) + '" title="' +
                escapeHtml(formatFailureWhen(s.date)) + '">' + escapeHtml(when) + '</time>');
        }
        var running = s.status === 'running' || s.status === 'queued';
        row.innerHTML =
            (opts.iconUrl ? '<img src="' + escapeHtml(opts.iconUrl) + '" class="w-8 h-8 rounded-lg flex-shrink-0" alt="">' : '') +
            '<div class="min-w-0 flex-1">' +
                '<div class="flex items-baseline gap-2 min-w-0">' +
                    '<span class="text-sm font-medium text-white truncate">' + escapeHtml(s.label) + '</span>' +
                    '<span class="text-sm text-slate-400 whitespace-nowrap">' + meta.join(' · ') + '</span>' +
                '</div>' +
                (opts.line2Html ? '<div class="text-sm text-slate-400 truncate mt-0.5">' + opts.line2Html + '</div>' : '') +
            '</div>' +
            '<div class="flex items-center gap-2 flex-shrink-0">' +
                formatRunStatusBadge(s.status) +
                ((s.status === 'failed' || s.status === 'cancelled') && opts.onRetry
                    ? '<button type="button" data-run-retry class="text-xs px-2 py-0.5 rounded-full border border-purple-500/30 text-purple-300 hover:bg-purple-600/20 transition-colors" title="' +
                      escapeHtml(opts.retryTitle || '') + '">Retry</button>'
                    : '') +
                (running ? '' : '<button type="button" data-run-delete class="text-slate-500 hover:text-red-400 transition-colors p-1" title="Delete">' + DELETE_ICON + '</button>') +
            '</div>';
        if (clickable && opts.onOpen) row.addEventListener('click', function () { opts.onOpen(s.id); });
        var retry = row.querySelector('[data-run-retry]');
        if (retry) retry.addEventListener('click', function (e) { e.stopPropagation(); opts.onRetry(s.id, retry); });
        var del = row.querySelector('[data-run-delete]');
        if (del && opts.onDelete) del.addEventListener('click', function (e) { e.stopPropagation(); opts.onDelete(s.id); });
        return row;
    }

    // The filter box above a recent list only helps past five runs. Call it
    // before filtering, so a hidden box never narrows the list.
    function syncRecentFilter(total) {
        var filter = document.getElementById('recent-sessions-filter');
        if (!filter) return;
        filter.classList.toggle('hidden', total <= 5);
        if (total <= 5) filter.value = '';
    }

    /*
     * Why a run failed: the one panel every AI tab shows, on a failed card in
     * the Recent list and when a run fails on screen. Built from the server's
     * failure record (aso_pro/run_failures.py): what went wrong, whose side
     * it is on, what to do, the step, the model, the version. The Copy button
     * hands over the same facts as plain text, with nothing secret in them,
     * so a screenshot or a pasted report is enough to act on.
     */
    function formatFailureWhen(iso) {
        if (!iso) return '';
        var d = new Date(iso);
        return isNaN(d) ? iso : d.toLocaleString();
    }

    function formatRunFailure(failure) {
        if (!failure) return '';
        var facts = [
            ['Step', failure.step],
            ['AI service', failure.service],
            ['Model', failure.model],
            ['When', formatFailureWhen(failure.at)],
            ['RespectASO', failure.version],
            ['Run', failure.run]
        ].filter(function (f) { return f[1]; });
        var grid = facts.length
            ? '<dl class="mt-3 grid grid-cols-[7rem_1fr] gap-x-3 gap-y-1 text-xs">' +
                facts.map(function (f) {
                    return '<dt class="text-slate-400">' + escapeHtml(f[0]) + '</dt>' +
                           '<dd class="text-slate-300 break-words">' + escapeHtml(f[1]) + '</dd>';
                }).join('') + '</dl>'
            : '';
        return '<div class="run-failure bg-red-950/40 border border-red-500/30 rounded-lg p-4 text-left cursor-auto">' +
            '<p class="text-sm font-semibold text-red-200">' + escapeHtml(failure.headline) + '</p>' +
            '<p class="text-xs text-red-300/80 mt-0.5">Whose side: ' + escapeHtml(failure.whose) + '</p>' +
            (failure.reason
                ? '<p class="mt-3 text-xs text-slate-300 break-words"><span class="text-slate-400">Details: </span>' +
                  escapeHtml(failure.reason) + '</p>'
                : '') +
            '<p class="mt-2 text-xs text-slate-100"><span class="text-slate-400">What to do: </span>' +
                escapeHtml(failure.next) + '</p>' +
            grid +
            (failure.where
                ? '<p class="mt-2 text-2xs font-mono text-slate-400 break-all">Code location: ' +
                  escapeHtml(failure.where) + '</p>'
                : '') +
            '<button type="button" class="run-failure-copy btn-secondary mt-3" ' +
                'title="Copies what went wrong, the step, the model and the version. No keys or private data." ' +
                'data-report="' + escapeHtml(failure.report || '') + '">Copy error report</button>' +
        '</div>';
    }

    /*
     * A failed card in a Recent list: says what went wrong on the card itself,
     * and opens the full panel on a click. Returns the element to append in
     * place of the row (the row itself when the run did not fail).
     */
    function wrapFailedRun(row, session) {
        if (session.status !== 'failed' || !session.failure) return row;
        var box = document.createElement('div');
        var note = document.createElement('p');
        note.className = 'text-xs text-red-300 mt-1';
        var label = row.querySelector('.min-w-0') || row;
        label.appendChild(note);
        var panel = document.createElement('div');
        panel.className = 'hidden mt-2';
        panel.innerHTML = formatRunFailure(session.failure);
        function sync(open) {
            note.textContent = session.failure.headline + (open ? '' : ' Click to see why and what to do.');
            row.setAttribute('aria-expanded', open ? 'true' : 'false');
        }
        row.classList.add('cursor-pointer', 'hover:border-red-500/30', 'hover:bg-slate-800/60', 'transition-colors');
        row.addEventListener('click', function () {
            sync(!panel.classList.toggle('hidden'));
        });
        sync(false);
        box.appendChild(row);
        box.appendChild(panel);
        return box;
    }

    /*
     * A run that failed while its tab was open: the same panel, in the tab's
     * error section (#error-failure), in place of the raw text. `showError`
     * is the tab's own, which resets the section.
     */
    function presentRunFailure(run, showError) {
        showError(run.failure ? '' : (run.error || 'The run failed.'));
        var slot = document.getElementById('error-failure');
        var text = document.getElementById('error-message');
        if (!slot) return;
        slot.innerHTML = run.failure ? formatRunFailure(run.failure) : '';
        slot.classList.toggle('hidden', !run.failure);
        if (text) text.classList.toggle('hidden', !!run.failure);
    }

    document.addEventListener('click', function (e) {
        var btn = e.target.closest('.run-failure-copy');
        if (!btn) return;
        e.stopPropagation();
        copyTextToClipboard(btn.getAttribute('data-report') || '')
            .then(function () { showCopyToast(btn, 'Copied', true); })
            .catch(function () { showCopyToast(btn, 'Copy failed', false); });
    });

    // Whose score a run's tables show, as the server composed it: the sentence
    // above each table, the words under each Opportunity heading and that
    // heading's hover text. One copy for the three AI tabs
    // (READINESS_CLARITY_PLAN.md, D7).
    function applyWhoseScore(data) {
        // The amber card when the app a run is scored for sits in another
        // category than the niche. Whose score it is, "scored for Pausely",
        // is in the results header (renderRunHeader). The Simulator sends
        // neither: it always scores the app itself.
        var card = document.getElementById('category-note-card');
        if (card) {
            document.getElementById('category-note-text').textContent = data.category_note || '';
            card.classList.toggle('hidden', !data.category_note);
        }
        if (data.scored_for) {
            document.querySelectorAll('.scored-for-note').forEach(function (el) {
                el.textContent = data.scored_for;
                el.classList.remove('hidden');
            });
        }
        if (data.opportunity_subline) {
            document.querySelectorAll('.opp-subline').forEach(function (el) {
                el.textContent = data.opportunity_subline;
            });
        }
        if (data.opportunity_tip) {
            document.querySelectorAll('svg[data-column="opportunity"]').forEach(function (el) {
                el.setAttribute('data-tip', data.opportunity_tip);
            });
        }
    }

    // "Simulate this metadata" under the Researcher's and the Competitor's
    // title and subtitle variants (aso_pro/_includes/simulate_metadata_button.html).
    // The server builds the Simulator link for the run (data.simulate_url:
    // keyword field, storefront, language, keyword lengths, app); this only
    // puts in the title and subtitle the user has selected. Called on every
    // change of the selection, so the link always opens what the card shows.
    function simulateLink(url, title, subtitle) {
        var link = document.getElementById('simulate-metadata-btn');
        if (!link) return;
        if (!url) {
            link.classList.add('hidden');
            return;
        }
        var target = new URL(url, window.location.href);
        target.searchParams.set('title', title || '');
        target.searchParams.set('subtitle', subtitle || '');
        link.href = target.pathname + target.search;
        link.classList.remove('hidden');
    }

    // ---- Results header (AI_DECISION_FIRST_PLAN.md 2.1 and 4) --------------

    var SOURCE_LABELS = {apple: 'Apple Ads popularity', internal: 'RespectASO estimate'};

    // The longest run of whole sentences that fits in max characters, or ''
    // when even the first sentence is longer (copy rule: a tip is short).
    function cutAtSentence(text, max) {
        text = String(text || '').trim();
        if (text.length <= max) return text;
        var out = '';
        var end = /[.!?](?=\s|$)/g;
        var match;
        while ((match = end.exec(text)) && match.index < max) out = text.slice(0, match.index + 1);
        return out;
    }

    // The hover of "scored for Pausely": the Overview's old long sentence,
    // cut to a tip's length at a sentence end, or the short label.
    function scoredForTip(data) {
        var whose = data.overview_scored_for;
        var label = data.scored_for_label || '';
        if (!whose) return label;
        return cutAtSentence('Scored for ' + whose.who + whose.detail, 120) || label;
    }

    /**
     * One title and one meta line over a run's tabs: "United States ·
     * scored for Pausely · Apple Ads popularity". opts: {title, country,
     * scoredFor: {words, tip} or null, appName, source: 'apple' |
     * 'internal' | ''}. When the run's popularity source is not the one new
     * runs use, a small amber chip at the end says so (it used to follow the
     * "Popularity source used in this analysis" line).
     */
    function renderResultsHeader(opts) {
        var title = document.getElementById('results-title');
        if (title) title.textContent = opts.title || '';
        var meta = document.getElementById('results-meta');
        if (!meta) return;
        meta.textContent = '';
        var parts = [];
        if (opts.country) {
            parts.push(document.createTextNode(window.CountryPicker
                ? window.CountryPicker.name(opts.country) : String(opts.country).toUpperCase()));
        }
        if (opts.scoredFor && opts.scoredFor.words) {
            var who = document.createElement('span');
            who.className = 'cursor-help border-b border-dotted border-slate-500';
            who.textContent = opts.scoredFor.words;
            if (opts.scoredFor.tip) who.setAttribute('data-tip', opts.scoredFor.tip);
            parts.push(who);
        }
        if (opts.appName) parts.push(document.createTextNode(opts.appName));
        if (SOURCE_LABELS[opts.source]) parts.push(document.createTextNode(SOURCE_LABELS[opts.source]));
        parts.forEach(function (part, i) {
            if (i) meta.appendChild(document.createTextNode(' · '));
            meta.appendChild(part);
        });
        var current = window.POPULARITY_SOURCE || 'internal';
        if (SOURCE_LABELS[opts.source] && opts.source !== current) {
            var chip = document.createElement('span');
            chip.className = 'chip ml-2 border-amber-500/30 bg-amber-500/10 text-amber-200';
            chip.textContent = 'differs from your current selection. New runs use ' +
                (current === 'apple' ? 'Apple Ads popularity' : 'the RespectASO estimate');
            meta.appendChild(chip);
        }
    }

    // The header of an AI Researcher or AI Competitor run, from its results.
    function renderRunHeader(data, title) {
        var label = data.scored_for_label || '';
        renderResultsHeader({
            title: title,
            country: data.country,
            scoredFor: label ? {words: label.charAt(0).toLowerCase() + label.slice(1), tip: scoredForTip(data)} : null,
            source: data.popularity_source_used || '',
        });
    }

    // ---- Keyword chips on the suggested metadata (2.5) -----------------------

    // Words as the App Store matches them: letters and digits in any
    // language, split at spaces and punctuation, case folded.
    function words(text) {
        return String(text || '').toLowerCase().split(/[^\p{L}\p{N}]+/u).filter(Boolean);
    }

    function containsPhrase(hay, needle) {
        for (var i = 0; i + needle.length <= hay.length; i++) {
            var j = 0;
            while (j < needle.length && hay[i + j] === needle[j]) j++;
            if (j === needle.length) return true;
        }
        return false;
    }

    /**
     * Up to three chips for the run's scored keywords a title, subtitle or
     * keyword field contains as whole words, highest Opportunity first, each
     * "app blocker Sweet Spot" with a dot in that tag's colour and what the
     * tag means for that keyword on hover. '' when it contains none.
     * rows: the run's keyword rows ({keyword, opportunity, classification}).
     */
    function targetChips(text, rows) {
        var hay = words(text);
        if (!hay.length || !window.ClassificationBadge) return '';
        var seen = {};
        var hits = (rows || []).filter(function (row) {
            var keyword = row && (row.keyword || row.term);
            if (!keyword || !row.classification) return false;
            var key = words(keyword).join(' ');
            if (!key || seen[key]) return false;
            if (!containsPhrase(hay, key.split(' '))) return false;
            seen[key] = true;
            return true;
        });
        hits.sort(function (a, b) { return (b.opportunity || 0) - (a.opportunity || 0); });
        // Quiet on purpose (the owner, 2026-10-02): the keyword with a dot in
        // its tag's colour and the tag in grey, not a coloured box per chip.
        return hits.slice(0, 3).map(function (row) {
            var item = window.ClassificationBadge.get(row.classification);
            if (!item) return '';
            return '<span class="inline-flex items-center gap-1.5 whitespace-nowrap text-xs text-slate-300 cursor-help" ' +
                'data-tip="' + escapeHtml(row.classification_tip || item.description) + '">' +
                '<span class="h-1.5 w-1.5 shrink-0 rounded-full ' + escapeHtml(item.bar || 'bg-slate-500') + '" aria-hidden="true"></span>' +
                escapeHtml(row.keyword || row.term) + '<span class="text-slate-500">' + escapeHtml(row.classification) + '</span></span>';
        }).join('');
    }

    // ---- Title and subtitle repeats (shared by the three tabs) ---------------

    var DUPE_STOP_WORDS = new Set(['the', 'and', 'for', 'with', 'from', 'your', 'you', 'that', 'this', 'are',
        'was', 'has', 'have', 'not', 'but', 'all', 'can', 'app', 'pro', 'new', 'get', 'our', 'its', 'a', 'an',
        'in', 'on', 'to', 'of', 'is', 'it', 'by', 'at', 'or', 'be', 'as', 'do', 'no', 'so', 'if', 'my', 'up',
        'us', 'we', 'he', 'me']);

    // Words of three letters or more that are not stop words.
    function getSignificantWords(text) {
        var found = (text || '').toLowerCase().match(/\b\w{3,}\b/g) || [];
        return found.filter(function (w) { return !DUPE_STOP_WORDS.has(w); });
    }

    // The text, escaped, with the words in dupeWords underlined in amber.
    function highlightDupeWords(text, dupeWords) {
        if (!dupeWords || dupeWords.size === 0) return escapeHtml(text);
        return String(text || '').split(/(\w+)/).map(function (part) {
            if (/^\w+$/.test(part) && dupeWords.has(part.toLowerCase()) && part.length >= 3 &&
                    !DUPE_STOP_WORDS.has(part.toLowerCase())) {
                return '<span class="text-amber-400 underline decoration-wavy decoration-amber-400/50">' +
                    escapeHtml(part) + '</span>';
            }
            return escapeHtml(part);
        }).join('');
    }

    // The words a subtitle repeats from its title.
    function findTitleSubtitleDupes(title, subtitle) {
        var titleSet = new Set(getSignificantWords(title));
        return new Set(getSignificantWords(subtitle).filter(function (w) { return titleSet.has(w); }));
    }

    // ---- Title and subtitle variants (2.5) -----------------------------------

    // One choice of a title or a subtitle, picked like a radio button (the
    // owner, 2026-10-02): a filled purple dot and a purple tint when picked,
    // "Recommended" as a small green word, the keywords it targets below,
    // its length and a copy button on the right.
    function variantCard(kind, text, opts) {
        var selected = text === (kind === 'title' ? opts.selectedTitle : opts.selectedSubtitle);
        var recommended = text === ((opts.recommended || {})[kind] || '');
        var over = text.length > 30;
        var chips = targetChips(text, opts.rows);
        var row = document.createElement('div');
        row.setAttribute('role', 'radio');
        row.setAttribute('aria-checked', selected ? 'true' : 'false');
        row.tabIndex = 0;
        row.className = 'group flex cursor-pointer items-start gap-3 rounded-lg border px-3 py-2.5 transition-colors ' +
            'focus:outline-none focus-visible:ring-2 focus-visible:ring-purple-500 ' +
            (selected ? 'border-purple-400/60 bg-purple-500/10' : 'border-white/10 hover:border-white/20 hover:bg-white/[0.03]');
        row.innerHTML =
            '<span class="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full border-2 ' +
                (selected ? 'border-purple-400' : 'border-slate-500 group-hover:border-slate-400') + '" aria-hidden="true">' +
                (selected ? '<span class="h-2 w-2 rounded-full bg-purple-400"></span>' : '') + '</span>' +
            '<span class="min-w-0 flex-1">' +
                '<span class="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">' +
                    '<span class="text-sm font-medium text-white">' + escapeHtml(text) + '</span>' +
                    (recommended ? '<span class="text-2xs font-semibold uppercase tracking-wide text-emerald-300">Recommended</span>' : '') +
                '</span>' +
                (chips ? '<span class="mt-1.5 flex flex-wrap gap-x-4 gap-y-1">' + chips + '</span>' : '') +
            '</span>' +
            '<span class="shrink-0 pt-0.5 text-xs tabular-nums ' + (over ? 'font-medium text-red-400' : 'text-slate-400') + '">' +
                text.length + '/30' + (over ? ', too long' : '') + '</span>' +
            '<button type="button" data-copy-variant class="-m-1 shrink-0 rounded p-1 text-slate-400 transition-colors hover:text-white" ' +
                'title="Copy" aria-label="Copy">' + (opts.copyIcon || '') + '</button>';
        function pick() { if (!selected) opts.onSelect(kind, text); }
        row.addEventListener('click', pick);
        row.addEventListener('keydown', function (e) {
            if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pick(); }
        });
        var copy = row.querySelector('[data-copy-variant]');
        copy.addEventListener('click', function (e) { e.stopPropagation(); opts.copy(text, copy); });
        return row;
    }

    /**
     * The keyword field in Your new listing. When the pick is not the
     * recommended pair the field was made for, the words it repeats from the
     * picked title or subtitle are struck through in amber, with a note:
     * Apple already reads those, so they waste characters. With that pair,
     * nothing (a grey "which is fine" note only added text, the owner,
     * 2026-10-02).
     * One copy for the three tabs; prefix is "meta" or "sug".
     */
    function updateKeywordOverlap(prefix, title, subtitle, validated) {
        var field = validated.keyword_field || '';
        var note = document.getElementById(prefix + '-keywords-overlap');
        var shown = document.getElementById(prefix + '-keywords');
        var coverage = document.getElementById('coverage-combo-label');
        var recommendedPair = title === validated.title && subtitle === validated.subtitle;
        if (coverage) {
            coverage.textContent = recommendedPair ? 'recommended' : 'recommended (not your current pick)';
            coverage.className = recommendedPair ? 'text-slate-400' : 'text-amber-400';
        }
        var read = new Set(((title || '') + ' ' + (subtitle || '')).toLowerCase().match(/\b\w+\b/g) || []);
        function repeats(token) {
            return (token.match(/\b\w+\b/g) || []).some(function (w) { return read.has(w) && !DUPE_STOP_WORDS.has(w); });
        }
        var tokens = field.split(',').map(function (k) { return k.trim().toLowerCase(); }).filter(Boolean);
        var repeated = tokens.filter(repeats);
        if (shown) {
            shown.innerHTML = tokens.map(function (token) {
                return repeats(token) && !recommendedPair
                    ? '<span class="text-amber-400 line-through decoration-amber-400/50">' + escapeHtml(token) + '</span>'
                    : '<span class="text-white">' + escapeHtml(token) + '</span>';
            }).join('<span class="text-slate-500">,</span>');
        }
        if (!note) return;
        if (!repeated.length || recommendedPair) {
            note.classList.add('hidden');
            note.innerHTML = '';
            return;
        }
        var wasted = repeated.reduce(function (sum, token) { return sum + token.length + 1; }, 0);
        note.classList.remove('hidden');
        note.innerHTML =
            '<div class="rounded-lg border border-amber-500/20 bg-amber-900/20 px-3 py-2">' +
            '<p class="text-xs font-medium text-amber-300">' + repeated.length + ' keyword' + (repeated.length > 1 ? 's repeat' : ' repeats') +
                ' a word of your title or subtitle, about ' + wasted + ' characters you could use for new words.</p>' +
            '<p class="mt-0.5 text-xs text-amber-200/70">This keyword field was made for the recommended title and subtitle.</p>' +
            '</div>';
    }

    // A note in Your new listing: a heading and a real list, amber for what
    // is worth a look, grey for what RespectASO already fixed (the owner,
    // 2026-10-02: hyphen-led lines of loose amber text read as a log).
    function listNote(el, items, heading, tone) {
        if (!el) return;
        if (!items || !items.length) {
            el.classList.add('hidden');
            el.innerHTML = '';
            return;
        }
        var amber = tone === 'amber';
        el.classList.remove('hidden');
        el.innerHTML =
            '<div class="rounded-lg border px-3 py-2 ' + (amber ? 'border-amber-500/20 bg-amber-900/20' : 'border-white/10 bg-slate-800/50') + '">' +
            '<p class="text-xs font-medium ' + (amber ? 'text-amber-300' : 'text-slate-300') + '">' + escapeHtml(heading) + '</p>' +
            '<ul class="mt-1 list-disc space-y-0.5 pl-4 text-xs ' + (amber ? 'text-amber-200/80' : 'text-slate-400') + '">' +
            items.map(function (item) { return '<li>' + escapeHtml(item) + '</li>'; }).join('') + '</ul></div>';
    }

    // What is worth a second look in the picked title and subtitle: a word
    // the subtitle repeats from the title, and room left unused.
    function pickWarnings(elId, title, subtitle) {
        var items = [];
        var skip = new Set(['the', 'and', 'for', 'app', 'with', 'your', 'that', 'this', 'from']);
        function significant(text) {
            return ((text || '').toLowerCase().match(/\b\w{3,}\b/g) || []).filter(function (w) { return !skip.has(w); });
        }
        if (title && subtitle) {
            var inTitle = new Set(significant(title));
            var repeated = Array.from(new Set(significant(subtitle).filter(function (w) { return inTitle.has(w); }))).sort();
            if (repeated.length) {
                items.push('The subtitle repeats ' + repeated.join(', ') + ' from the title; a new word would reach more searches.');
            }
        }
        if (title && title.length < 27) {
            items.push('The title uses ' + title.length + ' of 30 characters; a keyword would fill the rest.');
        }
        if (subtitle && subtitle.length < 27) {
            items.push('The subtitle uses ' + subtitle.length + ' of 30 characters; a descriptive word or a keyword would fill the rest.');
        }
        listNote(document.getElementById(elId), items, 'Worth a look before you ship', 'amber');
    }

    // What RespectASO already corrected in the AI's metadata.
    function showFixes(elId, fixes) {
        listNote(document.getElementById(elId), fixes || [], 'Fixed for you', 'grey');
    }

    // The picked title and subtitle in Your new listing, with their length,
    // a copy button, and the words the subtitle repeats from the title.
    function fillListing(root, opts, repeats) {
        [['title', opts.selectedTitle || ''], ['subtitle', opts.selectedSubtitle || '']].forEach(function (pair) {
            var kind = pair[0], text = pair[1];
            var value = root.querySelector('[data-pick="' + kind + '"]');
            if (value) value.innerHTML = highlightDupeWords(text, repeats);
            var length = root.querySelector('[data-pick-length="' + kind + '"]');
            if (length) {
                length.textContent = text.length + '/30';
                length.className = 'text-xs tabular-nums ' + (text.length > 30 ? 'font-medium text-red-400' : 'text-slate-400');
            }
            var copy = root.querySelector('[data-pick-copy="' + kind + '"]');
            if (copy) copy.onclick = function () { opts.copy(text, copy); };
        });
    }

    /**
     * Suggested Metadata on the AI Researcher, the AI Competitor and the
     * Simulator (aso_pro/_includes/suggested_metadata.html): the title and
     * subtitle choices, each with the scored keywords it contains
     * (targetChips) and the AI's own note about it on hover, and the pick in
     * Your new listing. Picking a choice calls opts.onSelect(kind, text) and
     * the page redraws; the copy buttons copy.
     *
     * opts: {titles, subtitles (containers), titleVariants, subtitleVariants
     * ([{text, note}]), recommended ({title, subtitle}), selectedTitle,
     * selectedSubtitle, rows (the run's keyword rows), copyIcon,
     * copy(text, button), onSelect(kind, text)}
     */
    function renderMetadataVariants(opts) {
        var repeats = findTitleSubtitleDupes(opts.selectedTitle, opts.selectedSubtitle);
        [['title', opts.titles, opts.titleVariants], ['subtitle', opts.subtitles, opts.subtitleVariants]]
            .forEach(function (set) {
                var container = set[1];
                if (!container) return;
                container.textContent = '';
                (set[2] || []).forEach(function (variant) {
                    var row = variantCard(set[0], variant.text || '', opts);
                    if (variant.note) row.title = variant.note;
                    container.appendChild(row);
                });
            });
        var root = opts.titles && opts.titles.closest('[data-suggested-metadata]');
        if (root) fillListing(root, opts, repeats);
    }

    // ---- Overview helpers ----------------------------------------------------

    /**
     * Shows the first n items of a list and a quiet "Show all {n}" button
     * after it that shows the rest (Top apps in this niche, the Niche Map).
     */
    function showFirst(list, n) {
        if (!list) return;
        var old = list.nextElementSibling;
        if (old && old.hasAttribute('data-show-all')) old.remove();
        var items = Array.prototype.slice.call(list.children);
        if (items.length <= n) return;
        items.slice(n).forEach(function (item) { item.classList.add('hidden'); });
        var button = document.createElement('button');
        button.type = 'button';
        button.setAttribute('data-show-all', '');
        button.className = 'btn-quiet mt-2 -ml-2';
        button.textContent = 'Show all ' + items.length;
        button.addEventListener('click', function () {
            items.forEach(function (item) { item.classList.remove('hidden'); });
            button.remove();
        });
        list.insertAdjacentElement('afterend', button);
    }

    // "See all 126 keywords" and "See all suggestions" on the What we found
    // card: open that tab of the results with the page's own tab switcher.
    document.addEventListener('click', function (e) {
        var button = e.target.closest('[data-switch-tab]');
        if (!button || typeof window.setActiveTab !== 'function') return;
        e.preventDefault();
        window.setActiveTab(button.getAttribute('data-switch-tab'));
        var results = document.getElementById('results-section');
        if (results) results.scrollIntoView({block: 'start'});
    });

    // The sentence a failed request carries: the server's own "error" when it
    // answered with JSON (every endpoint and aso/error_views.py do), a plain
    // sentence otherwise, never the raw body.
    function errorFrom(response) {
        return response.text().then(function (text) {
            try {
                var data = JSON.parse(text);
                if (data && data.error) { return data.error; }
            } catch (e) { /* not JSON: fall through */ }
            return 'RespectASO could not load this (error ' + response.status + '). Try again.';
        });
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
        formatRunStatusBadge: formatRunStatusBadge,
        formatRunFailure: formatRunFailure,
        wrapFailedRun: wrapFailedRun,
        presentRunFailure: presentRunFailure,
        applyWhoseScore: applyWhoseScore,
        simulateLink: simulateLink,
        errorFrom: errorFrom,
        cutAtSentence: cutAtSentence,
        scoredForTip: scoredForTip,
        renderResultsHeader: renderResultsHeader,
        renderRunHeader: renderRunHeader,
        targetChips: targetChips,
        renderMetadataVariants: renderMetadataVariants,
        updateKeywordOverlap: updateKeywordOverlap,
        pickWarnings: pickWarnings,
        showFixes: showFixes,
        DUPE_STOP_WORDS: DUPE_STOP_WORDS,
        getSignificantWords: getSignificantWords,
        highlightDupeWords: highlightDupeWords,
        findTitleSubtitleDupes: findTitleSubtitleDupes,
        showFirst: showFirst,
        shortAppName: shortAppName,
        timeAgo: timeAgo,
        recentRunRow: recentRunRow,
        syncRecentFilter: syncRecentFilter,
    };
    window.escapeHtml = escapeHtml;
    window.formatSourceLabel = formatSourceLabel;
    window.getSourceColor = getSourceColor;
    window.getSourceTooltip = getSourceTooltip;
    window.fmt = fmt;
    window.formatDownloadCell = formatDownloadCell;
    window.formatSourceBadge = formatSourceBadge;
    window.formatRunStatusBadge = formatRunStatusBadge;
    window.formatRunFailure = formatRunFailure;
    window.wrapFailedRun = wrapFailedRun;
    window.presentRunFailure = presentRunFailure;
    window.getSignificantWords = getSignificantWords;
    window.highlightDupeWords = highlightDupeWords;
    window.findTitleSubtitleDupes = findTitleSubtitleDupes;
})();
