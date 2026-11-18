/* The Store section's buttons and live progress (aso_pro/templates/aso_pro/store/).
 *
 * A button with data-store-post posts JSON to data-url:
 *   data-body         the JSON body (default {})
 *   data-keys         a selector: the values of the checked boxes go in body.keys
 *   data-locales      a selector: the values of the checked boxes go in body.locales
 *   data-text         a selector: that field's value goes in body.text
 *   data-needs-text   the button stays off while that field is blank (Post reply)
 *   data-feedback     a selector: that field's value goes in body.feedback
 *   data-page-form    Search pages' new page form: body.name and body.searches
 *                     (the picked suggestion's searches, or the typed ones)
 *   data-order        a selector: the data-shot-index of its [data-shot] children,
 *                     in the order they stand, go in body.order
 *   data-confirm      a confirm dialog first, with this title, and:
 *     data-confirm-lines  its few short lines, split by "|": what it does,
 *                         how long, what happens if RespectASO stops
 *     data-confirm-ok     the button that goes ahead (default "Continue")
 *     data-confirm-alt    a second way to go ahead, posting data-confirm-alt-body
 *                         (JSON) merged into the body (Draft all: tonight)
 * then reloads the page in place (or opens the answer's url). An error is
 * shown in the closest [data-store-scope]'s [data-store-error], unless the
 * answer asks for a reload (a refused review reply: the page then offers Copy).
 *
 * Search pages: picking a suggested page fills the name with its own (unless
 * the person typed one); typing searches of one's own picks that choice; a
 * [data-shot-move] button ("-1" or "1") moves its screenshot one place in
 * its [data-shots] row.
 *
 * A button with data-copy-text copies that selector's field value
 * (static/js/clipboard.js, loaded first).
 *
 * An element with data-poll-url and data-poll-status is asked every few
 * seconds; its [data-poll-message] and [data-poll-bar] follow the run, and the
 * page reloads once the run's status changes.
 */
