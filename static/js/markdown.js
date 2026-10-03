/* Markdown to HTML: the one renderer of the app.
 *
 * Draws every piece of markdown the app shows: the AI tabs' written analysis
 * (the Niche Overview, the App Brief, the competitor analysis, the strategy,
 * the Simulator's feedback) and the update banner's release notes, in both
 * editions. Nothing else in the app turns markdown into HTML
 * (aso/tests/test_markdown.py fails on a second copy).
 *
 * Safe by construction: every character of the text is escaped before any
 * markup is added, so the only tags in the result are the renderer's own:
 *   h2 h3 p br ul ol li strong em del code pre blockquote hr
 *   div (class="overflow-x-auto", around a table) table thead tbody tr th td
 * with a numeric start on ol and a text-align style on table cells.
 * Links are not drawn: [text](address) stays text. A link in a model's
 * answer would lead the app window away, and the model reads third party
 * app text that could steer it to any address.
 *
 * The result carries no styling: the container decides the look (.ai-prose
 * in static/css/tailwind.source.css on the AI tabs, the variants on
 * #update-notes-content in the base templates for the banner).
 *
 * Plain ES5 on purpose: the Mac app's window is the system WebKit, as old as
 * Safari 15.6 on macOS 12, which cannot parse newer syntax such as a
 * lookbehind in a regular expression.
 *
 *   AsoMarkdown.toHtml('## Title\n\n| a | b |\n|---|--:|\n| x | 1 |')
 *
 * docs/development/DEDUPE_RENDERERS_PLAN.md
 */
