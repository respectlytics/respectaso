/*
 * Sorting a table by any of its columns, in the page, without a reload.
 *
 *   table[data-table-sort]
 *     th[aria-sort="descending" or "none"] holding the button that
 *       aso/partials/sort_button.html draws: button[data-sort-key="proceeds"],
 *       its words and an arrow whose halves are [data-sort-up] and [data-sort-down]
 *     tbody td[data-sort-key="proceeds"][data-sort-value="123.4"] in every row
 *
 * - A button's data-sort-type="text" compares words (A to Z first); every
 *   other column compares numbers (highest first). data-sort-first="asc"
 *   or "desc" changes which way the first click sorts; a second click on the
 *   same column turns the order round.
 * - A cell with an empty data-sort-value has no figure and stays at the
 *   bottom whichever way the column runs.
 * - Ties keep the order the page arrived in, so the table's own order is the
 *   tie breaker. The <th> whose aria-sort is not "none" on arrival names the
 *   order the rows already have; nothing is moved until a header is clicked.
 * - aria-sort, the header's colour and the arrow follow the order: the
 *   sorted column's words and the arrow's up or down half light up
 *   (text-slate-100), the rest stay dim.
 */
(function () {
    'use strict';

    function start(table) {
        var body = table.tBodies[0];
        if (!body) return;
        Array.prototype.forEach.call(body.rows, function (row, index) { row.dataset.sortIndex = String(index); });
        var buttons = Array.prototype.slice.call(table.querySelectorAll('button[data-sort-key]'));
        var current = null;
        buttons.forEach(function (button) {
            var sort = button.closest('th').getAttribute('aria-sort');
            if (sort === 'ascending' || sort === 'descending') {
                current = {key: button.dataset.sortKey, dir: sort === 'ascending' ? 'asc' : 'desc'};
            }
        });

        function value(row, key, text) {
            var cell = row.querySelector('[data-sort-key="' + key + '"]');
            var raw = cell ? cell.dataset.sortValue : '';
            if (raw === undefined || raw === null || raw === '') return null;
            if (text) return raw;
            var number = Number(raw);
            return isNaN(number) ? null : number;
        }

        function paint() {
            buttons.forEach(function (button) {
                var on = current && button.dataset.sortKey === current.key;
                var th = button.closest('th');
                th.setAttribute('aria-sort', on ? (current.dir === 'asc' ? 'ascending' : 'descending') : 'none');
                button.classList.toggle('text-slate-100', !!on);
                button.classList.toggle('text-slate-400', !on);
                var up = button.querySelector('[data-sort-up]');
                var down = button.querySelector('[data-sort-down]');
                if (up) up.setAttribute('class', on && current.dir === 'asc' ? 'text-slate-100' : 'text-slate-600');
                if (down) down.setAttribute('class', on && current.dir === 'desc' ? 'text-slate-100' : 'text-slate-600');
            });
        }

        function sortBy(button) {
            var key = button.dataset.sortKey;
            var text = button.dataset.sortType === 'text';
            var first = button.dataset.sortFirst || (text ? 'asc' : 'desc');
            current = current && current.key === key
                ? {key: key, dir: current.dir === 'asc' ? 'desc' : 'asc'}
                : {key: key, dir: first};
            var sign = current.dir === 'asc' ? 1 : -1;
            var rows = Array.prototype.slice.call(body.rows);
            rows.sort(function (a, b) {
                var va = value(a, key, text), vb = value(b, key, text);
                if (va === null || vb === null) {
                    if (va !== vb) return va === null ? 1 : -1;
                } else {
                    var order = text ? va.localeCompare(vb) : va - vb;
                    if (order) return order * sign;
                }
                return Number(a.dataset.sortIndex) - Number(b.dataset.sortIndex);
            });
            rows.forEach(function (row) { body.appendChild(row); });
            paint();
        }

        buttons.forEach(function (button) {
            button.addEventListener('click', function (event) {
                event.stopPropagation();
                sortBy(button);
            });
        });
        paint();
    }

    document.querySelectorAll('table[data-table-sort]').forEach(start);
})();
