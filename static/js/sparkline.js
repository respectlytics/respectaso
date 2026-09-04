/**
 * Inline sparklines for the Top Search Terms table.
 *
 * Shared by the live page (aso_pro/templates/aso_pro/top_terms.html) and
 * the sample-data preview (aso/templates/aso/partials/top_terms_preview.html)
 * so both draw identical charts. Every element matching `.spark-btn` carries
 * data-series='[[iso_week, popularity], ...]' and contains an
 * <svg class="spark-svg"> (72x22) that receives a polyline plus an end dot,
 * or a dashed baseline when fewer than two points exist.
 */
(function () {
    function renderSparklines(root) {
        (root || document).querySelectorAll('.spark-btn').forEach(function (el) {
            var series = [];
            try { series = JSON.parse(el.dataset.series || '[]'); } catch (e) { series = []; }
            var svg = el.querySelector('.spark-svg');
            if (!svg) return;
            if (series.length < 2) {
                svg.innerHTML = '<line x1="4" y1="11" x2="68" y2="11" stroke="rgba(148,163,184,0.25)" stroke-width="1.5" stroke-dasharray="2,3"/>';
                return;
            }
            var values = series.map(function (p) { return p[1]; });
            var min = Math.min.apply(null, values), max = Math.max.apply(null, values);
            var range = (max - min) || 1;
            var points = values.map(function (v, i) {
                var x = 4 + (i / (values.length - 1)) * 64;
                var y = 18 - ((v - min) / range) * 14;
                return x.toFixed(1) + ',' + y.toFixed(1);
            });
            var last = points[points.length - 1].split(',');
            svg.innerHTML = '<polyline points="' + points.join(' ')
                + '" fill="none" stroke="rgb(56,189,248)" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round" opacity="0.9"/>'
                + '<circle cx="' + last[0] + '" cy="' + last[1] + '" r="2" fill="rgb(56,189,248)"/>';
        });
    }
    window.renderSparklines = renderSparklines;
})();
