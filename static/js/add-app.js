/**
 * The one Add an app dialog (aso/partials/add_app_dialog.html, aso/add_app.py),
 * drawn by base.html on every page.
 *
 * Opened by a click on any [data-add-app] (data-add-app="all" ticks every
 * app of the App Store Connect account), by ?add=1 or #add in the address on
 * arrival, or by AddApp.open(opener). Escape and the X close it, and focus
 * goes back to what opened it.
 *
 * Three ways in, each posting once and saying what happened in the dialog's
 * notice: the account's apps (ticked, then one "Add N apps" button), any app
 * in the App Store by name or link (aso:app_lookup, each result with its own
 * Add), and one by hand behind a disclosure. A button and its boxes turn off
 * at once and the button spins until the answer, so nothing is sent twice;
 * the server also adds an app already tracked as a no-op.
 *
 * Every add fires "app:added" on the document with {handled: false}. A page
 * that shows the new app in place (the Apps page, apps-home.js) sets handled;
 * any other page reloads once when the dialog closes, and opens again where
 * the reader was (a page that focuses a field on load would jump to the top).
 */
(function () {
    'use strict';

    var dialog = document.getElementById('add-app-dialog');
    if (!dialog) { return; }

    var csrf = dialog.dataset.csrf || '';
    var notice = document.getElementById('add-app-notice');
    var opener = null;
    var pending = 0;
    var reloadOnClose = false;
    var searchTimer = null;
    var searchSeq = 0;
    var scrollAtOpen = 0;

    function el(id) { return document.getElementById(id); }

    // The reload after an add keeps the reader's place in the history entry,
    // which a reload keeps (the app uses no browser storage: desktop-compat).
    function reload() {
        // Where the reader was when the dialog opened: handing focus back may have scrolled since.
        var state = Object.assign({}, history.state || {}, {addAppScrollY: scrollAtOpen});
        history.replaceState(state, '');
        window.location.reload();
    }

    // Back where the reader was after the reload above, after the browser's own
    // autofocus, which runs after load and scrolls to its field.
    if (history.state && typeof history.state.addAppScrollY === 'number') {
        var keptY = history.state.addAppScrollY;
        var rest = Object.assign({}, history.state);
        delete rest.addAppScrollY;
        history.replaceState(rest, '');
        window.addEventListener('load', function () {
            requestAnimationFrame(function () { setTimeout(function () { window.scrollTo(0, keptY); }, 0); });
        });
    }

    function say(text, tone) {
        if (window.Notice) { window.Notice.show(notice, text, tone); }
    }

    function busy(button, on, label) {
        var spinner = button.querySelector('[data-spinner]');
        var words = button.querySelector('[data-label]');
        button.disabled = on;
        button.setAttribute('aria-busy', on ? 'true' : 'false');
        if (spinner) { spinner.classList.toggle('hidden', !on); }
        if (words && label) { words.textContent = label; }
    }

    function post(url, body) {
        pending += 1;
        return fetch(url, {
            method: 'POST', credentials: 'same-origin',
            headers: {'X-CSRFToken': csrf, 'Content-Type': 'application/json'},
            body: JSON.stringify(body)
        }).then(function (response) {
            return response.json().catch(function () { return {}; }).then(function (data) {
                if (!response.ok) { throw new Error(data.error || 'Something went wrong. Try again.'); }
                return data;
            });
        }, function () {
            throw new Error('RespectASO did not answer. Try again.');
        }).finally(function () {
            pending -= 1;
            if (pending === 0 && reloadOnClose && !dialog.open) { reload(); }
        });
    }

    // What an answer said, in the notice, and the page told about the new apps.
    function done(data) {
        var added = data.added || [];
        say(data.message || '', added.length ? 'success' : 'info');
        if (!added.length) { return; }
        var detail = {handled: false, names: added};
        document.dispatchEvent(new CustomEvent('app:added', {detail: detail}));
        if (!detail.handled) { reloadOnClose = true; }
    }

    // ------------------------------------------------ the account's apps

    function accountBoxes() {
        return Array.prototype.slice.call(dialog.querySelectorAll('input[name="add-app-account"]'));
    }

    function updateAccount() {
        var button = el('add-app-account-button');
        if (!button || button.getAttribute('aria-busy') === 'true') { return; }
        var boxes = accountBoxes();
        var picked = boxes.filter(function (box) { return box.checked; }).length;
        busy(button, false, picked === 0 ? 'Add apps' : (picked === 1 ? 'Add 1 app' : 'Add ' + picked + ' apps'));
        button.disabled = picked === 0;
        var hint = el('add-app-account-hint');
        if (hint) { hint.classList.toggle('hidden', picked > 0); }
        var all = el('add-app-account-all');
        if (all) {
            all.checked = picked > 0 && picked === boxes.length;
            all.indeterminate = picked > 0 && picked < boxes.length;
        }
    }

    function lockAccount(on) {
        accountBoxes().forEach(function (box) { box.disabled = on; });
        var all = el('add-app-account-all');
        if (all) { all.disabled = on; }
    }

    // With every account app added, the dialog is search and manual only.
    function dropAccountIfEmpty() {
        var section = el('add-app-account');
        if (!section || accountBoxes().length) { return; }
        section.remove();
        var search = el('add-app-search');
        search.classList.remove('mt-5', 'border-t', 'border-white/5', 'pt-4');
        var heading = search.querySelector('h3');
        if (heading) { heading.remove(); }
        var label = search.querySelector('label');
        if (label) { label.classList.remove('mt-2'); }
    }

    function addAccountApps() {
        var section = el('add-app-account');
        var button = el('add-app-account-button');
        var picked = accountBoxes().filter(function (box) { return box.checked; });
        if (!section || !picked.length || button.disabled) { return; }
        lockAccount(true);
        busy(button, true, picked.length === 1 ? 'Adding 1 app…' : 'Adding ' + picked.length + ' apps…');
        post(section.dataset.url, {ids: picked.map(function (box) { return box.value; })}).then(function (data) {
            picked.forEach(function (box) { box.closest('li').remove(); });
            done(data);
        }).catch(function (error) {
            say(error.message, 'error');
        }).finally(function () {
            lockAccount(false);
            busy(button, false);
            updateAccount();
            dropAccountIfEmpty();
        });
    }

    // ------------------------------------------------ any app in the App Store

    var ADDED_ICON = '<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7"/></svg>';

    function addedMark() {
        var mark = document.createElement('span');
        mark.className = 'inline-flex shrink-0 items-center gap-1 text-xs text-emerald-300';
        mark.innerHTML = ADDED_ICON;
        mark.appendChild(document.createTextNode('Added'));
        return mark;
    }

    function resultRow(app) {
        var li = document.createElement('li');
        li.className = 'flex items-center gap-3 px-3 py-2';
        if (app.artworkUrl100) {
            var icon = document.createElement('img');
            icon.src = app.artworkUrl100;
            icon.alt = '';
            icon.className = 'h-9 w-9 shrink-0 rounded-lg';
            li.appendChild(icon);
        }
        var text = document.createElement('div');
        text.className = 'min-w-0 flex-1';
        var name = document.createElement('p');
        name.className = 'truncate text-sm text-slate-100';
        name.textContent = app.trackName;
        var seller = document.createElement('p');
        seller.className = 'truncate text-xs text-slate-400';
        seller.textContent = app.sellerName || app.bundleId || '';
        text.appendChild(name);
        text.appendChild(seller);
        li.appendChild(text);
        if (app.tracked) {
            li.appendChild(addedMark());
            return li;
        }
        var button = document.createElement('button');
        button.type = 'button';
        button.className = 'btn-secondary inline-flex shrink-0 items-center gap-1.5 py-1.5 text-xs';
        button.setAttribute('aria-label', 'Add ' + app.trackName);
        button.innerHTML = '<span class="hidden h-3 w-3 shrink-0 animate-spin rounded-full border-2 border-purple-400 border-t-transparent" data-spinner aria-hidden="true"></span><span data-label>Add</span>';
        button.addEventListener('click', function () {
            if (button.disabled) { return; }
            busy(button, true, 'Adding…');
            post(dialog.dataset.addUrl, {store: app}).then(function (data) {
                button.replaceWith(addedMark());
                done(data);
            }).catch(function (error) {
                busy(button, false, 'Add');
                say(error.message, 'error');
            });
        });
        li.appendChild(button);
        return li;
    }

    function showResults(apps, note, isError) {
        var list = el('app-search-results');
        var line = el('app-search-note');
        list.replaceChildren.apply(list, (apps || []).map(resultRow));
        list.classList.toggle('hidden', !(apps && apps.length));
        line.textContent = note || '';
        line.classList.toggle('hidden', !note);
        line.classList.toggle('text-red-300', !!isError);
        line.classList.toggle('text-slate-400', !isError);
    }

    function search(now) {
        var query = el('app-search-input').value.trim();
        var spinner = el('app-search-spinner');
        clearTimeout(searchTimer);
        if (query.length < 2) { spinner.classList.add('hidden'); showResults([], ''); return; }
        searchTimer = setTimeout(function () {
            var seq = ++searchSeq;
            spinner.classList.remove('hidden');
            fetch(dialog.dataset.lookupUrl + '?q=' + encodeURIComponent(query), {credentials: 'same-origin'})
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    if (seq !== searchSeq) { return; }
                    spinner.classList.add('hidden');
                    // Apple not answering is said as such, never as "no apps".
                    if (!data.apps || !data.apps.length) {
                        showResults([], data.error || 'No apps found. Try another name or paste the App Store link.', !!data.error);
                        return;
                    }
                    showResults(data.apps, '');
                })
                .catch(function () {
                    if (seq !== searchSeq) { return; }
                    spinner.classList.add('hidden');
                    showResults([], 'The search did not finish. Try again.', true);
                });
        }, now === true ? 0 : 400);
    }

    // ------------------------------------------------ by hand

    function addManual(event) {
        event.preventDefault();
        var button = el('add-app-manual-button');
        var name = el('id_add-app-name');
        var bundle = el('id_add-app-bundle_id');
        if (button.disabled) { return; }
        if (!name.value.trim()) { say("Type the app's name to add it.", 'error'); name.focus(); return; }
        busy(button, true, 'Adding…');
        post(dialog.dataset.addUrl, {manual: {name: name.value.trim(), bundle_id: bundle.value.trim()}}).then(function (data) {
            name.value = '';
            bundle.value = '';
            done(data);
        }).catch(function (error) {
            say(error.message, 'error');
        }).finally(function () {
            busy(button, false, 'Add');
        });
    }

    // ------------------------------------------------ open and close

    function open(from, options) {
        opener = from || document.activeElement;
        if (!dialog.open) { scrollAtOpen = window.scrollY; }
        if (window.Notice) { window.Notice.hide(notice); }
        el('app-search-input').value = '';
        showResults([], '');
        el('add-app-manual').open = false;
        var all = !!(options && options.all);
        accountBoxes().forEach(function (box) { box.checked = all; });
        updateAccount();
        if (!dialog.open) { dialog.showModal(); }
        var first = accountBoxes()[0];
        var target = all && first ? el('add-app-account-button') : (first || el('app-search-input'));
        target.focus();
    }

    function close() { dialog.close(); }

    dialog.addEventListener('close', function () {
        if (opener && document.contains(opener) && opener.focus) { opener.focus({preventScroll: true}); }
        opener = null;
        if (reloadOnClose && pending === 0) { reload(); }
    });

    document.addEventListener('click', function (event) {
        var trigger = event.target.closest('[data-add-app]');
        if (trigger) {
            event.preventDefault();
            open(trigger, {all: trigger.dataset.addApp === 'all'});
            return;
        }
        if (event.target.closest('[data-add-app-close]')) { close(); }
    });

    dialog.addEventListener('change', function (event) {
        if (event.target.id === 'add-app-account-all') {
            accountBoxes().forEach(function (box) { box.checked = event.target.checked; });
        }
        if (event.target.name === 'add-app-account' || event.target.id === 'add-app-account-all') { updateAccount(); }
    });
    var accountButton = el('add-app-account-button');
    if (accountButton) { accountButton.addEventListener('click', addAccountApps); }
    el('app-search-input').addEventListener('input', search);
    // Enter in the search field searches at once.
    el('app-search-input').addEventListener('keydown', function (event) {
        if (event.key === 'Enter') { event.preventDefault(); search(true); }
    });
    el('add-app-manual-form').addEventListener('submit', addManual);

    // ?add=1 or #add (a link from before, a bookmark) opens the dialog on arrival, once.
    var params = new URLSearchParams(window.location.search);
    if (params.get('add') === '1' || window.location.hash === '#add') {
        params.delete('add');
        var query = params.toString();
        history.replaceState(history.state, '', window.location.pathname + (query ? '?' + query : ''));
        open(null);
    }

    window.AddApp = {open: function (from, options) { open(from, options); }};
})();
