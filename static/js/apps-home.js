/**
 * The Apps page follows its data without a reload: when the state the
 * server reports differs from the one the page was drawn from, the page
 * fetches itself and swaps its own part in place (InPlace), keeping the
 * scroll position and the open "N more" folds. An app added in the Add an
 * app dialog (static/js/add-app.js) arrives the same way, at once. The
 * page's own refresh and delete swap #apps-home too, so it is always looked
 * up again rather than kept.
 */
(function () {
    'use strict';
    function home() { return document.getElementById('apps-home'); }
    if (!home() || !window.InPlace) return;
    function swap(doc) {
        // An open "N more" stays open: InPlace keeps only the class "hidden", not a <details>.
        var open = Array.prototype.map.call(
            document.querySelectorAll('#apps-home details[open][id]'), function (el) { return el.id; });
        InPlace.swapSection(doc, 'apps-home');
        InPlace.swapSection(doc, 'apps-period');
        InPlace.swapSection(doc, 'apps-setup');
        InPlace.swapSection(doc, 'apps-setup-chip');
        open.forEach(function (id) { var el = document.getElementById(id); if (el) el.open = true; });
    }
    function refresh() {
        InPlace.load(window.location.href, { background: true, history: 'none', swap: swap });
    }
    // 7 days or 30 days: the numbers and the To do lines of every row come from the same answer,
    // and the number beside Apps follows at once (it counts the period chosen last).
    document.addEventListener('click', function (event) {
        var link = event.target.closest('#apps-period a[data-period]');
        if (!link || event.button || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        if (link.getAttribute('aria-current') === 'true') return;
        InPlace.load(link.href, { swap: swap }).then(function () {
            document.dispatchEvent(new Event('todo:ask'));
        });
    });
    document.addEventListener('todo:state', function (event) {
        var state = event.detail || {};
        var stale = document.getElementById('apps-stale');
        if (state.failed) { if (stale && state.failed >= 3) stale.classList.remove('hidden'); return; }
        if (stale) stale.classList.add('hidden');
        var current = home();
        if (current && state.stamp && state.stamp !== current.dataset.stamp) refresh();
    });
    // The X of a line or a notice changes the count at once.
    document.addEventListener('click', function (event) {
        if (event.target.closest('[data-notice-close]')) {
            setTimeout(function () { document.dispatchEvent(new Event('todo:ask')); }, 400);
        }
    });
    // An app added in the dialog shows in the list without a reload.
    document.addEventListener('app:added', function (event) {
        if (event.detail) event.detail.handled = true;
        refresh();
    });
    // While an app's numbers are being read, ask every 5 seconds instead of every minute.
    setInterval(function () {
        if (document.querySelector('#apps-home [data-reading]')) document.dispatchEvent(new Event('todo:ask'));
    }, 5000);
})();
