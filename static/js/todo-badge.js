/**
 * The number beside Apps in the top bar, kept current on every page: asked
 * once a minute and whenever the window is looked at again. The answer is
 * also handed to the Apps page (apps-home.js) as the event "todo:state".
 */
(function () {
    'use strict';
    var script = document.currentScript;
    var url = script && script.dataset.url;
    if (!url) return;
    var failures = 0;
    function ask() {
        if (document.hidden) return;      // a hidden window asks again when it is looked at
        fetch(url, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
            .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
            .then(function (state) {
                failures = 0;
                document.querySelectorAll('[data-todo-count]').forEach(function (el) {
                    el.textContent = state.count;
                    el.classList.toggle('hidden', !state.count);
                });
                document.dispatchEvent(new CustomEvent('todo:state', { detail: state }));
            })
            .catch(function () {
                failures += 1;
                document.dispatchEvent(new CustomEvent('todo:state', { detail: { failed: failures } }));
            });
    }
    setInterval(ask, 60000);
    document.addEventListener('visibilitychange', function () { if (!document.hidden) ask(); });
    window.addEventListener('focus', ask);
    document.addEventListener('todo:ask', ask);
})();
