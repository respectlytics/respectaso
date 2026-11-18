/**
 * The icons of keyword tags and notes, by name, as aso/icons.py draws them.
 *
 * base.html publishes every icon's markup once (product-icons-data), so no
 * script carries a copy: a name the catalogue does not know, such as an emoji
 * an older version stored, draws nothing.
 */
(function () {
    'use strict';

    var icons = null;

    window.ProductIcon = function (name) {
        if (!icons) {
            icons = Object.create(null);
            var node = document.getElementById('product-icons-data');
            try {
                if (node) icons = Object.assign(icons, JSON.parse(node.textContent));
            } catch (err) {
                // A page without the catalogue still renders, without icons.
            }
        }
        return (name && icons[name]) || '';
    };

    // The icon inline in a line of text, sitting on the text's baseline, for a
    // mark beside words ("check", "x_circle"); '' for an unknown name.
    window.ProductIcon.inline = function (name) {
        var svg = window.ProductIcon(name);
        return svg ? '<span class="mr-1 inline-block align-[-2px]">' + svg + '</span>' : '';
    };

    // Replaces el's content with the icon and the words, the words as text so
    // a message from a server is never read as markup.
    window.ProductIcon.say = function (el, name, text) {
        el.innerHTML = window.ProductIcon.inline(name);
        el.appendChild(document.createTextNode(text || ''));
    };
})();
