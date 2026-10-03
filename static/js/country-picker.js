/*
 * The country picker, shared by every page that chooses storefronts.
 *
 * One instance per .cp-root on the page, all reading one catalog that
 * base.html emits as <script id="country-catalog-data" type="application/json">.
 * Nothing here knows a country name: that is the whole point. Three hardcoded
 * lists inside dashboard.html and opportunity.html used to disagree with each
 * other and with the server.
 *
 * Public surface:
 *   CountryPicker.get(id)        the instance, with .getSelected() / .set()
 *   CountryPicker.name(code)     "bg" -> "Bulgaria"
 *   CountryPicker.storeName(code) "nl" -> "the App Store in the Netherlands"
 *   CountryPicker.flag(code)     "bg" -> the flag
 *   CountryPicker.label(code)    flag + name
 *   CountryPicker.region(code)
 *   CountryPicker.onChange(id, fn)
 *
 * Deliberately not virtualized. 175 rows is an order of magnitude below where
 * that pays for itself, and the list is built once, lazily, on first open.
 *
 * With data-items (the id of a json_script of {value, label, icon, hint,
 * selected}) the same picker chooses ONE item instead, such as the Rival
 * Tracker setup's App: same button, panel and search, rows with an icon,
 * and picking one closes the panel. The country parts are left out.
 *
 * With data-select (a <select> id) the items are that select's options and
 * every choice is written back to it with a change event: the form and the
 * page's scripts keep using the select, hidden. A script that refills it is
 * followed by itself; one that sets its value calls CountryPicker.syncSelect.
 */
