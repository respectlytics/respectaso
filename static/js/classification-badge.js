/**
 * The keyword classifications, as the server publishes them.
 *
 * There used to be six copies of this table. dashboard.html and
 * opportunity-scan.js each held a classificationBadge() with every label, icon
 * and sentence spelled out, byte for byte identical; progress-ticker.js held an
 * icon map and a style map with a comment saying "matches dashboard
 * classificationBadge", which is the note a duplicate always leaves behind;
 * _app_summary.html spelled the seven out twice more in Django template tags.
 * Every one of them still described the classifier as it behaved two rewrites
 * ago, so a Sweet Spot was "high search volume + low competition" on a screen
 * where a Sweet Spot means ten or more downloads a day at a position you
 * could actually hold.
 *
 * So there is no table here either. aso/scoring.py owns it, base.html publishes
 * it as JSON, and this reads it. A label added, recoloured or reworded there
 * changes every screen at once.
 */
(function () {
    'use strict';

    var byLabel = null;
    var order = [];

    function catalog() {
        if (byLabel) return byLabel;
        byLabel = Object.create(null);
        var node = document.getElementById('classification-legend-data');
        if (!node) return byLabel;
        try {
            JSON.parse(node.textContent).forEach(function (item) {
                byLabel[item.label] = item;
                order.push(item.label);
            });
        } catch (err) {
            // A page without the legend still renders; badges just go plain.
        }
        return byLabel;
    }

    // Safe in text and in an attribute value: tips carry app names, and an
    // app name can hold a quote.
    function esc(value) {
        const div = document.createElement('div');
        div.textContent = value == null ? '' : String(value);
        return div.innerHTML.replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    /** Everything known about one label, or null. */
    function get(label) {
        return catalog()[label] || null;
    }

    function icon(label) {
        var item = get(label);
        return item ? item.icon : '';
    }

    /** {badge, ring} for the progress ticker's arc and chip. */
    function style(label) {
        var item = get(label) || get('Low Volume');
        return item
            ? { badge: item.css, ring: item.ring }
            : { badge: 'bg-slate-800 border-white/10 text-slate-300', ring: '#64748b' };
    }

    /**
     * The ASO Targeting card, from a payload's `targeting` object:
     * {icon, label, css, description, basis}. Returns '' when a response
     * carries no targeting, so an older cached row degrades quietly.
     * `withBasis: false` leaves out the score's explanation, for a card that
     * already prints it under the score.
     */
    function render(targeting, options) {
        if (!targeting || !targeting.label) return '';
        const withBasis = !options || options.withBasis !== false;
        const basis = withBasis && targeting.basis
            ? `<p class="text-[10px] text-slate-500 mt-1.5 leading-relaxed">${esc(targeting.basis)}</p>`
            : '';
        return `
            <div class="${esc(targeting.css)} border rounded-lg p-2.5">
                <p class="text-[10px] uppercase tracking-wide text-slate-500 mb-1">ASO Targeting</p>
                <p class="text-xs font-medium">${esc(targeting.icon)} ${esc(targeting.label)}</p>
                <p class="text-[11px] text-slate-400 mt-0.5 leading-relaxed">${esc(targeting.description)}</p>
                ${basis}
            </div>`;
    }

    /**
     * Where a tag stands, best first (0 for Sweet Spot), as the legend lists
     * them; tables sort their Insight column by it rather than by the name.
     * Unknown labels sort last.
     */
    function rank(label) {
        catalog();
        var i = order.indexOf(label);
        return i < 0 ? order.length : i;
    }

    /**
     * The tag chip every keyword table draws, with what it means for that row
     * on hover. Twin of the Dashboard's Insight cell (dashboard.html), the
     * same markup, so a tag looks the same on every table.
     */
    function chipHtml(label, tip) {
        var item = get(label);
        if (!item) return '';
        return '<span class="' + esc(item.css) + ' border rounded px-1.5 py-0.5 text-[11px] whitespace-nowrap '
            + 'inline-flex items-center gap-0.5 cursor-help" data-tip="' + esc(tip || item.description) + '">'
            + esc(item.icon) + ' ' + esc(item.label) + '</span>';
    }

    window.ClassificationBadge = {
        rank: rank,
        chipHtml: chipHtml,
        get: get,
        icon: icon,
        style: style,
        render: render,
    };
})();
