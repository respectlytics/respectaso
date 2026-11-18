/**
 * A notice's X (aso/partials/notice_close.html): closes the .notice it sits
 * in. A notice whose X carries data-dismiss="key ..." stays closed: its keys
 * go to the app in one request (aso.views.notice_dismiss_view), which keeps
 * them per install (aso/ui_state.py), and the page leaves the notice out.
 *
 * Notice.show(element, text, tone) fills a notice the page shows as things
 * happen (aso/partials/notice_live.html): its words, its tone (success,
 * info, warning, error) and the icon of that tone. Notice.hide(element).
 * One with data-dismiss-url posts to that address instead (What's New).
 *
 * base.html loads this file with the endpoint and the CSRF token on its
 * script tag (data-url, data-csrf). A failed save stays silent: the notice
 * is already closed, and comes back on the next page at worst.
 */
(function () {
    'use strict';

    var script = document.currentScript;
    var url = script ? script.dataset.url : '';
    var csrf = script ? script.dataset.csrf : '';

    var TONES = ['success', 'info', 'warning', 'error'];

    function show(element, text, tone) {
        if (!element) { return; }
        tone = TONES.indexOf(tone) >= 0 ? tone : 'info';
        TONES.forEach(function (t) {
            element.classList.toggle('notice-' + t, t === tone);
            var icon = element.querySelector('[data-tone-icon="' + t + '"]');
            if (icon) { icon.classList.toggle('hidden', t !== tone); }
        });
        var words = element.querySelector('[data-notice-text]') || element;
        words.textContent = text;
        element.classList.remove('hidden');
    }

    function hide(element) {
        if (element) { element.classList.add('hidden'); }
    }

    window.Notice = { show: show, hide: hide };

    document.addEventListener('click', function (event) {
        var button = event.target.closest('[data-notice-close]');
        if (!button) { return; }
        var notice = button.closest('.notice');
        var keys = (button.dataset.dismiss || '').split(' ').filter(Boolean);
        if (notice) { notice.classList.add('hidden'); }
        if (button.dataset.dismissUrl) {
            fetch(button.dataset.dismissUrl, { method: 'POST', headers: { 'X-CSRFToken': csrf }, keepalive: true })
                .catch(function () { /* already closed on screen */ });
        }
        if (!url) { return; }
        if (!keys.length) { return; }
        // Every key in one request: the app keeps them in one file, written whole.
        fetch(url, {
            method: 'POST',
            headers: { 'X-CSRFToken': csrf, 'Content-Type': 'application/json' },
            body: JSON.stringify({ keys: keys }),
            keepalive: true
        }).catch(function () { /* already closed on screen */ });
    });
}());
