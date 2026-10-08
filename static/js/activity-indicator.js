/**
 * ActivityIndicator - the one place in the top bar that shows background
 * work on every page: AI runs and the run queue (static/js/run-queue.js),
 * keyword searches and country scans (static/js/job-strip.js).
 *
 * Each source reports its state; the indicator shows something running
 * (spinner, short label, percent) or waiting. When all work ends while the
 * page is open, it signals "Done" briefly in the same footprint and goes;
 * a task that finished earlier never shows it. "N waiting" counts queued runs. The
 * button opens #activity-panel, where each source draws its own detail.
 *
 *   ActivityIndicator.report('queue', {running: {label, pct}, waiting: 2,
 *                                      done: {label, failed}} | null)
 *
 * Starting anything that runs in the background shows a short note under the
 * top bar that then flies into the pill (docs/development/ACTIVITY_CENTER_PLAN.md):
 *
 *   ActivityIndicator.announce({queued: true, label: '12 keywords in the United States',
 *                               current: 'the current search', eta: 180})
 *
 * The daily update of tracked keywords reports here too: the pill polls
 * aso:auto_refresh_status (data-daily-url on #activity-root).
 */
(function () {
    'use strict';

    var sources = {};

    function byId(id) { return document.getElementById(id); }

    // After work ends: a short signal in the badge's own footprint, then
    // gone. Only an end seen on this page is signalled; a task that finished
    // earlier never brings the badge back.
    var SIGNAL_MS = 3200, FADE_MS = 400;
    var wasActive = false;
    var activeSources = {};          // sources that ran something since work began here
    var signal = null;               // {timer} while the done signal shows

    function setIcons(spinner, done, failed) {
        byId('activity-spinner').classList.toggle('hidden', !spinner);
        byId('activity-done').classList.toggle('hidden', !done);
        byId('activity-failed').classList.toggle('hidden', !failed);
    }

    function hide() {
        var pill = byId('activity-pill');
        pill.classList.add('hidden');
        pill.classList.remove('activity-celebrate', 'activity-stopped', 'opacity-0');
        pill.style.width = '';
        byId('activity-signal').classList.add('hidden');
        byId('activity-pct').classList.remove('hidden');
        close();
    }

    function stopSignal() {
        if (!signal) return;
        clearTimeout(signal.timer);
        signal = null;
        var pill = byId('activity-pill');
        pill.classList.remove('activity-celebrate', 'activity-stopped', 'opacity-0');
        pill.style.width = '';
        byId('activity-signal').classList.add('hidden');
        byId('activity-pct').classList.remove('hidden');
    }

    function showSignal(done) {
        var pill = byId('activity-pill');
        var failed = !!(done && done.failed);
        pill.style.width = pill.offsetWidth + 'px';   // keep the footprint: nothing beside it moves
        setIcons(false, !failed, failed);
        byId('activity-name').textContent = '';
        byId('activity-pct').classList.add('hidden');
        // The word fits the wide badge; the compact one says it with the icon alone.
        var word = byId('activity-signal');
        word.textContent = failed ? 'Stopped' : 'Done';
        word.classList.toggle('hidden', !window.matchMedia('(min-width: 1440px)').matches);
        byId('activity-waiting').classList.add('hidden');
        pill.setAttribute('aria-label', failed ? 'Stopped' : 'Done');
        pill.classList.add('activity-celebrate');
        pill.classList.toggle('activity-stopped', failed);
        signal = {timer: setTimeout(function () {
            pill.classList.add('opacity-0');
            signal.timer = setTimeout(function () { signal = null; hide(); }, FADE_MS);
        }, SIGNAL_MS)};
    }

    function render() {
        var pill = byId('activity-pill');
        if (!pill) return;
        var running = null, waiting = 0;
        Object.keys(sources).forEach(function (key) {
            var state = sources[key];
            if (key !== 'announce' && state && (state.running || state.waiting)) sources.announce = null;
        });
        Object.keys(sources).forEach(function (key) {
            var state = sources[key];
            if (!state) return;
            if (state.running && !running) running = state.running;
            waiting += state.waiting || 0;
            if (state.running || state.waiting) activeSources[key] = true;
        });

        if (!running && !waiting) {
            if (wasActive) {          // the work just ended here: signal it once
                wasActive = false;
                // How it ended, from the sources that were working (an older
                // finished task elsewhere says nothing about this one).
                var done = null;
                Object.keys(activeSources).forEach(function (key) {
                    var state = sources[key];
                    if (state && state.done && (!done || state.done.failed)) done = state.done;
                });
                activeSources = {};
                pill.classList.remove('hidden');
                showSignal(done);
            } else if (!signal) {
                hide();
            }
            return;
        }

        stopSignal();
        wasActive = true;
        pill.classList.remove('hidden');
        setIcons(true, false, false);
        // From 1440 px the pill names the task; below, it stays small so the bar fits.
        var name = running ? running.label : 'Starting next';
        var pct = running && running.pct !== null && running.pct !== undefined ? running.pct + '%' : '';
        byId('activity-name').textContent = name;
        byId('activity-pct').textContent = pct;
        pill.setAttribute('aria-label', [name, pct, waiting ? waiting + ' waiting' : ''].join(' ').trim());
        byId('activity-waiting-count').textContent = waiting ? (window.matchMedia('(min-width: 1440px)').matches ? String(waiting) : '+' + waiting) : '';
        byId('activity-waiting').classList.toggle('hidden', !waiting);
    }

    function open() {
        var panel = byId('activity-panel');
        var pill = byId('activity-pill');
        if (!panel || !pill) return;
        panel.classList.remove('hidden');
        pill.setAttribute('aria-expanded', 'true');
    }

    function close() {
        var panel = byId('activity-panel');
        var pill = byId('activity-pill');
        if (panel) panel.classList.add('hidden');
        if (pill) pill.setAttribute('aria-expanded', 'false');
    }

    function toggle() {
        var panel = byId('activity-panel');
        if (panel && panel.classList.contains('hidden')) open(); else close();
    }

    document.addEventListener('click', function (event) {
        var root = byId('activity-root');
        if (!root) return;
        if (event.target.closest('#activity-pill')) { toggle(); return; }
        if (!root.contains(event.target)) close();
    });
    document.addEventListener('keydown', function (event) {
        if (event.key === 'Escape') close();
    });

    // --- the note that flies into the pill (UI_REDESIGN_PLAN.md 5.2) --------

    var NOTE_MS = 6000, FLY_MS = 400, ANNOUNCE_MS = 8000;
    var FOOTNOTE = 'One task at a time, so every result is fresh and complete.';

    // A wait in words, from the one copy every page loads (country-picker.js).
    function timeText(seconds) {
        return (window.CountryPicker && seconds) ? CountryPicker.durationText(seconds) : '';
    }

    function startedLine(etaSeconds) {
        var time = timeText(etaSeconds);
        if (!time) return 'You can keep working.';
        time = time.indexOf('about ') === 0 ? 'About ' + time.slice(6) : 'About ' + time;
        return time + '. You can keep working.';
    }

    function queuedLine(current, etaSeconds) {
        var time = timeText(etaSeconds);
        return 'Starts after ' + (current || 'the current task') + (time ? ', in ' + time : '') + '.';
    }

    var announceTimer = null;

    function announce(note) {
        note = note || {};
        var old = byId('activity-note');
        if (old) old.remove();
        var box = document.createElement('div');
        box.id = 'activity-note';
        box.setAttribute('role', 'status');
        // A clear order (the owner, 2026-10-02): the state in small coloured
        // capitals, the task as the one strong line, what happens next a step
        // quieter, the footnote quietest. Whole class strings, for Tailwind.
        box.className = 'fixed right-6 top-14 z-50 w-72 rounded-xl border border-white/10 bg-[#1e293b] px-3.5 py-3 shadow-xl shadow-black/40 cursor-pointer transition-all duration-[400ms] ease-in';
        box.innerHTML = '<p class="flex items-center gap-1.5 text-2xs font-semibold uppercase tracking-wider ' +
                (note.queued ? 'text-purple-300' : 'text-emerald-300') + '">' +
                '<span class="h-1.5 w-1.5 rounded-full ' + (note.queued ? 'bg-purple-300' : 'bg-emerald-400') + '"></span><span></span></p>' +
            '<p class="mt-1 truncate text-sm font-semibold text-white"></p>' +
            '<p class="mt-0.5 text-xs text-slate-300"></p>' +
            '<p class="mt-2 text-2xs text-slate-500"></p>';
        var lines = box.querySelectorAll('p');
        lines[0].lastChild.textContent = note.queued ? 'Queued' : 'Started';
        lines[1].textContent = note.label || '';
        lines[2].textContent = note.queued ? queuedLine(note.current, note.eta) : startedLine(note.eta);
        lines[3].textContent = FOOTNOTE;
        document.body.appendChild(box);

        // The pill shows at once, so the note has somewhere to land.
        sources.announce = note.queued ? {running: null, waiting: 1}
                                       : {running: {label: note.label || 'Starting', pct: null}, waiting: 0};
        render();
        // The panel's job row asks again, so a search or a scan started here shows in it,
        // and the update of tracked keywords is asked again once it has paused for it.
        if (window.JobStrip && JobStrip.poll) JobStrip.poll();
        setTimeout(pollDaily, DAILY_AFTER_START_MS);
        clearTimeout(announceTimer);
        announceTimer = setTimeout(function () {
            if (sources.announce) { sources.announce = null; render(); }
        }, ANNOUNCE_MS);

        var timer = null, hovered = false;
        function fly() {
            var pill = byId('activity-pill');
            if (!box.parentNode) return;
            if (!pill || pill.classList.contains('hidden')) { box.remove(); return; }
            var from = box.getBoundingClientRect(), to = pill.getBoundingClientRect();
            var dx = (to.left + to.width / 2) - (from.left + from.width / 2);
            var dy = (to.top + to.height / 2) - (from.top + from.height / 2);
            box.style.transform = 'translate(' + dx + 'px,' + dy + 'px) scale(0.15)';
            box.style.opacity = '0';
            setTimeout(function () {
                box.remove();
                pill.classList.remove('animate-pill-pulse');
                void pill.offsetWidth;
                pill.classList.add('animate-pill-pulse');
            }, FLY_MS);
        }
        function arm() {
            clearTimeout(timer);
            timer = setTimeout(function () { if (!hovered) fly(); }, NOTE_MS);
        }
        box.addEventListener('mouseenter', function () { hovered = true; clearTimeout(timer); });
        box.addEventListener('mouseleave', function () { hovered = false; arm(); });
        box.addEventListener('click', function () { clearTimeout(timer); box.remove(); open(); });
        arm();
    }

    // --- the daily update of tracked keywords -----------------------------

    var DAILY_MS = 10000;
    var DAILY_AFTER_START_MS = 3000;    // a refresh pauses within a read or two (aso/scheduler.py give_way)
    var dailyTimer = null;

    function showDaily(status) {
        var row = byId('activity-daily');
        var running = !!(status && status.running && status.total);
        // Paused while the user's other tasks go first (aso/scheduler.py
        // give_way): it waits like a queued run, and carries on by itself.
        var paused = running && !!status.paused;
        var counts = running
            ? (status.completed || 0).toLocaleString('en-US') + ' of ' + status.total.toLocaleString('en-US')
            : '';
        if (row) {
            row.classList.toggle('hidden', !running);
            row.textContent = '';
            if (running) {
                var line = document.createElement('p');
                line.textContent = 'Updating tracked keywords: ' + (paused ? 'paused at ' : '') + counts;
                row.appendChild(line);
            }
            if (paused) {
                var note = document.createElement('p');
                note.className = 'mt-0.5 text-xs text-slate-400';
                note.textContent = 'Carries on when your other tasks finish';
                row.appendChild(note);
            }
        }
        sources.daily = !running ? null
            : paused ? {running: null, waiting: 1}
            : {running: {label: 'Updating tracked keywords',
                         pct: Math.round(100 * (status.completed || 0) / status.total)}, waiting: 0};
        render();
    }

    function pollDaily() {
        clearTimeout(dailyTimer);
        var root = byId('activity-root');
        var url = root && root.dataset && root.dataset.dailyUrl;
        if (!url) return;
        if (document.visibilityState === 'visible') {
            fetch(url).then(function (r) { return r.ok ? r.json() : null; })
                .then(function (status) { if (status) showDaily(status); })
                .catch(function () {});
        }
        dailyTimer = setTimeout(pollDaily, DAILY_MS);
    }
    document.addEventListener('visibilitychange', function () {
        if (document.visibilityState === 'visible') pollDaily();
    });
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', pollDaily);
    else pollDaily();

    window.ActivityIndicator = {
        report: function (source, state) {
            sources[source] = state;
            render();
        },
        open: open,
        close: close,
        announce: announce,
        startedLine: startedLine,
        queuedLine: queuedLine
    };
})();
