/**
 * Ask the app to remember something for this visitor (aso/ui_memory.py),
 * such as the folded App Summary or a country picker's countries.
 *
 *   UiMemory.save('app_summary_folded', true)
 *   UiMemory.save('countries.search', ['us', 'de'])
 *
 * The app keeps it in the Django session and draws the next page with it.
 * The browser's own storage is not used: desktop-compat keeps it out of the
 * app (docs/development/NO_BROWSER_STORAGE_PLAN.md).
 *
 * The screen already shows the change, so this only keeps it for the next
 * page. One request per name is on its way at a time, and a newer value
 * waits for it, so answers never land out of order. A value still waiting
 * when the page goes away is sent then; keepalive lets it finish after the
 * page is gone. A failed save stays silent: it is a convenience.
 *
 * base.html loads this file with the endpoint and the CSRF token on its
 * script tag (data-url, data-csrf).
 */
(function () {
    'use strict';

    var script = document.currentScript;
    var url = script ? script.dataset.url : '';
    var csrf = script ? script.dataset.csrf : '';
    var busy = {};      // name -> true while a request is on its way
    var waiting = {};   // name -> the newest value not sent yet

    function send(name, value) {
        busy[name] = true;
        fetch(url, {
            method: 'POST',
            headers: { 'X-CSRFToken': csrf, 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: name, value: value }),
            keepalive: true
        }).catch(function () { /* the screen already shows it */ }).then(function () {
            busy[name] = false;
            if (Object.prototype.hasOwnProperty.call(waiting, name)) {
                var next = waiting[name];
                delete waiting[name];
                send(name, next);
            }
        });
    }

    function save(name, value) {
        if (!url) { return; }
        if (busy[name]) { waiting[name] = value; return; }
        send(name, value);
    }

    window.addEventListener('pagehide', function () {
        Object.keys(waiting).forEach(function (name) {
            var value = waiting[name];
            delete waiting[name];
            send(name, value);
        });
    });

    window.UiMemory = { save: save };
})();
