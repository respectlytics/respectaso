/**
 * popularity-banner.js - keeps the popularity-source banner region LIVE.
 *
 * The banners (choose-source, Apple signed-out/expired/needs-test, soft
 * staleness notice) are server-rendered into #popularity-banner-region by
 * the aso/partials/popularity_banner.html partial. This module refetches
 * that partial and swaps it in whenever the underlying state may have
 * changed, so banners appear and disappear WITHOUT a page reload:
 *   - on a 60s timer,
 *   - whenever the tab regains focus/visibility,
 *   - immediately via window.refreshPopularityBanner() (called by the
 *     settings page after sign-in / sign-out / test / source switches).
 *
 * The swapped HTML must stay script-free (innerHTML never executes
 * scripts): all behavior, including dismissing the staleness notice,
 * lives here. Whether that notice shows is the server's to say: a
 * dismissal is kept per install (aso/ui_state.py) and the partial leaves a
 * dismissed notice out.
 */
(function () {
    'use strict';

    var REFRESH_INTERVAL_MS = 60000;

    function region() {
        return document.getElementById('popularity-banner-region');
    }

    window.dismissAppleStaleBanner = function () {
        var banner = document.getElementById('apple-stale-banner');
        var el = region();
        if (!banner || !el) return;
        banner.remove();
        fetch(el.dataset.staleDismissUrl, {
            method: 'POST',
            headers: { 'X-CSRFToken': el.dataset.csrf }
        }).catch(function () { /* gone from this page; the next one asks again */ });
    };

    window.refreshPopularityBanner = function () {
        var el = region();
        if (!el || !el.dataset.bannerUrl) return;
        fetch(el.dataset.bannerUrl, { headers: { 'X-Requested-With': 'fetch' } })
            .then(function (r) { return r.ok ? r.text() : null; })
            .then(function (html) {
                if (html === null) return;
                if (html.trim() !== el.innerHTML.trim()) {
                    el.innerHTML = html;
                }
            })
            .catch(function () { /* transient - next tick retries */ });
    };

    document.addEventListener('DOMContentLoaded', function () {
        if (!region()) return;
        setInterval(window.refreshPopularityBanner, REFRESH_INTERVAL_MS);
        document.addEventListener('visibilitychange', function () {
            if (!document.hidden) window.refreshPopularityBanner();
        });
    });
})();
