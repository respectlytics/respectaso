// Mail links in the Mac app (loaded on every page by aso/templates/aso/base.html).
// The window's WebKit view cannot open a mailto: link: a click on one did nothing
// (Help, Contact us). In the Mac app the click goes to macOS through the window's
// bridge (desktop/main.py, open_external), which opens the person's mail app. In a
// browser (the Docker edition) nothing changes: the browser opens mail links itself.
// Links to websites need nothing: the window hands every target="_blank" link to the
// default browser.
(function () {
    'use strict';

    document.addEventListener('click', function (event) {
        var link = event.target.closest('a[href^="mailto:"]');
        var api = window.pywebview && window.pywebview.api;
        if (!link || !api || !api.open_external) return;
        event.preventDefault();
        api.open_external(link.getAttribute('href'));
    });
})();
