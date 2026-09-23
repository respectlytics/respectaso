/**
 * The difficulty breakdown: one tile per factor that counts toward the score.
 *
 * The factors, their names and their weights come from the server
 * (aso.scoring.difficulty_factor_legend, published once per page in
 * base.html), so a tile can never show a factor the score does not use or a
 * weight it does not apply. The Dashboard result card and the Country
 * Opportunity Finder both draw their grid here; each used to carry its own
 * copy of the list.
 */
(function () {
    'use strict';

    var cached = null;

    function factors() {
        if (cached) return cached;
        var node = document.getElementById('difficulty-factors-data');
        try {
            cached = node ? JSON.parse(node.textContent) : [];
        } catch (err) {
            cached = [];
        }
        return cached;
    }

    function esc(value) {
        var div = document.createElement('div');
        div.textContent = value == null ? '' : String(value);
        return div.innerHTML;
    }

    function tile(factor, value) {
        var val = Number(value) || 0;
        var level = val <= 25 ? 'Low' : val <= 50 ? 'Medium' : val <= 75 ? 'High' : 'Very High';
        var bar = val <= 25 ? 'bg-green-500' : val <= 50 ? 'bg-yellow-500' : val <= 75 ? 'bg-orange-500' : 'bg-red-500';
        var text = val <= 25 ? 'text-green-400' : val <= 50 ? 'text-yellow-400' : val <= 75 ? 'text-orange-400' : 'text-red-400';
        var tip = esc(factor.tip) + '<br><br><strong>Score: ' + Math.round(val) + '/100</strong>. '
            + 'Higher means harder competition. This factor is <strong>' + esc(factor.weight)
            + '%</strong> of the difficulty score.';
        return '<div class="bg-slate-800 rounded p-2 group/tip relative cursor-help">'
            + '<p class="text-slate-500">' + esc(factor.label) + '</p>'
            + '<p class="' + text + ' font-semibold mt-0.5">' + level + '</p>'
            + '<div class="w-full bg-slate-700 rounded-full h-1 mt-1">'
            + '<div class="' + bar + ' h-1 rounded-full" style="width:' + Math.min(val, 100) + '%"></div>'
            + '</div>'
            + '<div class="absolute bottom-full left-1/2 -translate-x-1/2 mb-1.5 w-56 bg-slate-900 border border-white/10 rounded-lg p-2.5 text-[10px] text-slate-300 leading-snug opacity-0 pointer-events-none group-hover/tip:opacity-100 transition-opacity z-50 shadow-lg">'
            + tip + '</div>'
            + '</div>';
    }

    /**
     * The grid for one keyword's stored breakdown, or '' when it has none.
     * ``extraClass`` adds spacing classes to the grid (e.g. "mb-4").
     */
    function gridHtml(breakdown, extraClass) {
        var bd = breakdown || {};
        if (bd.rating_volume === undefined) return '';
        return '<div class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-2 text-xs ' + (extraClass || '') + '">'
            + factors().map(function (factor) { return tile(factor, bd[factor.key]); }).join('')
            + '</div>';
    }

    window.DifficultyFactors = { list: factors, gridHtml: gridHtml };
})();
