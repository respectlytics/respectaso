// The Get set up strip of the Apps page (aso/templates/aso/partials/setup_checklist.html).
// Every listener is on document, so the strip and its chip can be swapped in place.
(function () {
    function csrf() { return (document.cookie.match(/csrftoken=([^;]+)/) || [])[1] || ''; }
    function redraw() {
        if (!window.InPlace) { window.location.reload(); return; }
        InPlace.load(window.location.href, {
            background: true, history: 'none',
            swap: function (doc) {
                InPlace.swapSection(doc, 'apps-setup');
                InPlace.swapSection(doc, 'apps-setup-chip');
            }
        });
    }
    function send(url, body) {
        return fetch(url, {method: 'POST', credentials: 'same-origin', headers: {'X-CSRFToken': csrf()}, body: body})
            .then(function (response) {
                if (!response.ok) throw new Error('refused');
                return (response.headers.get('Content-Type') || '').indexOf('json') >= 0 ? response.json() : {};
            })
            .then(function (answer) {
                // An answer can carry a sentence for the person (System Settings did not open).
                if (answer && answer.error && window.showAlert) showAlert(answer.error);
                redraw();
            })
            .catch(function () { if (window.showAlert) showAlert('That could not be saved. Try again.'); });
    }
    // A tile that needs a look is often settled outside RespectASO (allowing
    // notifications in System Settings): coming back to the window redraws the strip.
    var last = 0;
    function redrawOnReturn() {
        if (document.hidden || Date.now() - last < 1000) return;
        if (!document.querySelector('#setup-strip [data-state="warn"]')) return;
        last = Date.now();
        redraw();
    }
    window.addEventListener('focus', redrawOnReturn);
    document.addEventListener('visibilitychange', redrawOnReturn);
    document.addEventListener('click', function (event) {
        var button = event.target.closest('[data-setup-do]');
        if (!button) return;
        var body = new FormData();
        body.append('do', button.dataset.setupDo);
        send(button.dataset.setupUrl || document.getElementById('apps-setup').dataset.url, body);
    });
    document.addEventListener('submit', function (event) {
        var form = event.target.closest('form[data-setup-post]');
        if (!form) return;
        event.preventDefault();
        send(form.getAttribute('action'), new FormData(form));   // a field named "action" shadows the form's own property
    });
})();