(function () {
    var tokenField = document.querySelector('[name=csrfmiddlewaretoken]');
    var csrf = tokenField ? tokenField.value : '';

    function checkedValues(selector) {
        return Array.prototype.map.call(document.querySelectorAll(selector + ':checked'), function (box) {
            return box.value;
        });
    }

    function showError(button, text) {
        var scope = button.closest('[data-store-scope]') || document;
        var box = scope.querySelector('[data-store-error]') || document.getElementById('store-error');
        if (!box) { window.alert(text); return; }
        if (box.hasAttribute('data-notice-live')) { window.Notice.show(box, text, 'error'); return; }
        box.textContent = text;
        box.classList.remove('hidden');
    }

    // A reload keeps the scroll position (the browser's own scroll restoration).
    function reloadInPlace() {
        window.location.reload();
    }

    // After the person acts (Apply, Create, Discard, Draft), the page opens at its top, where
    // the result is said, instead of where the button was: the place a reload restores is the
    // one it had when it left.
    function reloadAtTop() {
        window.scrollTo(0, 0);
        window.location.reload();
    }

    // The confirm dialog, built once: a title, a few short lines and its buttons.
    var confirmBox = null;
    function confirmDialog() {
        if (confirmBox) { return confirmBox; }
        confirmBox = document.createElement('dialog');
        confirmBox.id = 'store-confirm';
        confirmBox.setAttribute('aria-labelledby', 'store-confirm-title');
        confirmBox.className = 'w-full max-w-md rounded-xl border border-white/10 bg-[#1e293b] p-0 text-slate-200 backdrop:bg-black/60';
        confirmBox.innerHTML = '<div class="p-5">' +
            '<h2 class="card-title" id="store-confirm-title"></h2>' +
            '<ul class="mt-3 space-y-2 text-sm text-slate-300" data-confirm-lines></ul>' +
            '<div class="mt-5 flex flex-wrap justify-end gap-2">' +
            '<button type="button" class="btn-quiet" data-confirm-cancel>Cancel</button>' +
            '<button type="button" class="btn-secondary hidden" data-confirm-alt></button>' +
            '<button type="button" class="btn-primary" data-confirm-ok></button></div></div>';
        document.body.appendChild(confirmBox);
        confirmBox.querySelector('[data-confirm-cancel]').addEventListener('click', function () { confirmBox.close(); });
        return confirmBox;
    }

    function askFirst(button, go) {
        var box = confirmDialog();
        box.querySelector('#store-confirm-title').textContent = button.dataset.confirm;
        var list = box.querySelector('[data-confirm-lines]');
        list.innerHTML = '';
        (button.dataset.confirmLines || '').split('|').forEach(function (text) {
            text = text.trim();
            if (!text) { return; }
            var li = document.createElement('li');
            li.className = 'flex items-start gap-2';
            li.innerHTML = '<span class="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-purple-400" aria-hidden="true"></span>';
            var span = document.createElement('span');
            span.textContent = text;
            li.appendChild(span);
            list.appendChild(li);
        });
        var ok = box.querySelector('[data-confirm-ok]');
        var alt = box.querySelector('[data-confirm-alt]');
        ok.textContent = button.dataset.confirmOk || 'Continue';
        alt.textContent = button.dataset.confirmAlt || '';
        alt.classList.toggle('hidden', !button.dataset.confirmAlt);
        ok.onclick = function () { box.close(); go({}); };
        alt.onclick = function () {
            var extra = {};
            try { extra = JSON.parse(button.dataset.confirmAltBody || '{}'); } catch (e) { extra = {}; }
            box.close();
            go(extra);
        };
        box.showModal();
        ok.focus();
    }

    document.addEventListener('click', function (event) {
        var button = event.target.closest('[data-store-post]');
        if (!button) { return; }
        event.preventDefault();
        if (button.dataset.confirm) {
            askFirst(button, function (extra) { post(button, extra); });
            return;
        }
        post(button, {});
    });

    function post(button, extra) {
        var body = {};
        try { body = JSON.parse(button.dataset.body || '{}'); } catch (e) { body = {}; }
        Object.keys(extra || {}).forEach(function (key) { body[key] = extra[key]; });
        if (button.dataset.keys) { body.keys = checkedValues(button.dataset.keys); }
        if (button.dataset.locales) { body.locales = checkedValues(button.dataset.locales); }
        if (button.dataset.text) { body.text = (document.querySelector(button.dataset.text) || {}).value || ''; }
        if (button.dataset.feedback) {
            body.feedback = (document.querySelector(button.dataset.feedback) || {}).value || '';
        }
        if (button.hasAttribute('data-page-form')) { Object.assign(body, pageForm()); }
        if (button.dataset.order) {
            var row = document.querySelector(button.dataset.order);
            body.order = row ? Array.prototype.map.call(row.querySelectorAll('[data-shot]'), function (el) {
                return parseInt(el.dataset.shotIndex, 10);
            }) : [];
        }
        button.disabled = true;
        fetch(button.dataset.url, {
            method: 'POST',
            headers: {'X-CSRFToken': csrf, 'Content-Type': 'application/json'},
            body: JSON.stringify(body)
        }).then(function (response) {
            return response.json().then(function (data) { return {ok: response.ok, data: data}; });
        }).then(function (answer) {
            button.disabled = false;
            updateNeedsText();
            if (!answer.ok && answer.data.reload) { reloadInPlace(); return; }
            if (!answer.ok) { showError(button, answer.data.error || 'Something went wrong. Try again.'); return; }
            if (answer.data.url) { window.location.href = answer.data.url; return; }
            reloadAtTop();
        }).catch(function () {
            button.disabled = false;
            updateNeedsText();
            showError(button, 'RespectASO did not answer. Try again.');
        });
    }

    // A button that sends a field's text stays off while the field is blank.
    function updateNeedsText() {
        document.querySelectorAll('[data-store-post][data-needs-text]').forEach(function (button) {
            var field = document.querySelector(button.dataset.text);
            button.disabled = !(field && field.value.trim());
        });
    }
    document.addEventListener('input', updateNeedsText);
    updateNeedsText();

    document.addEventListener('click', function (event) {
        var button = event.target.closest('[data-copy-text]');
        if (!button) { return; }
        event.preventDefault();
        var field = document.querySelector(button.dataset.copyText);
        copyTextToClipboard(field ? field.value : '').then(function () {
            // The plain label: the toast's green text does not read on a primary button.
            var label = button.dataset.copyLabel || button.textContent;
            button.dataset.copyLabel = label;
            button.textContent = 'Copied';
            clearTimeout(button._copiedTimer);
            button._copiedTimer = setTimeout(function () { button.textContent = label; }, 1500);
        }).catch(function () {
            showCopyToast(button, 'Copy failed', false);
        });
    });

    // Search pages' new page form.
    function pageForm() {
        var name = document.getElementById('store-page-name');
        var picked = document.querySelector("input[name='page-choice']:checked");
        var searches = [];
        if (picked && picked.value === 'idea') {
            searches = (picked.dataset.searches || '').split('|');
        } else {
            searches = ((document.getElementById('store-page-own') || {}).value || '').split(',');
        }
        return {
            name: name ? name.value.trim() : '',
            searches: searches.map(function (s) { return s.trim(); }).filter(Boolean)
        };
    }

    function fillName(text) {
        var name = document.getElementById('store-page-name');
        if (!name || (name.value && name.dataset.typed === '1')) { return; }
        name.value = text.slice(0, 30);
    }

    // Draft this page is the form's one main button: off, with the reason
    // beside it, until it has searches and a name.
    function updateDraft() {
        var button = document.getElementById('store-page-draft-button');
        var hint = document.getElementById('store-page-draft-hint');
        if (!button || !hint) { return; }
        var form = pageForm();
        var count = document.getElementById('store-page-name-count');
        var nameField = document.getElementById('store-page-name');
        if (count && nameField) { count.textContent = nameField.value.length + '/30'; }
        var reason = !form.searches.length ? 'searches' : (!form.name.trim() ? 'name' : 'ready');
        var problem = checkedProblem(form.searches);
        button.disabled = reason !== 'ready' || problem !== '';
        hint.classList.toggle('text-amber-300', problem !== '');
        hint.classList.toggle('text-slate-400', problem === '');
        hint.textContent = problem || hint.dataset['hint' + reason.charAt(0).toUpperCase() + reason.slice(1)] || '';
    }

    // Typed searches are checked against the keyword field as they are typed (the server
    // reads them the way a draft does), so a search that cannot work is said before Draft.
    var checked = {key: '', problem: ''};
    var checkTimer = null;
    function checkedProblem(searches) {
        var own = document.getElementById('store-page-own-choice');
        var picked = document.querySelector("input[name='page-choice']:checked");
        var typing = !picked || picked.value === 'own' || !own;
        // The name rides along, so the session keeps the whole form as typed.
        var key = typing && searches.length ? searches.join(',') + '|' + ((document.getElementById('store-page-name') || {}).value || '') : '';
        if (!key) { return ''; }
        if (key === checked.key) { return checked.problem; }
        clearTimeout(checkTimer);
        checkTimer = setTimeout(function () {
            var button = document.getElementById('store-page-draft-button');
            if (!button || !button.dataset.checkUrl) { return; }
            fetch(button.dataset.checkUrl, {
                method: 'POST', credentials: 'same-origin',
                headers: {'X-CSRFToken': csrf, 'Content-Type': 'application/json'},
                body: JSON.stringify({app: parseInt(button.dataset.app, 10), searches: searches, own: true,
                                      name: (document.getElementById('store-page-name') || {}).value || ''})
            }).then(function (r) { return r.ok ? r.json() : null; }).then(function (data) {
                if (!data) { return; }
                checked = {key: key, problem: data.problem || ''};
                updateDraft();
            }).catch(function () {});
        }, 350);
        return '';
    }



    document.addEventListener('input', function (event) {
        if (event.target.id === 'store-page-name') {
            event.target.dataset.typed = event.target.value ? '1' : '';
            updateDraft();
        }
        if (event.target.id === 'store-page-own') {
            var own = document.getElementById('store-page-own-choice');
            if (own) { own.checked = true; }
            var first = event.target.value.split(',')[0].trim();
            fillName(first ? first.charAt(0).toUpperCase() + first.slice(1) : '');
            updateDraft();
        }
    });

    document.addEventListener('focusin', function (event) {
        if (event.target.id !== 'store-page-own') { return; }
        var own = document.getElementById('store-page-own-choice');
        if (own && !own.checked) {
            own.checked = true;
            var first = event.target.value.split(',')[0].trim();
            fillName(first ? first.charAt(0).toUpperCase() + first.slice(1) : '');
            updateDraft();
        }
    });

    document.addEventListener('change', function (event) {
        var choice = event.target.closest("input[name='page-choice']");
        if (!choice) { return; }
        if (choice.value === 'idea') { fillName(choice.dataset.name || ''); }
        if (choice.value === 'own') {
            var own = document.getElementById('store-page-own');
            if (own) {
                var first = own.value.split(',')[0].trim();
                fillName(first ? first.charAt(0).toUpperCase() + first.slice(1) : '');
                own.focus();
            }
        }
        updateDraft();
    });

    updateDraft();

    // A Store tab's first-meeting explanation: How it works opens it, Hide folds it.
    document.addEventListener('click', function (event) {
        var toggle = event.target.closest('[data-intro-toggle]');
        if (!toggle) { return; }
        var body = document.getElementById(toggle.getAttribute('aria-controls'));
        if (!body) { return; }
        var opening = body.classList.contains('hidden');
        body.classList.toggle('hidden', !opening);
        toggle.setAttribute('aria-expanded', opening ? 'true' : 'false');
        toggle.textContent = opening ? 'Hide' : 'How it works';
    });

    // A search page's screenshots: move one a place left or right.
    document.addEventListener('click', function (event) {
        var button = event.target.closest('[data-shot-move]');
        if (!button) { return; }
        var shot = button.closest('[data-shot]');
        var step = parseInt(button.dataset.shotMove, 10);
        var other = step < 0 ? shot.previousElementSibling : shot.nextElementSibling;
        if (!other) { return; }
        if (step < 0) { shot.parentNode.insertBefore(shot, other); } else { shot.parentNode.insertBefore(other, shot); }
        var row = shot.parentNode;
        var order = [];
        row.querySelectorAll('[data-shot]').forEach(function (el, i) {
            var number = el.querySelector('[data-shot-number]');
            if (number) { number.textContent = String(i + 1); }
            var left = el.querySelector('[data-shot-move="-1"]');
            var right = el.querySelector('[data-shot-move="1"]');
            left.disabled = i === 0;
            right.disabled = !el.nextElementSibling;
            left.setAttribute('aria-label', 'Move screenshot ' + (i + 1) + ' left');
            right.setAttribute('aria-label', 'Move screenshot ' + (i + 1) + ' right');
            order.push(parseInt(el.dataset.shotIndex, 10));
        });
        button.focus();
        // Saved at once, so a reload or Back keeps the person's order.
        if (row.dataset.saveUrl) {
            fetch(row.dataset.saveUrl, {
                method: 'POST', credentials: 'same-origin',
                headers: {'X-CSRFToken': csrf, 'Content-Type': 'application/json'},
                body: JSON.stringify({order: order}),
            }).then(function (r) { return r.ok ? r.json() : null; }).then(function (data) {
                var note = row.parentNode.querySelector('[data-order-note]');
                if (data && data.note && note) { note.textContent = data.note; }
            }).catch(function () {});
        }
    });

    // A checkbox list's "Apply" stays off while nothing is checked.
    document.addEventListener('change', function (event) {
        var box = event.target.closest('[data-keys-for]');
        if (!box) { return; }
        var button = document.getElementById(box.dataset.keysFor);
        if (!button) { return; }
        var none = checkedValues(button.dataset.keys || button.dataset.locales).length === 0;
        button.disabled = none;
        // Off, the button says why beside it.
        var hint = button.dataset.noneHint ? document.querySelector(button.dataset.noneHint) : null;
        if (hint) { hint.textContent = none ? hint.dataset.hintNone : hint.dataset.hintSome; }
    });

    // Dialogs opened from a tile: data-dialog-open="<id>", closed by data-dialog-close or Escape.
    // A link to #store-languages (a finding's "Add German") opens the languages dialog.
    document.addEventListener('click', function (event) {
        var opener = event.target.closest('[data-dialog-open]');
        if (opener) {
            var dialog = document.getElementById(opener.dataset.dialogOpen);
            if (dialog && dialog.showModal) { dialog.showModal(); }
            return;
        }
        var closer = event.target.closest('[data-dialog-close]');
        if (closer) { closer.closest('dialog').close(); }
    });
    if (window.location.hash === '#store-languages') {
        var languages = document.getElementById('store-languages-dialog');
        if (languages && languages.showModal) { languages.showModal(); }
    }

    // The Custom product pages tab asks Apple for its pages as it opens and shows them again
    // only when something changed there (submitted, approved, rejected or deleted).
    document.querySelectorAll('[data-pages-refresh]').forEach(function (box) {
        fetch(box.dataset.pagesRefresh, {
            method: 'POST', credentials: 'same-origin',
            headers: {'X-CSRFToken': csrf, 'Content-Type': 'application/json'},
            body: box.dataset.body || '{}'
        }).then(function (r) { return r.ok ? r.json() : null; }).then(function (data) {
            if (data && data.changed) { reloadInPlace(); }
        }).catch(function () { /* the stored list stays */ });
    });

    // A part that has never been read from Apple (data-store-autoread) is read
    // once the page shows, so opening the tab never waits for Apple.
    document.querySelectorAll('[data-store-autoread]').forEach(function (box) {
        fetch(box.dataset.url, {
            method: 'POST',
            headers: {'X-CSRFToken': csrf, 'Content-Type': 'application/json'},
            body: box.dataset.body || '{}'
        }).then(function (response) {
            return response.json().then(function (data) { return {ok: response.ok, data: data}; });
        }).then(function (answer) {
            if (answer.ok) { reloadInPlace(); return; }
            readFailed(box, answer.data.error || 'Apple did not answer. Try again in a minute.');
        }).catch(function () { readFailed(box, 'RespectASO did not answer. Try again.'); });
    });

    // A read that failed stops spinning, says so in its title, and offers to try again.
    function readFailed(box, message) {
        var spinner = box.querySelector('.animate-spin');
        if (spinner) { spinner.remove(); }
        var title = box.querySelector('.card-title');
        if (title) { title.textContent = 'Could not read your store page'; }
        var lead = box.querySelector('.card-lead');
        if (lead) { lead.remove(); }
        var error = box.querySelector('[data-store-error]');
        if (!error) { showError(box, message); return; }
        error.textContent = message;
        error.classList.remove('hidden');
        if (!box.querySelector('[data-store-retry]')) {
            var retry = document.createElement('button');
            retry.type = 'button';
            retry.className = 'btn-secondary ml-auto shrink-0 py-1.5 text-xs';
            retry.dataset.storeRetry = '';
            retry.textContent = 'Try again';
            retry.addEventListener('click', reloadInPlace);
            error.insertAdjacentElement('afterend', retry);
        }
    }

    // The app switcher beside the title opens this tab for the chosen app.
    // Until the page arrives, the button spins and the page under the
    // header fades, so a switch answers at once (owner, 2026-11-14: a
    // switch that showed nothing read as broken). The Store templates load
    // this file in the scripts block, after country-picker.js: loaded
    // before it, the switcher was never wired and choosing an app did nothing.
    if (window.CountryPicker && CountryPicker.get('store-app-switch')) {
        var shown = (CountryPicker.get('store-app-switch').getSelected() || [])[0];
        CountryPicker.onChange('store-app-switch', function (values) {
            if (!values[0]) { return; }
            var picker = document.getElementById('store-app-switch');
            if (values[0] === '#add-app') {
                // "Add an app…": the Add an app dialog (static/js/add-app.js), and the picker
                // goes back to the app on screen.
                var instance = CountryPicker.get('store-app-switch');
                instance.selected = shown ? [shown] : [];
                instance.updateButton();
                if (window.AddApp) { window.AddApp.open(picker.querySelector('button') || picker); }
                return;
            }
            var page = picker.closest('[data-store-tab]');
            if (page) {
                page.setAttribute('aria-busy', 'true');
                Array.prototype.forEach.call(page.children, function (part) {
                    if (part.tagName === 'NAV' || part.contains(picker)) { return; }
                    part.classList.add('opacity-50', 'pointer-events-none', 'transition-opacity', 'store-opening');
                });
            }
            var label = picker.querySelector('.cp-button-text');
            if (label && !picker.querySelector('.store-spinner')) {
                label.insertAdjacentHTML('beforebegin', '<span class="store-spinner animate-spin h-3.5 w-3.5 border-2 border-purple-400 border-t-transparent rounded-full shrink-0" aria-hidden="true"></span>');
            }
            window.location.href = values[0];
        });
        // Back to this page from the browser's cache: undo the fade.
        window.addEventListener('pageshow', function (event) {
            if (!event.persisted) { return; }
            document.querySelectorAll('.store-opening').forEach(function (part) {
                part.classList.remove('opacity-50', 'pointer-events-none', 'store-opening');
            });
            document.querySelectorAll('.store-spinner').forEach(function (spin) { spin.remove(); });
            var busy = document.querySelector('[data-store-tab][aria-busy]');
            if (busy) { busy.removeAttribute('aria-busy'); }
        });
    }

    var watched = document.querySelectorAll('[data-poll-url]');
    if (!watched.length) { return; }
    function poll() {
        Array.prototype.forEach.call(watched, function (el) {
            fetch(el.dataset.pollUrl, {headers: {'Accept': 'application/json'}})
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    if (data.status !== el.dataset.pollStatus) { reloadInPlace(); return; }
                    // A step that finished shows its result at once.
                    var changed = (data.steps || []).some(function (step) {
                        var box = el.querySelector('[data-step="' + step.key + '"]');
                        return box && box.dataset.stepState !== step.state;
                    });
                    if (changed) { reloadInPlace(); return; }
                    var message = el.querySelector('[data-poll-message]');
                    if (message && data.progress_message) { message.textContent = data.progress_message; }
                    var bar = el.querySelector('[data-poll-bar]');
                    if (bar) { bar.style.width = (data.progress_percent || 0) + '%'; }
                })
                .catch(function () { /* the next tick tries again */ });
        });
    }
    setInterval(poll, 3000);
    document.addEventListener('visibilitychange', function () { if (!document.hidden) { poll(); } });

})();
