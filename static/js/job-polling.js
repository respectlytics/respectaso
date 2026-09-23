/*
 * The generic half of a background job client.
 *
 * Both long running jobs in the app poll the same way and say the same things
 * about time, so the formatting and the poll loop live here once:
 * keyword-search-job.js and opportunity-scan.js are the two job specific
 * halves on top. Before this, "about 3 min left" was written twice and could
 * read differently on two pages.
 *
 * Nothing here knows what a job is. It formats, fetches and schedules.
 */
(function () {
    'use strict';

    function esc(value) {
        return String(value === null || value === undefined ? '' : value)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    function fmt(n) {
        return Number(n || 0).toLocaleString();
    }

    function plural(n, one, many) {
        return n === 1 ? one : (many || one + 's');
    }

    function durationText(seconds) {
        if (!seconds || seconds <= 0) { return ''; }
        if (seconds < 60) { return Math.max(10, Math.round(seconds / 10) * 10) + ' seconds'; }
        var mins = Math.ceil(seconds / 60);
        return 'about ' + mins + ' minute' + (mins === 1 ? '' : 's');
    }

    function etaText(seconds) {
        var text = durationText(seconds);
        return text ? text + ' left' : '';
    }

    function byId(id) { return document.getElementById(id); }

    function setText(id, text) {
        var el = byId(id);
        if (el) { el.textContent = text; }
    }

    function setHtml(id, html) {
        var el = byId(id);
        if (el) { el.innerHTML = html; }
    }

    function toggle(id, show) {
        var el = byId(id);
        if (el) { el.classList.toggle('hidden', !show); }
    }

    function urlFor(template, id) {
        // URL templates carry a literal 0 where the row id goes, so Django
        // can reverse them in the template without knowing the id yet.
        return template.replace(/\/0\//, '/' + id + '/');
    }

    function post(url, fields, csrfToken) {
        var body = new FormData();
        Object.keys(fields || {}).forEach(function (key) { body.append(key, fields[key]); });
        return fetch(url, {
            method: 'POST',
            headers: { 'X-CSRFToken': csrfToken },
            body: body
        }).then(function (response) {
            return response.json().then(function (data) {
                if (!response.ok) { throw new Error(data.error || 'Request failed'); }
                return data;
            });
        });
    }

    /*
     * A poll loop that stops cleanly. `intervalFor(data)` returns the delay in
     * milliseconds, or 0 to stop polling until something calls start() again.
     */
    function poller(options) {
        var timer = null;
        var stopped = false;

        function schedule(data) {
            if (timer) { clearTimeout(timer); timer = null; }
            if (stopped) { return; }
            var delay = options.intervalFor(data);
            if (delay > 0) { timer = setTimeout(refresh, delay); }
        }

        function refresh() {
            return fetch(options.url, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    options.onData(data);
                    schedule(data);
                    return data;
                })
                .catch(function (err) {
                    if (options.onError) { options.onError(err); }
                    schedule(null);
                });
        }

        return {
            start: function () { stopped = false; return refresh(); },
            stop: function () {
                stopped = true;
                if (timer) { clearTimeout(timer); timer = null; }
            },
            refresh: refresh,
            schedule: schedule
        };
    }

    window.JobPolling = {
        esc: esc,
        fmt: fmt,
        plural: plural,
        durationText: durationText,
        etaText: etaText,
        byId: byId,
        setText: setText,
        setHtml: setHtml,
        toggle: toggle,
        urlFor: urlFor,
        post: post,
        poller: poller
    };
})();
