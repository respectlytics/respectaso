// Settings, Mac App (aso/templates/aso/settings_mac_app.html).
// - "Open Notification Settings" asks the server to open System Settings at
//   RespectASO's notifications (aso.settings_views.open_notification_settings_view):
//   the window's WebKit view cannot open a System Settings link itself.
// - Coming back to the window redraws the cards whose state lives in macOS, so
//   allowing notifications or approving the login item there shows here at once.
(function () {
    'use strict';

    function csrf(form) {
        var field = form.querySelector('input[name=csrfmiddlewaretoken]');
        return field ? field.value : '';
    }

    document.addEventListener('submit', function (event) {
        var form = event.target.closest('form[data-open-notification-settings]');
        if (!form) return;
        event.preventDefault();
        var failed = document.querySelector('[data-open-failed]');
        fetch(form.getAttribute('action'), {method: 'POST', credentials: 'same-origin', headers: {'X-CSRFToken': csrf(form)}})
            .then(function (response) { return response.ok ? response.json() : {opened: false}; })
            .catch(function () { return {opened: false}; })
            .then(function (answer) {
                if (!failed) return;
                failed.textContent = answer.error || failed.dataset.openFailed;
                failed.classList.toggle('hidden', !!answer.opened);
            });
    });

    // The window coming to the front again sends focus (System Settings was in
    // front of it) or visibilitychange (it was hidden or covered); one redraw each time.
    var last = 0;
    function redraw() {
        if (document.hidden || !window.InPlace || Date.now() - last < 1000) return;
        last = Date.now();
        InPlace.load(window.location.href, {
            background: true, history: 'none',
            swap: function (doc) {
                InPlace.swapSection(doc, 'login-item');
                InPlace.swapSection(doc, 'notifications');
            }
        });
    }
    window.addEventListener('focus', redraw);
    document.addEventListener('visibilitychange', redraw);
})();
