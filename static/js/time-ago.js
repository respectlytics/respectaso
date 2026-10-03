/**
 * Keep "refreshed 3 hours, 12 minutes ago" true while a page stays open.
 *
 * The server writes the words once, with Django's timesince filter, inside
 * <time datetime="..." data-time-ago>. A Dashboard left open all day kept
 * saying "refreshed 2 minutes ago" in the evening. This redraws every such
 * element once a minute and when the window is looked at again.
 *
 * since() is the twin of django.utils.timesince.timesince (two adjacent
 * units, calendar months, "0 minutes" under a minute, a no-break space
 * between number and unit), so the words do not change their shape when the
 * script takes over from the server.
 *
 * The element holds the whole phrase, "3 hours ago" or "just now" under a
 * minute, as the server's ago filter (aso/templatetags/aso_tags.py) writes it.
 *
 *   TimeAgo.render(root)   redraw the elements under root (after a swap)
 *   TimeAgo.start()        redraw the page now, every minute, and on return
 *   TimeAgo.since(d, now)  the words for two Date objects
 *   TimeAgo.ago(d, now, largestOnly)  "3 hours, 12 minutes ago" or "just now";
 *                          largestOnly keeps the largest unit, "3 hours ago"
 *   TimeAgo.parse(iso)     a Date from what Django writes, or null
 */
(function () {
    'use strict';

    var UNITS = ['year', 'month', 'week', 'day', 'hour', 'minute'];
    var CHUNKS = [60 * 60 * 24 * 7, 60 * 60 * 24, 60 * 60, 60];
    // Django's own table: February is always 28 days here.
    var MONTHS_DAYS = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
    var TICK_MS = 60000;

    function phrase(unit, n) {
        return n + ' ' + unit + (n === 1 ? '' : 's');
    }

    function msOfDay(d) {
        return ((d.getUTCHours() * 60 + d.getUTCMinutes()) * 60 + d.getUTCSeconds()) * 1000
            + d.getUTCMilliseconds();
    }

    // The server stores and renders UTC, so the calendar arithmetic is UTC too.
    function since(d, now) {
        if (Math.floor((now - d) / 1000) <= 0) return phrase('minute', 0);

        var totalMonths = (now.getUTCFullYear() - d.getUTCFullYear()) * 12
            + (now.getUTCMonth() - d.getUTCMonth());
        if (d.getUTCDate() > now.getUTCDate()
                || (d.getUTCDate() === now.getUTCDate() && msOfDay(d) > msOfDay(now))) {
            totalMonths -= 1;
        }
        var years = Math.floor(totalMonths / 12);
        var months = totalMonths % 12;

        var pivot = d;
        if (years || months) {
            var year = d.getUTCFullYear() + years;
            var month = d.getUTCMonth() + months;   // 0 based
            if (month > 11) { month -= 12; year += 1; }
            pivot = new Date(Date.UTC(year, month, Math.min(MONTHS_DAYS[month], d.getUTCDate()),
                d.getUTCHours(), d.getUTCMinutes(), d.getUTCSeconds()));
        }
        var remaining = (now - pivot) / 1000;
        var parts = [years, months];
        CHUNKS.forEach(function (chunk) {
            var count = Math.floor(remaining / chunk);
            parts.push(count);
            remaining -= chunk * count;
        });

        var i = 0;
        while (i < parts.length && parts[i] === 0) i++;
        if (i === parts.length) return phrase('minute', 0);
        var out = [];
        while (i < UNITS.length && out.length < 2 && parts[i] !== 0) {
            out.push(phrase(UNITS[i], parts[i]));
            i++;
        }
        return out.join(', ');
    }

    function ago(d, now, largestOnly) {
        var words = since(d, now);
        if (words === phrase('minute', 0)) return 'just now';
        return (largestOnly ? words.split(', ')[0] : words) + ' ago';
    }

    function parse(iso) {
        // Django writes microseconds; not every engine reads more than three
        // digits of a fraction.
        var d = new Date(String(iso || '').replace(/(\.\d{3})\d+/, '$1'));
        return isNaN(d) ? null : d;
    }

    function render(root) {
        var now = new Date();
        (root || document).querySelectorAll('time[data-time-ago][datetime]').forEach(function (el) {
            var d = parse(el.getAttribute('datetime'));
            if (!d) return;
            var text = ago(d, now);
            if (el.textContent !== text) el.textContent = text;
        });
    }

    function start() {
        render(document);
        setInterval(function () { if (!document.hidden) render(document); }, TICK_MS);
        document.addEventListener('visibilitychange', function () {
            if (!document.hidden) render(document);
        });
    }

    window.TimeAgo = { render: render, start: start, since: since, ago: ago, parse: parse };
})();