(function () {
    'use strict';

    var MAX_QUOTE_DEPTH = 3;

    function escapeHtml(text) {
        return String(text)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    // One line of text with its inline formatting. Code spans are set aside
    // first, so nothing inside them is formatted; the rest is escaped, then
    // bold, italics and strikethrough are added.
    function inline(raw) {
        var spans = [];
        var text = String(raw).replace(/(`+)([^`]|[^`][\s\S]*?[^`])\1(?!`)/g, function (match, ticks, body) {
            spans.push('<code>' + escapeHtml(body.replace(/^ (.*) $/, '$1')) + '</code>');
            return '\u0000' + (spans.length - 1) + '\u0000';
        });
        text = escapeHtml(text)
            .replace(/\*\*\*(?=\S)([\s\S]*?\S)\*\*\*/g, '<strong><em>$1</em></strong>')
            .replace(/\*\*(?=\S)([\s\S]*?\S)\*\*/g, '<strong>$1</strong>')
            .replace(/(^|[^A-Za-z0-9_])__(?=\S)([\s\S]*?\S)__(?![A-Za-z0-9_])/g, '$1<strong>$2</strong>')
            .replace(/\*(?=[^\s*])([^*]*?[^\s*])\*/g, '<em>$1</em>')
            .replace(/(^|[^A-Za-z0-9_])_(?=[^\s_])([^_]*?[^\s_])_(?![A-Za-z0-9_])/g, '$1<em>$2</em>')
            .replace(/~~(?=\S)([\s\S]*?\S)~~/g, '<del>$1</del>');
        return text.replace(/\u0000(\d+)\u0000/g, function (match, index) {
            return spans[Number(index)];
        });
    }

    function isBlank(line) {
        return /^\s*$/.test(line);
    }

    // Leading spaces, a tab counted as four.
    function indentOf(line) {
        var width = 0;
        for (var k = 0; k < line.length; k++) {
            var ch = line.charAt(k);
            if (ch === ' ') width += 1;
            else if (ch === '\t') width += 4;
            else break;
        }
        return width;
    }

    var FENCE = /^ {0,3}(`{3,}|~{3,})/;
    var HEADING = /^ {0,3}(#{1,6})[ \t]+(.*?)(?:[ \t]+#+)?[ \t]*$/;
    var RULE = /^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$/;
    var LIST_ITEM = /^([ \t]*)([-*+]|\d{1,9}[.)])[ \t]+(.*)$/;
    var QUOTE = /^ {0,3}>/;
    var DELIMITER_CELL = /^:?-+:?$/;

    // The cells of a table row: the outer pipes dropped, an escaped pipe
    // kept as a pipe.
    function splitRow(line) {
        var s = line.trim();
        if (s.charAt(0) === '|') s = s.slice(1);
        if (s.charAt(s.length - 1) === '|' && s.charAt(s.length - 2) !== '\\') s = s.slice(0, -1);
        var cells = [];
        var cell = '';
        for (var k = 0; k < s.length; k++) {
            var ch = s.charAt(k);
            if (ch === '\\' && s.charAt(k + 1) === '|') {
                cell += '|';
                k += 1;
            } else if (ch === '|') {
                cells.push(cell.trim());
                cell = '';
            } else {
                cell += ch;
            }
        }
        cells.push(cell.trim());
        return cells;
    }

    // The alignments of a delimiter row, or null when the line is not one.
    function delimiterRow(line) {
        if (line.indexOf('|') < 0 || line.indexOf('-') < 0) return null;
        var cells = splitRow(line);
        var aligns = [];
        for (var k = 0; k < cells.length; k++) {
            var cell = cells[k].replace(/\s+/g, '');
            if (!DELIMITER_CELL.test(cell)) return null;
            var left = cell.charAt(0) === ':';
            var right = cell.charAt(cell.length - 1) === ':';
            aligns.push(left && right ? 'center' : right ? 'right' : left ? 'left' : '');
        }
        return aligns;
    }

    function isTableStart(lines, i) {
        if (lines[i].indexOf('|') < 0 || i + 1 >= lines.length) return false;
        var aligns = delimiterRow(lines[i + 1]);
        return aligns !== null && aligns.length === splitRow(lines[i]).length;
    }

    function startsBlock(lines, i) {
        var line = lines[i];
        return FENCE.test(line) || HEADING.test(line) || RULE.test(line) || LIST_ITEM.test(line)
            || QUOTE.test(line) || isTableStart(lines, i);
    }

    function cellHtml(tag, content, align) {
        var style = align ? ' style="text-align:' + align + '"' : '';
        return '<' + tag + style + '>' + inline(content) + '</' + tag + '>';
    }

    function table(lines, i, out) {
        var head = splitRow(lines[i]);
        var aligns = delimiterRow(lines[i + 1]);
        var html = ['<div class="overflow-x-auto"><table><thead><tr>'];
        for (var c = 0; c < head.length; c++) html.push(cellHtml('th', head[c], aligns[c]));
        html.push('</tr></thead>');
        i += 2;
        var rows = [];
        while (i < lines.length && !isBlank(lines[i]) && lines[i].indexOf('|') >= 0) {
            var cells = splitRow(lines[i]);
            var row = ['<tr>'];
            for (var k = 0; k < head.length; k++) row.push(cellHtml('td', k < cells.length ? cells[k] : '', aligns[k]));
            row.push('</tr>');
            rows.push(row.join(''));
            i += 1;
        }
        if (rows.length) html.push('<tbody>' + rows.join('') + '</tbody>');
        html.push('</table></div>');
        out.push(html.join(''));
        return i;
    }

    // Bullet and numbered lists, nested by indent. A blank line between two
    // items keeps the list going; an indented line without a marker
    // continues the item above it on a new line.
    function list(lines, i, out) {
        var html = [];
        var stack = [];

        function open(tag, marker, indent) {
            var start = '';
            if (tag === 'ol') {
                var number = parseInt(marker, 10);
                if (number !== 1) start = ' start="' + number + '"';
            }
            html.push('<' + tag + start + '><li>');
            stack.push({tag: tag, indent: indent});
        }

        function close() {
            html.push('</li></' + stack.pop().tag + '>');
        }

        while (i < lines.length) {
            var line = lines[i];
            if (isBlank(line)) {
                var next = i + 1;
                while (next < lines.length && isBlank(lines[next])) next += 1;
                if (next < lines.length && LIST_ITEM.test(lines[next]) && !RULE.test(lines[next])) {
                    i = next;
                    continue;
                }
                break;
            }
            var item = RULE.test(line) ? null : line.match(LIST_ITEM);
            if (!item) {
                if (indentOf(line) >= 2 && !startsBlock(lines, i)) {
                    html.push('<br>' + inline(line.trim()));
                    i += 1;
                    continue;
                }
                break;
            }
            var indent = indentOf(item[1]);
            var tag = /\d/.test(item[2]) ? 'ol' : 'ul';
            if (!stack.length) {
                open(tag, item[2], indent);
            } else {
                while (stack.length > 1 && indent < stack[stack.length - 1].indent) close();
                var top = stack[stack.length - 1];
                if (indent >= top.indent + 2) {
                    open(tag, item[2], indent);
                } else if (top.tag !== tag) {
                    close();
                    open(tag, item[2], indent);
                } else {
                    html.push('</li><li>');
                }
            }
            html.push(inline(item[3]));
            i += 1;
        }
        while (stack.length) close();
        out.push(html.join(''));
        return i;
    }

    function blocks(lines, depth) {
        var out = [];
        var i = 0;
        while (i < lines.length) {
            var line = lines[i];
            if (isBlank(line)) {
                i += 1;
                continue;
            }
            var fence = line.match(FENCE);
            if (fence) {
                var mark = fence[1];
                var closing = new RegExp('^ {0,3}' + (mark.charAt(0) === '`' ? '`' : '~') + '{' + mark.length + ',}[ \\t]*$');
                var code = [];
                i += 1;
                while (i < lines.length && !closing.test(lines[i])) {
                    code.push(lines[i]);
                    i += 1;
                }
                i += 1;
                out.push('<pre><code>' + escapeHtml(code.join('\n')) + '</code></pre>');
                continue;
            }
            var heading = line.match(HEADING);
            if (heading) {
                var tag = heading[1].length <= 2 ? 'h2' : 'h3';
                if (heading[2]) out.push('<' + tag + '>' + inline(heading[2]) + '</' + tag + '>');
                i += 1;
                continue;
            }
            if (isTableStart(lines, i)) {
                i = table(lines, i, out);
                continue;
            }
            if (RULE.test(line)) {
                out.push('<hr>');
                i += 1;
                continue;
            }
            if (LIST_ITEM.test(line)) {
                i = list(lines, i, out);
                continue;
            }
            if (QUOTE.test(line) && depth < MAX_QUOTE_DEPTH) {
                var quoted = [];
                while (i < lines.length && QUOTE.test(lines[i])) {
                    quoted.push(lines[i].replace(/^ {0,3}> ?/, ''));
                    i += 1;
                }
                out.push('<blockquote>' + blocks(quoted, depth + 1) + '</blockquote>');
                continue;
            }
            var paragraph = [inline(line.trim())];
            i += 1;
            while (i < lines.length && !isBlank(lines[i]) && !startsBlock(lines, i)) {
                paragraph.push(inline(lines[i].trim()));
                i += 1;
            }
            out.push('<p>' + paragraph.join('<br>') + '</p>');
        }
        return out.join('');
    }

    function toHtml(text) {
        if (text === null || text === undefined) return '';
        var lines = String(text).replace(/\u0000/g, '').replace(/\r\n?/g, '\n').split('\n');
        return blocks(lines, 0);
    }

    window.AsoMarkdown = {toHtml: toHtml};
})();