(function () {
    'use strict';

    var catalog = null;
    var byCode = {};
    var instances = {};

    function loadCatalog() {
        if (catalog) { return catalog; }
        var el = document.getElementById('country-catalog-data');
        catalog = el ? JSON.parse(el.textContent) : { countries: [], regions: [], presets: [], default: ['us'] };
        catalog.countries.forEach(function (c) { byCode[c.code] = c; });
        return catalog;
    }

    function esc(s) {
        return String(s === null || s === undefined ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    function fold(s) {
        // Match the server's _fold(): lowercase, diacritics stripped, so
        // "turkiye" finds "Türkiye" and "cote" finds "Côte d'Ivoire".
        return String(s || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
    }

    // A wait in words: "20 seconds", "about 3 minutes", "about 1 hour 7
    // minutes". The one script copy (job-polling.js and keyword-search-job.js
    // call it); its twin is duration_text() in aso/opportunity_scans.py, and
    // aso/tests/test_duration_text.py runs both.
    function durationText(seconds) {
        if (!seconds || seconds <= 0) { return ''; }
        var tens = Math.max(10, Math.floor(seconds / 10 + 0.5) * 10);
        if (tens < 60) { return tens + ' seconds'; }
        var minutes = Math.ceil(seconds / 60);
        if (minutes < 60) { return 'about ' + minutes + ' minute' + (minutes === 1 ? '' : 's'); }
        var hours = Math.floor(minutes / 60);
        minutes = minutes % 60;
        return 'about ' + hours + ' hour' + (hours === 1 ? '' : 's') +
            (minutes ? ' ' + minutes + ' minute' + (minutes === 1 ? '' : 's') : '');
    }

    // ---- one picker ----------------------------------------------------

    // A select's options as items (data-select).
    function itemsOf(select) {
        return Array.prototype.map.call(select.options, function (o) {
            return {
                value: o.value, label: o.text.trim(), icon: o.dataset.icon || '', hint: o.dataset.hint || '',
                glyph: o.dataset.glyph || '', placeholder: 'placeholder' in o.dataset, selected: o.selected
            };
        });
    }

    function Picker(root) {
        this.root = root;
        this.id = root.id;
        this.items = null;
        this.select = root.dataset.select ? document.getElementById(root.dataset.select) : null;
        if (this.select) {
            this.items = itemsOf(this.select);
        } else if (root.dataset.items) {
            var source = document.getElementById(root.dataset.items);
            this.items = source ? JSON.parse(source.textContent) : [];
        }
        this.max = this.items ? 1 : (parseInt(root.dataset.max, 10) || 0);
        // The picker's last selection is the app's to remember for this
        // visitor (aso/ui_memory.py): the server writes it into
        // data-remembered, and every change goes back through UiMemory.
        this.memoryKey = root.dataset.memoryKey || '';
        this.costPer = parseFloat(root.dataset.costPerCountry) || 0;
        this.defaultPreset = root.dataset.defaultPreset || '';
        this.button = root.querySelector('.cp-button');
        this.buttonText = root.querySelector('.cp-button-text');
        this.panel = root.querySelector('.cp-panel');
        this.search = root.querySelector('.cp-search');
        this.presetsBox = root.querySelector('.cp-presets');
        this.list = root.querySelector('.cp-list');
        this.empty = root.querySelector('.cp-empty');
        this.countEl = root.querySelector('.cp-count');
        this.costEl = root.querySelector('.cp-cost');
        this.value = root.querySelector('.cp-value');
        this.rows = {};
        this.built = false;
        this.selectAllEl = root.querySelector('.cp-select-all');
        if (this.selectAllEl && this.max) { this.selectAllEl.classList.add('hidden'); }
        this.listeners = [];
        this.selected = this.restore();
        this.syncValue();
        this.updateButton();
        this.bind();
        if (this.select) { this.watchSelect(); }
    }

    // The select stays the source of truth: refilled or changed by the
    // page's scripts, the picker follows.
    Picker.prototype.watchSelect = function () {
        var self = this;
        this.select.addEventListener('change', function () { self.syncSelect(); });
        // A form reset puts the select back without a change event.
        if (this.select.form) {
            this.select.form.addEventListener('reset', function () { setTimeout(function () { self.syncSelect(); }, 0); });
        }
        new MutationObserver(function () {
            self.items = itemsOf(self.select);
            self.built = false;
            if (!self.panel.classList.contains('hidden')) { self.build(); }
            self.syncSelect();
        }).observe(this.select, {childList: true, subtree: true, attributes: true});
    };

    Picker.prototype.syncSelect = function () {
        this.selected = this.clean([this.select.value]);
        this.updateButton();
        if (this.built) { this.paintChecks(); }
    };

    Picker.prototype.restore = function () {
        if (this.items) {
            return this.items.filter(function (item) { return item.selected; })
                .map(function (item) { return String(item.value); }).slice(0, 1);
        }
        var stored = null;
        try {
            stored = this.memoryKey ? JSON.parse(this.root.dataset.remembered || '[]') : null;
        } catch (e) { stored = null; }
        var codes = this.clean(stored || []);
        if (!codes.length && this.defaultPreset) {
            var preset = catalog.presets.filter(function (p) { return p.id === this.defaultPreset; }.bind(this))[0];
            if (preset) { codes = this.clean(preset.codes); }
        }
        if (!codes.length) { codes = this.clean(catalog.default); }
        return codes;
    };

    Picker.prototype.item = function (value) {
        return (this.items || []).filter(function (i) { return String(i.value) === String(value); })[0] || null;
    };

    Picker.prototype.clean = function (codes) {
        var seen = {}, out = [];
        if (this.items) {
            (codes || []).forEach(function (raw) {
                if (this.item(raw) && !seen[raw]) { seen[raw] = 1; out.push(String(raw)); }
            }, this);
            return out.slice(0, 1);
        }
        (codes || []).forEach(function (raw) {
            var code = String(raw || '').toLowerCase();
            if (byCode[code] && !seen[code]) { seen[code] = 1; out.push(code); }
        });
        return this.max ? out.slice(0, this.max) : out;
    };

    Picker.prototype.persist = function () {
        if (!this.memoryKey || !window.UiMemory) { return; }
        UiMemory.save('countries.' + this.memoryKey, this.selected);
    };

    Picker.prototype.syncValue = function () {
        if (this.value) { this.value.value = this.selected.join(','); }
    };

    Picker.prototype.build = function () {
        if (this.built) { return; }
        var html = '';
        var self = this;
        if (this.items) {
            this.rows = {};
            this.list.innerHTML = this.items.filter(function (item) { return !item.placeholder; })
                .map(function (item) { return self.itemHtml(item); }).join('');
            Array.prototype.forEach.call(this.list.querySelectorAll('.cp-row'), function (row) {
                self.rows[row.dataset.code] = row;
            });
            this.built = true;
            this.paintChecks();
            return;
        }
        // "Select all" is hidden on a capped picker: every region holds more
        // than the cap, so the click could only silently keep the first few.
        var showRegionAll = !this.max;
        catalog.regions.forEach(function (region) {
            var rows = catalog.countries.filter(function (c) { return c.region === region; });
            if (!rows.length) { return; }
            html += '<div class="cp-group" data-region="' + esc(region) + '">' +
                '<p class="cp-region sticky top-0 z-10 bg-slate-800 px-3 py-1 text-2xs uppercase tracking-wider text-slate-400 flex items-center justify-between">' +
                '<span>' + esc(region) + '</span>' +
                (showRegionAll
                    ? '<button type="button" class="link cp-region-all normal-case tracking-normal">Select all</button>'
                    : '') +
                '</p>';
            rows.forEach(function (c) { html += self.rowHtml(c); });
            html += '</div>';
        });
        this.list.innerHTML = html;
        var self2 = this;
        Array.prototype.forEach.call(this.list.querySelectorAll('.cp-row'), function (row) {
            self2.rows[row.dataset.code] = row;
        });
        this.built = true;
        this.paintChecks();
    };

    Picker.prototype.rowHtml = function (c) {
        var marks = '';
        if (!c.apple_ads) {
            marks += '<span class="cp-mark ml-1.5 text-2xs px-1 py-px rounded bg-slate-700/40 text-slate-300 border border-white/10" ' +
                'title="Apple Ads does not operate in this storefront, so popularity here is RespectASO\'s estimate.">No Apple data</span>';
        }
        if (!c.has_language) {
            marks += '<span class="cp-mark ml-1.5 text-2xs px-1 py-px rounded bg-slate-700/40 text-slate-300 border border-white/10" ' +
                'title="Apple offers no App Store listing language for this storefront. A listing here appears in your app\'s fallback language.">No App Store language</span>';
        }
        return '<label class="cp-row flex items-center gap-2 px-3 py-1.5 hover:bg-white/5 cursor-pointer text-sm" ' +
            'data-code="' + esc(c.code) + '" data-search="' + esc(c.search) + '">' +
            '<input type="checkbox" class="cp-cb rounded border-white/20 bg-slate-900 text-purple-500 focus:ring-purple-500" value="' + esc(c.code) + '">' +
            '<span class="shrink-0">' + c.flag + '</span>' +
            '<span class="text-slate-200 truncate">' + esc(c.name) + '</span>' +
            marks + '</label>';
    };

    // The two rows of an app switcher that are not an app: every app at once,
    // and the way to the Apps page.
    var GLYPHS = {
        all: 'M4 5h6v6H4zM14 5h6v6h-6zM4 15h6v6H4zM14 15h6v6h-6z',
        manage: 'M12 5v14M5 12h14',
        none: 'M6 18L18 6'
    };

    Picker.prototype.itemIcon = function (item, size) {
        if (item.glyph && GLYPHS[item.glyph]) {
            return '<span class="' + size + ' rounded-md bg-slate-700 shrink-0 flex items-center justify-center text-slate-300">' +
                '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">' +
                '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="' + GLYPHS[item.glyph] + '"/></svg></span>';
        }
        return item.icon
            ? '<img src="' + esc(item.icon) + '" alt="" class="' + size + ' rounded-md shrink-0">'
            : '<span class="' + size + ' rounded-md bg-slate-600 shrink-0"></span>';
    };

    Picker.prototype.itemHtml = function (item) {
        return '<button type="button" role="option" class="cp-row cp-item w-full flex items-center gap-2.5 px-3 py-2 text-left text-sm hover:bg-white/5 focus:bg-white/5 focus:outline-none" ' +
            'data-code="' + esc(item.value) + '" data-search="' + esc(fold(item.label)) + '">' +
            this.itemIcon(item, 'w-6 h-6') +
            '<span class="min-w-0 flex-1 truncate text-slate-200">' + esc(item.label) + '</span>' +
            (item.hint ? '<span class="shrink-0 text-2xs text-slate-400">' + esc(item.hint) + '</span>' : '') +
            '<svg class="cp-tick w-4 h-4 shrink-0 text-purple-300" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">' +
            '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"></path></svg>' +
            '</button>';
    };

    Picker.prototype.paintChecks = function () {
        var chosen = {};
        this.selected.forEach(function (c) { chosen[c] = 1; });
        if (this.items) {
            Object.keys(this.rows).forEach(function (code) {
                var row = this.rows[code];
                row.setAttribute('aria-selected', chosen[code] ? 'true' : 'false');
                row.querySelector('.cp-tick').classList.toggle('invisible', !chosen[code]);
            }, this);
            return;
        }
        var atMax = this.max && this.selected.length >= this.max;
        Object.keys(this.rows).forEach(function (code) {
            var cb = this.rows[code].querySelector('.cp-cb');
            cb.checked = !!chosen[code];
            // Disable rather than silently unchecking the click, which is how
            // the old dashboard picker behaved and read as a broken checkbox.
            cb.disabled = !!(atMax && !cb.checked);
            this.rows[code].classList.toggle('opacity-40', cb.disabled);
        }, this);
        this.paintPresets();
    };

    Picker.prototype.paintPresets = function () {
        if (!this.presetsBox) { return; }
        if (!this.presetsBox.dataset.built) {
            var html = '';
            catalog.presets.forEach(function (p) {
                if (this.max && p.codes.length > this.max) { return; }
                html += '<button type="button" class="cp-preset text-2xs px-2 py-1 rounded-full ' +
                    'border border-white/10 text-slate-300 hover:border-purple-500/40 ' +
                    'hover:text-purple-200" data-preset="' + esc(p.id) + '">' +
                    esc(p.label) + '</button>';
            }, this);
            if (!html) { this.presetsBox.classList.add('hidden'); }
            this.presetsBox.innerHTML = html;
            this.presetsBox.dataset.built = '1';
        }
        var n = this.selected.length;
        this.countEl.textContent = n + ' selected' + (this.max ? ' of ' + this.max : '');
        if (this.max && n >= this.max) {
            this.countEl.textContent = n + ' of ' + this.max + ' selected, clear one to add another';
        }
        if (this.costEl) {
            var text = this.costPer ? durationText(n * this.costPer) : '';
            this.costEl.textContent = text ? n + ' ' + (n === 1 ? 'country' : 'countries') + ', ' + text : '';
        }
    };

    Picker.prototype.updateButton = function () {
        var codes = this.selected;
        if (this.items) {
            var item = this.item(codes[0]);
            if (item && item.placeholder) {
                this.buttonText.innerHTML = '<span class="text-slate-400">' + esc(item.label) + '</span>';
                return;
            }
            this.buttonText.innerHTML = item
                ? '<span class="flex items-center gap-2.5 min-w-0">' + this.itemIcon(item, 'w-5 h-5') +
                  '<span class="truncate">' + esc(item.label) + '</span></span>'
                : 'Nothing chosen';
            return;
        }
        if (!codes.length) { this.buttonText.textContent = 'No countries'; return; }
        if (this.root.dataset.compact && codes.length > 1) {
            // A compact picker (the Keywords search line): the first country and how many more.
            this.buttonText.textContent = CountryPicker.label(codes[0]) + ' +' + (codes.length - 1);
            return;
        }
        if (codes.length === 1) {
            this.buttonText.textContent = CountryPicker.label(codes[0]);
        } else if (codes.length <= 3) {
            this.buttonText.textContent = codes.map(CountryPicker.label).join(', ');
        } else {
            this.buttonText.textContent = codes.slice(0, 3).map(CountryPicker.flag).join(' ') +
                ' +' + (codes.length - 3) + ' more';
        }
    };

    Picker.prototype.set = function (codes) {
        this.selected = this.clean(codes);
        if (this.select && this.select.value !== (this.selected[0] || '')) {
            this.select.value = this.selected[0] || '';
            this.select.dispatchEvent(new Event('change', {bubbles: true}));
        }
        this.persist();
        this.syncValue();
        this.updateButton();
        if (this.built) { this.paintChecks(); } else { this.paintPresets(); }
        this.listeners.forEach(function (fn) { fn(this.selected); }, this);
    };

    Picker.prototype.getSelected = function () { return this.selected.slice(); };

    Picker.prototype.filter = function (term) {
        var needle = fold(term);
        var anyVisible = false;
        Object.keys(this.rows).forEach(function (code) {
            var row = this.rows[code];
            var hit = !needle || row.dataset.search.indexOf(needle) !== -1;
            row.classList.toggle('hidden', !hit);
            if (hit) { anyVisible = true; }
        }, this);
        if (this.items) {
            this.empty.classList.toggle('hidden', anyVisible);
            return;
        }
        Array.prototype.forEach.call(this.list.querySelectorAll('.cp-group'), function (group) {
            var visible = group.querySelectorAll('.cp-row:not(.hidden)').length;
            group.classList.toggle('hidden', visible === 0);
        });
        this.empty.classList.toggle('hidden', anyVisible);
    };

    Picker.prototype.open = function () {
        this.build();
        this.panel.classList.remove('hidden');
        this.button.setAttribute('aria-expanded', 'true');
        this.search.value = '';
        this.filter('');
        this.search.focus();
    };

    Picker.prototype.close = function () {
        this.panel.classList.add('hidden');
        this.button.setAttribute('aria-expanded', 'false');
    };

    Picker.prototype.bind = function () {
        var self = this;
        this.button.addEventListener('click', function (e) {
            e.stopPropagation();
            if (self.panel.classList.contains('hidden')) { self.open(); } else { self.close(); }
        });
        document.addEventListener('click', function (e) {
            if (!self.root.contains(e.target)) { self.close(); }
        });
        this.root.addEventListener('keydown', function (e) {
            if (e.key === 'Escape') { self.close(); self.button.focus(); }
        });
        this.search.addEventListener('input', function () { self.filter(self.search.value); });
        if (this.items) {
            this.list.addEventListener('click', function (e) {
                var row = e.target.closest('.cp-item');
                if (!row) { return; }
                self.close();
                self.button.focus();
                if (self.selected[0] !== row.dataset.code) { self.set([row.dataset.code]); }
            });
            return;
        }
        this.list.addEventListener('change', function (e) {
            if (!e.target.classList.contains('cp-cb')) { return; }
            var code = e.target.value;
            var next = self.selected.slice();
            if (e.target.checked) {
                if (next.indexOf(code) === -1) { next.push(code); }
            } else {
                next = next.filter(function (c) { return c !== code; });
            }
            self.set(next);
        });
        this.list.addEventListener('click', function (e) {
            if (!e.target.classList.contains('cp-region-all')) { return; }
            e.preventDefault();
            var region = e.target.closest('.cp-group').dataset.region;
            var codes = catalog.countries
                .filter(function (c) { return c.region === region; })
                .map(function (c) { return c.code; });
            self.set(self.selected.concat(codes));
        });
        this.presetsBox.addEventListener('click', function (e) {
            var button = e.target.closest('.cp-preset');
            if (!button || button.disabled) { return; }
            e.preventDefault();
            var preset = catalog.presets.filter(function (p) { return p.id === button.dataset.preset; })[0];
            if (preset) { self.set(preset.codes); }
        });
        this.root.querySelector('.cp-select-all').addEventListener('click', function (e) {
            e.preventDefault();
            if (self.max) { return; }
            self.set(catalog.countries.map(function (c) { return c.code; }));
        });
        this.root.querySelector('.cp-select-none').addEventListener('click', function (e) {
            e.preventDefault();
            self.set([]);
        });
    };

    // ---- module surface ------------------------------------------------

    var CountryPicker = {
        init: function () {
            loadCatalog();
            Array.prototype.forEach.call(document.querySelectorAll('.cp-root'), function (root) {
                if (!instances[root.id]) { instances[root.id] = new Picker(root); }
            });
            return instances;
        },
        get: function (id) {
            // Lazily build when a page script asks before DOMContentLoaded.
            // The picker is loaded from the base template, so a page's own
            // script often runs first, and asking must not come back empty.
            if (!instances[id] && document.getElementById(id)) { CountryPicker.init(); }
            return instances[id];
        },
        country: function (code) { loadCatalog(); return byCode[String(code || '').toLowerCase()] || null; },
        name: function (code) {
            var c = CountryPicker.country(code);
            return c ? c.name : String(code || '').toUpperCase();
        },
        storeName: function (code) {
            // The server's phrase (aso.countries.store_phrase), which knows the
            // names that take "the"; never composed here.
            var c = CountryPicker.country(code);
            return c ? c.store : 'the App Store in ' + String(code || '').toUpperCase();
        },
        flag: function (code) {
            var c = CountryPicker.country(code);
            if (c) { return c.flag; }
            code = String(code || '').trim();
            if (code.length !== 2) { return ''; }
            return String.fromCodePoint.apply(null, code.toUpperCase().split('').map(function (ch) {
                return 0x1F1E6 + ch.charCodeAt(0) - 65;
            }));
        },
        label: function (code) {
            var flag = CountryPicker.flag(code);
            return (flag ? flag + ' ' : '') + CountryPicker.name(code);
        },
        region: function (code) {
            var c = CountryPicker.country(code);
            return c ? c.region : '';
        },
        all: function () { loadCatalog(); return catalog.countries.slice(); },
        // A script set a select's value without a change event: its picker follows.
        syncSelect: function (selectId) {
            CountryPicker.init();
            Object.keys(instances).forEach(function (id) {
                var picker = instances[id];
                if (picker.select && picker.select.id === selectId) { picker.syncSelect(); }
            });
        },
        onChange: function (id, fn) {
            var picker = instances[id];
            if (picker) { picker.listeners.push(fn); }
        },
        durationText: durationText
    };

    window.CountryPicker = CountryPicker;

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function () { CountryPicker.init(); });
    } else {
        CountryPicker.init();
    }
})();
