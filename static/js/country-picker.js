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
 *   CountryPicker.flag(code)     "bg" -> the flag
 *   CountryPicker.label(code)    flag + name
 *   CountryPicker.region(code)
 *   CountryPicker.onChange(id, fn)
 *
 * Deliberately not virtualized. 175 rows is an order of magnitude below where
 * that pays for itself, and the list is built once, lazily, on first open.
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

    function durationText(seconds) {
        if (!seconds || seconds <= 0) { return ''; }
        if (seconds < 60) { return Math.max(10, Math.round(seconds / 10) * 10) + ' seconds'; }
        var mins = Math.ceil(seconds / 60);
        return 'about ' + mins + ' minute' + (mins === 1 ? '' : 's');
    }

    // ---- one picker ----------------------------------------------------

    function Picker(root) {
        this.root = root;
        this.id = root.id;
        this.max = parseInt(root.dataset.max, 10) || 0;
        this.storageKey = root.dataset.storageKey || '';
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
    }

    Picker.prototype.restore = function () {
        var stored = null;
        try {
            stored = this.storageKey ? JSON.parse(localStorage.getItem(this.storageKey)) : null;
        } catch (e) { stored = null; }
        var codes = this.clean(stored || []);
        if (!codes.length && this.defaultPreset) {
            var preset = catalog.presets.filter(function (p) { return p.id === this.defaultPreset; }.bind(this))[0];
            if (preset) { codes = this.clean(preset.codes); }
        }
        if (!codes.length) { codes = this.clean(catalog.default); }
        return codes;
    };

    Picker.prototype.clean = function (codes) {
        var seen = {}, out = [];
        (codes || []).forEach(function (raw) {
            var code = String(raw || '').toLowerCase();
            if (byCode[code] && !seen[code]) { seen[code] = 1; out.push(code); }
        });
        return this.max ? out.slice(0, this.max) : out;
    };

    Picker.prototype.persist = function () {
        if (!this.storageKey) { return; }
        try { localStorage.setItem(this.storageKey, JSON.stringify(this.selected)); } catch (e) { /* private mode */ }
    };

    Picker.prototype.syncValue = function () {
        if (this.value) { this.value.value = this.selected.join(','); }
    };

    Picker.prototype.build = function () {
        if (this.built) { return; }
        var html = '';
        var self = this;
        // "Select all" is hidden on a capped picker: every region holds more
        // than the cap, so the click could only silently keep the first few.
        var showRegionAll = !this.max;
        catalog.regions.forEach(function (region) {
            var rows = catalog.countries.filter(function (c) { return c.region === region; });
            if (!rows.length) { return; }
            html += '<div class="cp-group" data-region="' + esc(region) + '">' +
                '<p class="cp-region sticky top-0 z-10 bg-slate-800 px-3 py-1 text-[10px] uppercase tracking-wider text-slate-500 flex items-center justify-between">' +
                '<span>' + esc(region) + '</span>' +
                (showRegionAll
                    ? '<button type="button" class="cp-region-all text-purple-300 hover:text-purple-200 normal-case tracking-normal">Select all</button>'
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
            marks += '<span class="cp-mark ml-1.5 text-[9px] px-1 py-px rounded bg-slate-700/40 text-slate-300 border border-white/10" ' +
                'title="Apple Ads does not operate in this storefront, so popularity here is RespectASO\'s estimate.">No Apple data</span>';
        }
        if (!c.has_language) {
            marks += '<span class="cp-mark ml-1.5 text-[9px] px-1 py-px rounded bg-slate-700/40 text-slate-300 border border-white/10" ' +
                'title="Apple offers no App Store listing language for this storefront. A listing here appears in your app\'s fallback language.">No App Store language</span>';
        }
        return '<label class="cp-row flex items-center gap-2 px-3 py-1.5 hover:bg-white/5 cursor-pointer text-sm" ' +
            'data-code="' + esc(c.code) + '" data-search="' + esc(c.search) + '">' +
            '<input type="checkbox" class="cp-cb rounded border-white/20 bg-slate-900 text-purple-500 focus:ring-purple-500" value="' + esc(c.code) + '">' +
            '<span class="shrink-0">' + c.flag + '</span>' +
            '<span class="text-slate-200 truncate">' + esc(c.name) + '</span>' +
            marks + '</label>';
    };

    Picker.prototype.paintChecks = function () {
        var chosen = {};
        this.selected.forEach(function (c) { chosen[c] = 1; });
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
        if (!this.presetsBox.dataset.built) {
            var html = '';
            catalog.presets.forEach(function (p) {
                if (this.max && p.codes.length > this.max) { return; }
                html += '<button type="button" class="cp-preset text-[11px] px-2 py-1 rounded-full ' +
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
        if (!codes.length) { this.buttonText.textContent = 'No countries'; return; }
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
