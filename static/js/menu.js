/**
 * Menus in the top bar and on pages: a [data-menu-button] opens the
 * [data-menu] that is its next sibling (docs/development/APP_SHELL_PLAN.md).
 * One menu is open at a time; a click outside, Escape, or a click on an item
 * closes it. The Activity panel keeps its own script.
 */
(function () {
    'use strict';

    function closeAll(except) {
        document.querySelectorAll('[data-menu]').forEach(function (menu) {
            if (menu === except) return;
            menu.classList.add('hidden');
            var button = menu.previousElementSibling;
            if (button && button.hasAttribute('data-menu-button')) button.setAttribute('aria-expanded', 'false');
        });
    }

    document.addEventListener('click', function (event) {
        var button = event.target.closest('[data-menu-button]');
        if (button) {
            var menu = button.nextElementSibling;
            if (!menu || !menu.hasAttribute('data-menu')) return;
            var opening = menu.classList.contains('hidden');
            closeAll(opening ? menu : null);
            menu.classList.toggle('hidden', !opening);
            button.setAttribute('aria-expanded', opening ? 'true' : 'false');
            return;
        }
        if (event.target.closest('[data-menu] a, [data-menu] button')) { closeAll(null); return; }
        if (!event.target.closest('[data-menu]')) closeAll(null);
    });
    document.addEventListener('keydown', function (event) {
        if (event.key === 'Escape') closeAll(null);
    });
})();
