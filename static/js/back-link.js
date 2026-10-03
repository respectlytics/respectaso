/*
 * The way back after a jump (aso/back_link.py): a link in a page's content
 * that opens another page carries "back", the page it was clicked on, so a
 * page reached by a button such as Simulate this metadata says "Back to ..."
 * at its top left (the owner, 2026-10-02). Navigation (the top bar, a
 * section's tabs: any <nav>) carries nothing: arriving there is no jump. Nor
 * does a link to the same page, another site, a download or the way back
 * itself.
 */
(function () {
    'use strict';

    function here() {
        var url = new URL(window.location.href);
        url.searchParams.delete('back');
        return url.pathname + url.search;
    }

    document.addEventListener('click', function (e) {
        var link = e.target.closest ? e.target.closest('a[href]') : null;
        if (!link || link.closest('nav') || link.hasAttribute('download') || link.hasAttribute('data-back-link')) return;
        var url;
        try { url = new URL(link.getAttribute('href'), window.location.href); } catch (err) { return; }
        if (url.origin !== window.location.origin || url.pathname === window.location.pathname) return;
        if (url.searchParams.has('back')) return;
        url.searchParams.set('back', here());
        link.setAttribute('href', url.pathname + url.search + url.hash);
    });
})();
