/*
 * The data-tip tooltip.
 *
 * Any element with a data-tip attribute gets a hoverable tooltip, positioned
 * fixed so it escapes overflow containers and scrolling tables. Loaded from
 * the base template, so every page can explain a control without shipping its
 * own copy: it used to live inside dashboard.html, which meant a data-tip on
 * any other page did nothing at all.
 *
 * The tooltip never catches the mouse, and it belongs to one element only.
 * It used to be hoverable, to stay open while the mouse travelled onto it.
 * On a short, filtered table a row's tooltip opened over the rows below, and
 * pointing at one of those landed on the tooltip, so it kept showing the
 * first row's text: "ranks #2" over a row that ranks #14 (2026-09-23). Now
 * whatever is under the pointer gets its own tooltip, and a tooltip closes
 * as soon as its element is gone or the page scrolls under it.
 */
(function () {

    const tip = document.createElement('div');
    tip.className = 'fixed z-[9999] max-w-[320px] bg-slate-900 border border-white/10 rounded-lg px-3 py-2 text-[11px] text-slate-300 leading-snug shadow-xl opacity-0 transition-opacity duration-150';
    tip.style.display = 'none';
    tip.style.pointerEvents = 'none';  // the element underneath stays hoverable
    document.body.appendChild(tip);

    let hideTimeout = null;
    let current = null;  // the element the tooltip belongs to
    let anchor = null;   // where that element was when the tooltip opened

    function show(e) {
        // Capturing mouseenter/focusin also fire with the document itself as
        // target (cursor entering the window), which has no .closest().
        if (!(e.target instanceof Element)) return;
        const el = e.target.closest('[data-tip]');
        if (!el) return;
        clearTimeout(hideTimeout);
        current = el;
        tip.textContent = el.dataset.tip;
        tip.style.display = 'block';

        // Position: prefer above the element, fall below if clipped, clamp to viewport if both
        const rect = el.getBoundingClientRect();
        anchor = { top: rect.top, left: rect.left };
        const tipRect = tip.getBoundingClientRect();
        const margin = 8;
        const vw = window.innerWidth;
        const vh = window.innerHeight;

        // Vertical: try above, then below, then whichever side has more room
        let top = rect.top - tipRect.height - margin;
        if (top < margin) {
            const below = rect.bottom + margin;
            if (below + tipRect.height <= vh - margin) {
                top = below;  // fits below
            } else {
                // Doesn't fit above OR below — pick the side with more space and clamp
                const spaceAbove = rect.top;
                const spaceBelow = vh - rect.bottom;
                if (spaceBelow >= spaceAbove) {
                    top = Math.max(margin, vh - tipRect.height - margin);
                } else {
                    top = margin;
                }
            }
        }

        // Horizontal: center on trigger, clamp to viewport
        let left = rect.left + rect.width / 2 - tipRect.width / 2;
        if (left < margin) left = margin;
        if (left + tipRect.width > vw - margin) left = vw - tipRect.width - margin;

        tip.style.top = top + 'px';
        tip.style.left = left + 'px';
        requestAnimationFrame(() => tip.style.opacity = '1');
    }

    function hideNow() {
        clearTimeout(hideTimeout);
        current = null;
        tip.style.opacity = '0';
        tip.style.display = 'none';
    }

    function hideSoon(e) {
        // Leaving a child of the element keeps the tooltip; leaving the
        // element itself closes it after a short grace, which a move onto
        // the next element cancels (show() clears the timer).
        if (e && e.target instanceof Element && current && e.target !== current) return;
        clearTimeout(hideTimeout);
        hideTimeout = setTimeout(() => {
            current = null;
            tip.style.opacity = '0';
            setTimeout(() => { if (!current) tip.style.display = 'none'; }, 150);
        }, 100);
    }

    document.addEventListener('mouseenter', show, true);
    document.addEventListener('mouseleave', hideSoon, true);
    document.addEventListener('focusin', show, true);
    document.addEventListener('focusout', hideSoon, true);
    // A tooltip never outlives its element or its place: the table it
    // belonged to was replaced (filtering, sorting and paging swap it in
    // place), or the page moved under it.
    // Only when its own element moved: a scroll elsewhere on the page (a
    // progress log, another panel) leaves it alone.
    document.addEventListener('scroll', () => {
        if (!current || !anchor) return;
        const rect = current.getBoundingClientRect();
        if (Math.abs(rect.top - anchor.top) > 1 || Math.abs(rect.left - anchor.left) > 1) hideNow();
    }, true);
    new MutationObserver(() => {
        if (current && !current.isConnected) hideNow();
    }).observe(document.body, { childList: true, subtree: true });
})();
