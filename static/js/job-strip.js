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

    var SHORT = {keyword_search: 'Keyword search', opportunity_scan: 'Country scan'};

    // Tell the activity indicator in the top bar (static/js/activity-indicator.js).
    function report(state) {
        if (!window.ActivityIndicator) { return; }
        // Not shown: nothing to say, or a search or scan that waits in the
        // queue, which the queue's Up next lists and counts (aso/job_strip.py).
        if (!state || !state.shown) { window.ActivityIndicator.report('job', null); return; }
        var label = SHORT[state.kind] || 'Search';
        if (state.icon === 'spinner') {
            window.ActivityIndicator.report('job', {running: {label: label, pct: state.show_bar ? (state.progress_percent || 0) : null}});
        } else if (state.icon === 'done') {
            window.ActivityIndicator.report('job', {done: {label: label}});
        } else {
            window.ActivityIndicator.report('job', {done: {text: label + ' paused', failed: true}});
        }
    }

    function render(state) {
        var strip = byId('search-job-strip');
        if (!strip) { report(state); return; }
        report(state);
        if (!state || !state.shown) { strip.classList.add('hidden'); return; }

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

    // Something just started on this page (ActivityIndicator.announce). The
    // page drew the strip with nothing active and stopped asking, so a search
    // started here never reached the activity panel's job row.
    function poll() {
        if (!cfg) { return; }
        clearTimeout(timer);
        refresh();
    }

    window.JobStrip = { init: init, render: render, poll: poll };
})();
