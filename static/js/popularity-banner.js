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
 * scripts) - all behavior, including the dismissible staleness notice,
 * lives here.
 */
(function () {
    'use strict';

    var REFRESH_INTERVAL_MS = 60000;

    function region() {
        return document.getElementById('popularity-banner-region');
    }

    /** Reveal the soft staleness notice unless THIS expiry was dismissed. */
    function initStaleNotice() {
        var banner = document.getElementById('apple-stale-banner');
        if (!banner) return;
        var key = 'aso_dismiss_apple_expired_' + (banner.dataset.expiredAt || 'unknown');
        try {
            if (!localStorage.getItem(key)) banner.classList.remove('hidden');
        } catch (e) {
            banner.classList.remove('hidden');
        }
    }

    window.dismissAppleStaleBanner = function () {
        var banner = document.getElementById('apple-stale-banner');
        if (!banner) return;
        var key = 'aso_dismiss_apple_expired_' + (banner.dataset.expiredAt || 'unknown');
        try { localStorage.setItem(key, '1'); } catch (e) { /* still hide */ }
        banner.classList.add('hidden');
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
                    initStaleNotice();
                }
            })
            .catch(function () { /* transient - next tick retries */ });
    };

    document.addEventListener('DOMContentLoaded', function () {
        if (!region()) return;
        initStaleNotice();
        setInterval(window.refreshPopularityBanner, REFRESH_INTERVAL_MS);
        document.addEventListener('visibilitychange', function () {
            if (!document.hidden) window.refreshPopularityBanner();
        });
    });
})();
