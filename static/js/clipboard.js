/* Copy-to-clipboard — the single source of truth for every Copy button.
 *
 * The shipped macOS app is a pywebview window serving http://localhost, where
 * WebKit blocks BOTH navigator.clipboard and document.execCommand('copy'). A
 * bare navigator.clipboard call therefore fails silently for real users while
 * working perfectly in a browser during development, so every Copy button must
 * go through copyTextToClipboard() and its three tiers.
 *
 * Load with <script src="{% static 'js/clipboard.js' %}"></script> before the
 * template's own script block.
 *
 * Exposes: copyTextToClipboard(text) -> Promise, showCopyToast(btn, msg, ok)
 */

function copyTextToClipboard(text) {
    // 1. Native bridge — always works in pywebview, since WebKit on http://localhost
    //    blocks both navigator.clipboard and document.execCommand('copy').
    if (window.pywebview && window.pywebview.api && window.pywebview.api.copy_to_clipboard) {
        return window.pywebview.api.copy_to_clipboard(text).then(function(ok) {
            if (!ok) throw new Error('Native copy returned false');
        });
    }
    // 2. Modern Clipboard API (works in regular browsers on https or localhost).
    if (navigator.clipboard && navigator.clipboard.writeText) {
        return navigator.clipboard.writeText(text).catch(function() {
            return legacyCopy(text);
        });
    }
    // 3. Legacy fallback for older browsers.
    return legacyCopy(text);
}

function legacyCopy(text) {
    return new Promise(function(resolve, reject) {
        var ta = document.createElement('textarea');
        ta.value = text;
        ta.setAttribute('readonly', '');
        // Must be on-screen for WebKit to honor the copy command.
        ta.style.position = 'fixed';
        ta.style.top = '0';
        ta.style.left = '0';
        ta.style.width = '1px';
        ta.style.height = '1px';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.focus();
        ta.select();
        try {
            var ok = document.execCommand('copy');
            document.body.removeChild(ta);
            ok ? resolve() : reject(new Error('execCommand returned false'));
        } catch (e) {
            document.body.removeChild(ta);
            reject(e);
        }
    });
}

function showCopyToast(btn, msg, ok) {
    if (!btn) return;
    if (btn.dataset.copyOriginal === undefined) btn.dataset.copyOriginal = btn.textContent;
    var orig = btn.dataset.copyOriginal;
    btn.textContent = msg;
    btn.classList.add(ok ? 'text-emerald-300' : 'text-red-300');
    clearTimeout(btn._copyToastTimer);
    btn._copyToastTimer = setTimeout(function() {
        btn.textContent = orig;
        btn.classList.remove('text-emerald-300', 'text-red-300');
    }, 1500);
}
