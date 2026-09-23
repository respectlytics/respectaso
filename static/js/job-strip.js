/*
 * The global progress strip.
 *
 * It shows whichever long job is running, a keyword search or a country scan,
 * and it knows about neither. The server composes the sentence (aso/job_strip.py)
 * because the sentence depends on data; this only paints it and polls.
 */
(function () {
    'use strict';

    var POLL_MS = 5000;
    var cfg = null;
    var timer = null;

    function byId(id) { return document.getElementById(id); }

    function toggle(id, show) {
        var el = byId(id);
        if (el) { el.classList.toggle('hidden', !show); }
    }

    function render(state) {
        var strip = byId('search-job-strip');
        if (!strip) { return; }
        if (!state) { strip.classList.add('hidden'); return; }

        var text = byId('sjs-text');
        if (text) { text.textContent = state.text; }
        var link = byId('sjs-link');
        if (link) {
            link.textContent = state.link_label;
            link.setAttribute('href', state.link_url);
        }
        toggle('sjs-spinner', state.icon === 'spinner');
        toggle('sjs-pause-icon', state.icon === 'pause');
        toggle('sjs-done-icon', state.icon === 'done');
        toggle('sjs-bar', !!state.show_bar);
        var fill = byId('sjs-fill');
        if (fill && state.show_bar) { fill.style.width = (state.progress_percent || 0) + '%'; }
        strip.classList.remove('hidden');
    }

    function refresh() {
        fetch(cfg.stateUrl)
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (data) {
                if (!data) { return; }
                render(data.strip);
                if (data.strip && data.strip.active) { timer = setTimeout(refresh, POLL_MS); }
            })
            .catch(function () { timer = setTimeout(refresh, POLL_MS); });
    }

    function init(options) {
        cfg = options;
        render(options.state);
        if (options.state && options.state.active) { timer = setTimeout(refresh, POLL_MS); }
    }

    window.JobStrip = { init: init, render: render };
})();
