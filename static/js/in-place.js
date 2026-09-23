/**
 * Update part of a page from its own URL, without reloading the page.
 *
 * Filtering, sorting, paging and small row actions used to set
 * window.location, which reloads the page and throws the reader back to the
 * top. Every page that shows a filterable list goes through this instead:
 * fetch the page at the new URL, let the caller swap its sections from the
 * answer, and keep the reader where they were.
 *
 *   InPlace.load(url, {swap: function (doc) {...}, anchor: element})
 *   InPlace.load(form.action, {method: 'POST', body: new FormData(form),
 *                              history: 'none', swap: ...})
 *
 * - The URL in the address bar follows (history.replaceState), so a reload,
 *   a restart or coming back to the tab shows the same view. Nothing is
 *   pushed: a filter typed a letter at a time must not become forty steps
 *   of "back".
 * - The element that had focus keeps it, with its text and caret, even when
 *   the swap replaced it (matched by id). Typing into a filter box carries
 *   on while the list under it changes.
 * - Elements marked data-keep-open keep their open or closed state across a
 *   swap (matched by id), so a dropdown or filter panel does not snap shut
 *   under the pointer.
 * - `anchor` stays at the same place on screen, so a list that gets shorter
 *   or a panel above it that changes height does not move the controls the
 *   reader is using. At the very top of the page it is not needed.
 * - A newer load cancels an older one, so fast typing never lands an old
 *   answer on top of a newer one. A background refresh ({background: true},
 *   e.g. a finished search) never cancels what the reader just asked for:
 *   it steps aside, since that answer brings fresh data anyway.
 * - `reveal` names an element to bring into view when it has scrolled
 *   above the screen, for "next page": the new page starts at its first row,
 *   not at the bottom of the old one, and never at the top of the page.
 * - A POST (a row action, a small form) swaps the page the server answers
 *   with, after its redirect. Pass history: 'none' so the address does not
 *   pick up a one-off parameter such as ?refresh=renamed.
 * - If the fetch fails, the page navigates normally, so the reader still
 *   gets the result, only with a reload. A POST fails loudly instead of
 *   being resent.
 */
(function () {
    'use strict';

    var inflight = null;

    function rememberFocus() {
        var el = document.activeElement;
        if (!el || !el.id || el === document.body) return null;
        var state = { id: el.id, value: null, start: null, end: null };
        if (typeof el.value === 'string' && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA')) {
            state.value = el.value;
            try {
                state.start = el.selectionStart;
                state.end = el.selectionEnd;
            } catch (e) { /* inputs without a caret */ }
        }
        return state;
    }

    function restoreFocus(state) {
        if (!state) return;
        var el = document.getElementById(state.id);
        if (!el || el === document.activeElement) return;
        if (state.value !== null && el.value !== state.value) {
            // What the reader typed while the answer was on its way wins over
            // the value the server rendered into the new box.
            el.value = state.value;
            el.dispatchEvent(new Event('input', { bubbles: true }));
        }
        el.focus({ preventScroll: true });
        if (state.start !== null) {
            try { el.setSelectionRange(state.start, state.end); } catch (e) { /* not a text box */ }
        }
    }

    function rememberOpen() {
        var open = {};
        document.querySelectorAll('[data-keep-open][id]').forEach(function (el) {
            open[el.id] = !el.classList.contains('hidden');
        });
        return open;
    }

    function restoreOpen(open) {
        Object.keys(open).forEach(function (id) {
            var el = document.getElementById(id);
            if (el) el.classList.toggle('hidden', !open[id]);
        });
    }

    function load(url, options) {
        options = options || {};
        if (inflight && options.background && !inflight.background) {
            return Promise.resolve(null);
        }
        if (inflight) inflight.abort();
        var controller = new AbortController();
        controller.background = !!options.background;
        inflight = controller;

        // At the very top of the page nothing needs holding: what changes
        // above (a new message) pushes the rest down where the reader sees it.
        var anchor = window.scrollY > 0 ? (options.anchor || null) : null;
        var anchorId = anchor && anchor.id;
        var anchorTop = anchor ? anchor.getBoundingClientRect().top : null;

        var method = options.method || 'GET';
        return fetch(url, {
            method: method,
            body: options.body || undefined,
            headers: { 'X-Requested-With': 'XMLHttpRequest' },
            signal: controller.signal,
        }).then(function (resp) {
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
            return resp.text();
        }).then(function (html) {
            if (inflight !== controller) return;
            inflight = null;
            var doc = new DOMParser().parseFromString(html, 'text/html');
            var focus = rememberFocus();
            var open = rememberOpen();
            if (options.history !== 'none') {
                window.history.replaceState(window.history.state, '', url);
            }
            options.swap(doc);
            restoreOpen(open);
            restoreFocus(focus);
            if (anchorId) {
                var fresh = document.getElementById(anchorId);
                if (fresh) {
                    window.scrollBy(0, fresh.getBoundingClientRect().top - anchorTop);
                }
            }
            var reveal = options.reveal && document.getElementById(options.reveal);
            if (reveal && reveal.getBoundingClientRect().top < 0) {
                reveal.scrollIntoView({ block: 'start' });
            }
            return doc;
        }).catch(function (err) {
            if (err && err.name === 'AbortError') return null;
            if (method !== 'GET') {
                if (window.showAlert) window.showAlert('That did not go through. Please try again.', { title: 'Something went wrong' });
                return null;
            }
            if (options.history === 'none') {
                console.warn('In-place update failed:', err);
                return null;
            }
            window.location.href = url;
            return null;
        });
    }

    /**
     * Put the answer's element `id` in place of the one on screen. A section
     * that only exists in the answer (a message, a banner) goes in before
     * `beforeId`; one that no longer exists in the answer is removed.
     */
    function swapSection(doc, id, beforeId) {
        var fresh = doc.getElementById(id);
        var old = document.getElementById(id);
        if (fresh && old) {
            old.replaceWith(fresh);
        } else if (fresh) {
            var before = document.getElementById(beforeId);
            if (before && before.parentNode) before.parentNode.insertBefore(fresh, before);
        } else if (old) {
            old.remove();
        }
    }

    /** "?a=1&b=2" for these params, or the bare path when there are none. */
    function urlFor(params, basePath) {
        var qs = params.toString();
        return qs ? '?' + qs : (basePath || window.location.pathname);
    }

    window.InPlace = { load: load, urlFor: urlFor, swapSection: swapSection };
})();
