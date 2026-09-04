/**
 * keyword-highlight.js - the ONE client-side keyword highlighter for app
 * titles in competitor lists (Dashboard search results, Opportunity).
 *
 * Server-side counterpart: highlight_keyword in aso/templatetags/aso_tags.py.
 * The two MUST emit identical HTML (aso/tests/test_keyword_highlight.py runs
 * this file under node and compares the output with the Django filter).
 *
 * Tiers: the exact phrase, or its run-together spelling ("ScrollLess" for
 * "scroll less") -> green; all words present but scattered -> amber; only
 * some words -> slate. Touching highlights are merged into ONE mark, so a
 * run-together word is never drawn as two padded chips that read like two
 * separate words.
 */
(function (global) {
    'use strict';

    const CLS_EXACT = 'bg-green-500/25 text-green-300 rounded px-0.5';
    const CLS_ALL   = 'bg-amber-500/25 text-amber-300 rounded px-0.5';
    const CLS_PART  = 'bg-slate-400/20 text-slate-300 rounded px-0.5';

    // Same characters as django.utils.html.escape, so both renderers agree.
    function escHtml(s) {
        return String(s)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#x27;');
    }

    function escRe(s) {
        return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    }

    // Run-together spelling of a multi-word keyword ("scroll less" ->
    // "scrollless"); single words have none. Twin of services.compound_form.
    function compoundForm(words) {
        return words.length > 1 ? words.join('') : '';
    }

    // [start, end] of the compound where it starts a word of the title.
    function compoundSpan(title, compound) {
        if (!compound) return null;
        const wordRe = /[\p{L}\p{N}]+/gu;
        let m;
        while ((m = wordRe.exec(title)) !== null) {
            if (m[0].toLowerCase().startsWith(compound)) {
                return [m.index, m.index + compound.length];
            }
        }
        return null;
    }

    function allSpans(title, re) {
        const spans = [];
        let m;
        while ((m = re.exec(title)) !== null) {
            if (m[0] === '') { re.lastIndex++; continue; }
            spans.push([m.index, m.index + m[0].length]);
        }
        return spans;
    }

    function renderMarks(title, spans, cls) {
        const merged = [];
        spans.slice().sort((a, b) => a[0] - b[0] || a[1] - b[1]).forEach(([start, end]) => {
            const last = merged[merged.length - 1];
            if (last && start <= last[1]) last[1] = Math.max(last[1], end);
            else merged.push([start, end]);
        });
        let out = '';
        let pos = 0;
        merged.forEach(([start, end]) => {
            out += escHtml(title.slice(pos, start))
                + '<mark class="' + cls + '">' + escHtml(title.slice(start, end)) + '</mark>';
            pos = end;
        });
        return out + escHtml(title.slice(pos));
    }

    function highlightKeyword(title, keyword) {
        if (!title) return '';
        const titleStr = String(title);
        if (!keyword) return escHtml(titleStr);
        const words = String(keyword).trim().split(/\s+/).filter(Boolean);
        if (!words.length) return escHtml(titleStr);

        const titleLower = titleStr.toLowerCase();
        const kwLower = words.join(' ').toLowerCase();
        const present = words.filter(w => titleLower.includes(w.toLowerCase()));

        if (words.length > 1 && titleLower.includes(kwLower)) {
            const re = new RegExp(escRe(kwLower), 'giu');
            return renderMarks(titleStr, allSpans(titleStr, re), CLS_EXACT);
        }

        const compound = compoundSpan(titleStr, compoundForm(words.map(w => w.toLowerCase())));
        if (compound) return renderMarks(titleStr, [compound], CLS_EXACT);

        if (!present.length) return escHtml(titleStr);
        let cls;
        if (words.length === 1) cls = CLS_EXACT;
        else if (present.length === words.length) cls = CLS_ALL;
        else cls = CLS_PART;
        const re = new RegExp(present.map(escRe).join('|'), 'giu');
        return renderMarks(titleStr, allSpans(titleStr, re), cls);
    }

    global.highlightKeyword = highlightKeyword;
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = { highlightKeyword };
    }
})(typeof window !== 'undefined' ? window : globalThis);
