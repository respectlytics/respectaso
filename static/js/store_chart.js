/*
 * The Results chart: one metric at a time over the chosen period, with a
 * line for each day with a release (purple, a purple dot on top) and each
 * day with an applied store change (amber, dashed, an amber dot on top), and
 * a readout under it that follows the pointer or the arrow keys. A pointer
 * near a marked line snaps to its day; the readout then names the release
 * or change in the line's colour, beside the day's value in the series
 * colour, and that line's dot grows. Data comes from screens.chart_data via
 * json_script: {dates, series: {metric: [value per day]}, labels,
 * markers: [{i, kind, text}], currency}, one marker per day and kind, its
 * words written by the server. Buttons with data-chart-metric pick the metric.
 */
(function () {
    'use strict';

    var W = 900, H = 240, L = 56, R = 16, T = 24, B = 28;
    var DOT_Y = 10;           // the marker dots sit above the plot, on top of their lines
    var SNAP_PX = 10;         // a pointer this close (screen pixels) to a marked line reads that day
    // No series is purple or amber: those two mean a release and a store change.
    var COLOURS = {proceeds: '#34d399', downloads: '#38bdf8', page_views: '#60a5fa',
                   impressions: '#f472b6', conversion: '#a3e635', purchases: '#2dd4bf'};
    var MARK = {release: {line: '#c084fc', text: 'text-purple-300', dash: ''},
                change: {line: '#fcd34d', text: 'text-amber-300', dash: ' stroke-dasharray="4 4"'}};

    function fmt(metric, value, currency) {
        if (value === null || value === undefined) return 'no report from Apple';
        if (metric === 'conversion') return (value * 100).toFixed(1) + '%';
        if (metric === 'proceeds') {
            return currency + ' ' + (Math.abs(value) >= 1000
                ? Math.round(value).toLocaleString('en-US')
                : value.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2}));
        }
        return Math.round(value).toLocaleString('en-US');
    }

    function short(metric, value, currency) {
        if (metric === 'conversion') return (Math.round(value * 1000) / 10) + '%';
        if (value === 0) return '0';
        var abs = Math.abs(value), text;
        if (abs >= 1e6) text = (value / 1e6).toFixed(1).replace(/\.0$/, '') + 'M';
        else if (abs >= 1e3) text = (value / 1e3).toFixed(1).replace(/\.0$/, '') + 'K';
        else text = (metric === 'proceeds' && abs < 10) ? value.toFixed(1) : Math.round(value).toString();
        return text;
    }

    function day(iso) {
        var d = new Date(iso + 'T00:00:00');
        return d.toLocaleDateString('en-US', {month: 'short', day: 'numeric'});
    }

    // Gridlines at round steps (1, 2 or 5 times a power of ten), so the labels read 0, 5, 10, 15, 20.
    function niceScale(max, whole) {
        if (max <= 0) return {top: whole ? 4 : 1, step: whole ? 1 : 0.25};
        var raw = max / 4, p = Math.pow(10, Math.floor(Math.log10(raw))), step = 10 * p;
        [1, 2, 5, 10].some(function (m) { if (m * p >= raw) { step = m * p; return true; } return false; });
        if (whole && step < 1) step = 1;
        return {top: Math.ceil(max / step - 1e-9) * step, step: step};
    }

    function draw(root, data, metric) {
        var svg = root.querySelector('[data-chart-svg]');
        var values = data.series[metric] || [];
        var n = values.length;
        var known = values.filter(function (v) { return v !== null; });
        var scale = niceScale(Math.max.apply(null, known.concat([0])), metric !== 'conversion' && metric !== 'proceeds');
        var top = scale.top;
        var colour = COLOURS[metric] || '#38bdf8';
        function x(i) { return L + (W - L - R) * (n > 1 ? i / (n - 1) : 0.5); }
        function y(v) { return T + (H - T - B) * (1 - v / top); }
        var out = [];
        for (var gv = 0; gv <= top + 1e-9; gv += scale.step) {
            var gy = y(gv);
            out.push('<line x1="' + L + '" x2="' + (W - R) + '" y1="' + gy + '" y2="' + gy + '" stroke="rgba(148,163,184,0.12)"/>');
            out.push('<text x="' + (L - 8) + '" y="' + (gy + 4) + '" text-anchor="end" font-size="11" fill="#94a3b8">' + short(metric, gv, data.currency) + '</text>');
        }
        var ticks = Math.min(6, n);
        for (var t = 0; t < ticks; t++) {
            var i = Math.round((n - 1) * t / Math.max(ticks - 1, 1));
            out.push('<text x="' + x(i) + '" y="' + (H - 8) + '" text-anchor="middle" font-size="11" fill="#94a3b8">' + day(data.dates[i]) + '</text>');
        }
        // Days Apple has no report for are a gap, never a zero: shaded and named when they lead.
        var firstKnown = values.findIndex(function (v) { return v !== null; });
        if (firstKnown > 0) {
            out.push('<rect x="' + L + '" y="' + T + '" width="' + (x(firstKnown) - L) + '" height="' + (H - T - B) + '" fill="rgba(148,163,184,0.06)"/>');
            if (x(firstKnown) - L > 120) {
                out.push('<text x="' + ((L + x(firstKnown)) / 2) + '" y="' + ((T + H - B) / 2) + '" text-anchor="middle" font-size="11" fill="#64748b">No report from Apple for these days</text>');
            }
        }
        var path = '', segments = [], current = [];
        values.forEach(function (v, i) {
            if (v === null) {
                if (current.length) { segments.push(current); current = []; }
                return;
            }
            path += (current.length ? ' L' : ' M') + x(i).toFixed(1) + ',' + y(v).toFixed(1);
            current.push(i);
        });
        if (current.length) { segments.push(current); }
        segments.forEach(function (seg) {
            if (seg.length < 2) { return; }
            var d = seg.map(function (i, k) { return (k ? 'L' : 'M') + x(i).toFixed(1) + ',' + y(values[i]).toFixed(1); }).join(' ');
            out.push('<path d="' + d + ' L' + x(seg[seg.length - 1]) + ',' + (H - B) + ' L' + x(seg[0]) + ',' + (H - B) + ' Z" fill="' + colour + '" opacity="0.10"/>');
        });
        out.push('<path d="' + path + '" fill="none" stroke="' + colour + '" stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round"/>');
        // One line and one dot per marked day and kind; a day with both stands its release
        // a little left of the day and its change a little right, so each keeps its own line.
        // The offset stays a small part of a day, so on a year's chart neither line leaves its day.
        var step = (W - L - R) / Math.max(n - 1, 1), apart = Math.min(5, Math.max(2, step * 0.3));
        var kinds = {}, drawn = [];
        data.markers.forEach(function (m) { kinds[m.i] = (kinds[m.i] || 0) + 1; });
        data.markers.forEach(function (m) {
            var look = MARK[m.kind] || MARK.change;
            var mx = x(m.i) + (kinds[m.i] > 1 ? (m.kind === 'release' ? -apart : apart) : 0);
            drawn.push({i: m.i, x: mx});
            out.push('<line data-mark-line="' + m.i + '" x1="' + mx + '" x2="' + mx + '" y1="' + DOT_Y + '" y2="' + (H - B) + '" stroke="' + look.line + '" stroke-width="1.2"' + look.dash + '/>');
            out.push('<circle data-mark-dot="' + m.i + '" cx="' + mx + '" cy="' + DOT_Y + '" r="4" fill="' + look.line + '" stroke="#1e293b" stroke-width="2"/>');
        });
        out.push('<line data-chart-cursor x1="0" x2="0" y1="' + T + '" y2="' + (H - B) + '" stroke="rgba(226,232,240,0.35)" visibility="hidden"/>');
        out.push('<circle data-chart-dot r="4" fill="' + colour + '" stroke="#0f172a" stroke-width="2" visibility="hidden"/>');
        svg.innerHTML = out.join('');
        svg.setAttribute('aria-label', (data.labels[metric] || metric) + ' per day');

        var readout = root.querySelector('[data-chart-readout]');
        var cursor = svg.querySelector('[data-chart-cursor]');
        var dot = svg.querySelector('[data-chart-dot]');
        function show(i) {
            i = Math.max(0, Math.min(n - 1, i));
            var v = values[i];
            cursor.setAttribute('x1', x(i)); cursor.setAttribute('x2', x(i));
            cursor.setAttribute('visibility', 'visible');
            if (v !== null) {
                dot.setAttribute('cx', x(i)); dot.setAttribute('cy', y(v));
                dot.setAttribute('visibility', 'visible');
            } else {
                dot.setAttribute('visibility', 'hidden');
            }
            // The readout in parts, each in the colour of what it reads: the value in the
            // series colour, a release in purple, a store change in amber, with their words.
            var parts = [{text: day(data.dates[i]) + ': ' + fmt(metric, v, data.currency), colour: colour}];
            data.markers.forEach(function (m) {
                if (m.i === i) parts.push({text: m.text, cls: (MARK[m.kind] || MARK.change).text});
            });
            readout.textContent = '';
            parts.forEach(function (part, k) {
                if (k) {
                    var gap = document.createElement('span');
                    gap.className = 'text-slate-500';
                    gap.textContent = ' · ';
                    readout.appendChild(gap);
                }
                var span = document.createElement('span');
                span.textContent = part.text;
                if (part.cls) span.className = part.cls;
                else span.style.color = part.colour;
                readout.appendChild(span);
            });
            mark(i);
            root.dataset.chartIndex = i;
        }
        // The marked line of the day being read stands out: a bigger dot, a bolder line.
        function mark(i) {
            svg.querySelectorAll('[data-mark-dot]').forEach(function (el) {
                el.setAttribute('r', String(Number(el.dataset.markDot) === i ? 5.5 : 4));
            });
            svg.querySelectorAll('[data-mark-line]').forEach(function (el) {
                el.setAttribute('stroke-width', Number(el.dataset.markLine) === i ? '2' : '1.2');
            });
        }
        function hide() {
            cursor.setAttribute('visibility', 'hidden');
            dot.setAttribute('visibility', 'hidden');
            readout.textContent = readout.dataset.idle || '';
            mark(-1);
        }
        svg.onmousemove = function (event) {
            var box = svg.getBoundingClientRect();
            var px = (event.clientX - box.left) / box.width * W;
            var i = Math.round((px - L) / (W - L - R) * (n - 1));
            // Near a marked line, read its day: nobody has to land on a line one pixel wide.
            var reach = SNAP_PX * W / box.width, best = null;
            drawn.forEach(function (m) {
                var gap = Math.abs(m.x - px);
                if (gap <= reach && (best === null || gap < best.gap)) best = {i: m.i, gap: gap};
            });
            show(best ? best.i : i);
        };
        svg.onmouseleave = hide;
        svg.onkeydown = function (event) {
            var i = parseInt(root.dataset.chartIndex || String(n - 1), 10);
            if (event.key === 'ArrowLeft') { show(i - 1); event.preventDefault(); }
            if (event.key === 'ArrowRight') { show(i + 1); event.preventDefault(); }
        };
        svg.onfocus = function () { show(parseInt(root.dataset.chartIndex || String(n - 1), 10)); };
        svg.onblur = hide;
        hide();
    }

    function start(root) {
        var source = document.getElementById(root.dataset.chartSource);
        if (!source) return;
        var data = JSON.parse(source.textContent);
        var buttons = root.querySelectorAll('[data-chart-metric]');
        function pick(metric) {
            buttons.forEach(function (b) {
                var on = b.dataset.chartMetric === metric;
                b.setAttribute('aria-pressed', on ? 'true' : 'false');
                b.classList.toggle('section-tab-active', on);
                b.classList.toggle('section-tab', !on);
            });
            draw(root, data, metric);
        }
        buttons.forEach(function (b) { b.addEventListener('click', function () { pick(b.dataset.chartMetric); }); });
        pick(root.dataset.chartDefault || 'downloads');
    }

    document.querySelectorAll('[data-store-chart]').forEach(start);
})();
