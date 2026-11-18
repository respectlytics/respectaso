/* How Get found works (aso_pro/templates/aso_pro/store/how.html): one stage
 * at a time. Next, Back, the dots and the arrow keys move between them; the
 * address keeps #step-N so a stage can be linked to (the stages on the Get
 * found card) and a reload stays on it. Done, on the last stage, returns to
 * Get found. Without this file every stage shows, one under the other. */
(function () {
    'use strict';
    var box = document.querySelector('[data-how]');
    if (!box) { return; }
    var slides = Array.prototype.slice.call(box.querySelectorAll('[data-slide]'));
    var dots = Array.prototype.slice.call(box.querySelectorAll('[data-how-dot]'));
    var back = box.querySelector('[data-how-back]');
    var next = box.querySelector('[data-how-next]');
    var controls = box.querySelector('[data-how-controls]');
    var current = 0;

    function fromHash() {
        var m = /^#step-(\d+)$/.exec(window.location.hash || '');
        var n = m ? parseInt(m[1], 10) - 1 : 0;
        return n >= 0 && n < slides.length ? n : 0;
    }

    function show(n, focus) {
        current = Math.max(0, Math.min(slides.length - 1, n));
        slides.forEach(function (slide, i) { slide.hidden = i !== current; });
        dots.forEach(function (dot, i) { dot.setAttribute('aria-selected', i === current ? 'true' : 'false'); });
        back.classList.toggle('invisible', current === 0);
        next.textContent = current === slides.length - 1 ? 'Done' : 'Next';
        if (window.history && window.history.replaceState) {
            window.history.replaceState(null, '', '#step-' + (current + 1));
        }
        if (focus) { slides[current].querySelector('h2').focus({preventScroll: true}); }
    }

    controls.hidden = false;
    slides.forEach(function (slide) { slide.querySelector('h2').setAttribute('tabindex', '-1'); });
    back.addEventListener('click', function () { show(current - 1, true); });
    next.addEventListener('click', function () {
        if (current === slides.length - 1) { window.location.href = box.dataset.doneUrl; return; }
        show(current + 1, true);
    });
    dots.forEach(function (dot, i) { dot.addEventListener('click', function () { show(i, true); }); });
    document.addEventListener('keydown', function (event) {
        if (event.target.closest('input, textarea, select')) { return; }
        if (event.key === 'ArrowRight') { show(current + 1, true); }
        if (event.key === 'ArrowLeft') { show(current - 1, true); }
    });
    show(fromHash(), false);
}());
