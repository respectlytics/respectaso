"""The top bar's activity indicator, run under node with a tiny fake page.

Reported 2026-10-01: after all work ended, the badge never went away and its
tick appeared and disappeared every 2 seconds, pushing the nav items back and
forth (classList.toggle got undefined, which flips instead of setting).
"""

import json
import shutil
import subprocess
from pathlib import Path

from django.test import SimpleTestCase

SCRIPT = Path(__file__).resolve().parents[2] / "static" / "js" / "activity-indicator.js"

# A fake page with just what the script touches, and a clock we move by hand.
HARNESS = r"""
const ids = ['activity-root','activity-pill','activity-panel','activity-spinner','activity-done','activity-failed',
             'activity-name','activity-pct','activity-signal','activity-waiting','activity-waiting-count'];
const els = {};
function el(id) {
  const classes = new Set(id === 'activity-pill' || id === 'activity-panel' ? ['hidden'] : ['hidden']);
  return {id, textContent: '', style: {}, attrs: {}, offsetWidth: 146,
    classList: {add: (...c) => c.forEach(x => classes.add(x)), remove: (...c) => c.forEach(x => classes.delete(x)),
      contains: c => classes.has(c),
      toggle: (c, force) => { if (force === undefined) throw new Error('toggle without a force flips: ' + id);
                              force ? classes.add(c) : classes.delete(c); }},
    setAttribute(k, v) { this.attrs[k] = v; }, contains: () => false, _classes: classes};
}
ids.forEach(id => els[id] = el(id));
let now = 0; const timers = [];
global.setTimeout = (fn, ms) => { timers.push({at: now + ms, fn}); return timers.length; };
global.clearTimeout = id => { if (timers[id - 1]) timers[id - 1].fn = () => {}; };
function advance(ms) { const end = now + ms; let due; while ((due = timers.filter(t => !t.done && t.at <= end).sort((a, b) => a.at - b.at)[0])) { now = due.at; due.done = true; due.fn(); } now = end; }
global.document = {getElementById: id => els[id], addEventListener: () => {}};
global.window = {matchMedia: () => ({matches: true})};
require(SCRIPT_PATH);
const A = window.ActivityIndicator;
const shown = id => !els[id]._classes.has('hidden') && els[id].textContent;
const pill = () => els['activity-pill']._classes.has('hidden') ? '-' : shown('activity-signal') || shown('activity-pct') || els['activity-name'].textContent;
const out = {};

// An old finished task at page load never shows the badge.
A.report('job', {done: {label: 'Keyword research'}}); A.report('queue', null);
out.stale = pill();

// Running, then everything ends: a short Done signal in the same width, then gone.
A.report('queue', {running: {label: 'Competitor', pct: 34}, waiting: 0});
out.running = pill();
A.report('queue', null);
out.done = pill();
out.width = els['activity-pill'].style.width;
out.tickShown = !els['activity-done']._classes.has('hidden');
// The queue keeps polling every 2 seconds: the tick must not flip.
for (let i = 0; i < 5; i++) { A.report('queue', null); }
out.tickStillShown = !els['activity-done']._classes.has('hidden');
advance(5000);
out.after = pill();
out.widthAfter = els['activity-pill'].style.width;
// More polls with the old finished task: still nothing.
for (let i = 0; i < 5; i++) { A.report('queue', null); A.report('job', {done: {label: 'Keyword research'}}); }
out.later = pill();

// A run that failed signals Stopped.
A.report('queue', {running: {label: 'Simulator', pct: 80}, waiting: 0});
A.report('queue', {done: {label: 'Simulator', failed: true}});
out.stopped = pill();
out.stoppedIcon = !els['activity-failed']._classes.has('hidden');
process.stdout.write(JSON.stringify(out));
"""


class ActivityIndicatorTest(SimpleTestCase):
    def test_the_badge_signals_once_and_goes(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        script = HARNESS.replace("SCRIPT_PATH", json.dumps(str(SCRIPT)))
        out = json.loads(subprocess.run([node, "-e", script], capture_output=True, text=True,
                                        check=True, timeout=30).stdout)
        self.assertEqual(out["stale"], "-")
        self.assertEqual(out["running"], "34%")
        self.assertEqual(out["done"], "Done")
        self.assertEqual(out["width"], "146px")
        self.assertTrue(out["tickShown"])
        self.assertTrue(out["tickStillShown"])
        self.assertEqual(out["after"], "-")
        self.assertEqual(out["widthAfter"], "")
        self.assertEqual(out["later"], "-")
        self.assertEqual(out["stopped"], "Stopped")
        self.assertTrue(out["stoppedIcon"])
