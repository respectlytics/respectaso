#!/usr/bin/env bash
# Stop hook: ensure Claude outputs [MOBILE_SUMMARY]...[/MOBILE_SUMMARY]
# before stopping.  Applies to ALL sessions (coordinator, agents, direct).
#
# This is a command-based Stop hook.  Claude Code passes context as JSON on stdin.
# The JSON includes "stop_hook_active" (bool) and "transcript_path" (string).
#
# Exit codes:
#   0  = allow stopping
#   2  = block stopping (stderr is sent as feedback to Claude)
#
# Strategy:
#   1. Check for MOBILE_SUMMARY markers in the transcript
#   2. If found → allow (exit 0)
#   3. If not found AND this is the first block → block with format instructions
#   4. If not found AND already blocked once → block with urgent reminder
#   5. If not found AND already blocked twice → allow anyway (prevent infinite loop)
#
# A counter file tracks how many times we've blocked for this transcript.

set -euo pipefail

# ── Decision tracing (diagnostic only) ───────────────────────────────────────
# Records WHY this guard went quiet. See .claude/hooks/lib/guard-trace.sh.
# NOTE the `set -e` above: every traced helper returns 0 unconditionally, or it
# would kill this hook outright. The stubs are LOAD-BEARING — without the lib,
# guard_allow must still exit 0 rather than be an undefined command.
if ! . "${BASH_SOURCE[0]%/*}/lib/guard-trace.sh" 2>/dev/null; then
  guard_trace() { :; }
  guard_arm()   { :; }
  guard_allow() { exit 0; }
  guard_block() { exit 2; }
fi

# Read stdin (JSON context from Claude Code)
INPUT=$(cat)
guard_arm check-mobile-summary \
  "$(printf '%s' "$INPUT" | jq -r '.session_id // "nosession"' 2>/dev/null || echo nosession)"

# Extract transcript path
TRANSCRIPT_PATH=$(echo "$INPUT" | python3 -c "
import sys, json
data = json.load(sys.stdin)
print(data.get('transcript_path', ''))
" 2>/dev/null || echo "")

if [ -z "$TRANSCRIPT_PATH" ] || [ ! -f "$TRANSCRIPT_PATH" ]; then
    # No transcript available — allow stopping (can't check)
    guard_allow 'no transcript — cannot check for the summary'
fi

# ── Banner-only exemption (auto-fired /freshness turn) ────────────────────────
# auto-remote-control.sh keyboard-submits `/freshness` on every fresh extension
# session; that turn's ENTIRE reply is a single "📦 Repo: …" banner line (by
# design — see .claude/commands/freshness.md). Such a turn must NOT be forced to
# emit a [MOBILE_SUMMARY] (it has no task to summarize) AND must NOT be allowed to
# satisfy this guard by emitting one (a false marker anywhere in the transcript
# would let a LATER real-work turn skip its summary — the grep below matches the
# whole file). So: if the LAST assistant text message is solely a single
# 📦-prefixed line, allow the stop WITHOUT requiring markers. A real work turn
# (last message = the summary, or any other multi-line / non-📦 text) fails this
# test and falls through to the normal marker check below — no regression, and
# the exemption never leaks into later turns of the same session.
if python3 - "$TRANSCRIPT_PATH" <<'PY' 2>/dev/null
import sys, json
tp = sys.argv[1]
last = ''
try:
    with open(tp, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if obj.get('type') != 'assistant':
                continue
            parts = []
            for b in (obj.get('message', {}).get('content') or []):
                if isinstance(b, dict) and b.get('type') == 'text':
                    parts.append(b.get('text', ''))
            t = ''.join(parts).strip()
            if t:
                last = t
    lines = [ln for ln in last.splitlines() if ln.strip()]
    sys.exit(0 if (len(lines) == 1 and lines[0].lstrip().startswith('📦')) else 1)
except Exception:
    sys.exit(1)
PY
then
    guard_allow 'banner-only /freshness turn — exempt by design'
fi

# ── Did THIS TURN emit the markers? ───────────────────────────────────────────
# Scoped to the current turn, not the whole file, and deliberately so. This guard
# asks a TURN-scoped question — "did you summarise the work you just did?" — and
# used to answer it with a SESSION-scoped fact: does the marker appear anywhere
# in the transcript. In a remote-controlled session that stays open for days, the
# first summary of the day then silences the guard for every task after it.
#
# That is not a hypothetical risk: check-next-steps.sh had the same shape (a
# session-scoped marker) and was silently disabled for 47 consecutive hours of a
# 52-hour session. The comment above already noted that this grep "matches the
# whole file", but defended only the narrow /freshness case.
#
# "This turn" = everything after the last genuine user prompt: a type:user entry
# that is NOT a tool_result carrier and NOT injected meta.
if python3 - "$TRANSCRIPT_PATH" <<'PY' 2>/dev/null
import json, sys

tp = sys.argv[1]
try:
    with open(tp, encoding="utf-8") as f:
        lines = f.readlines()
except Exception:
    sys.exit(0)  # unreadable -> fail OPEN (allow the stop), as before

# Harness-injected EVENTS that ride on a type:user entry but are not the user
# speaking. Counting one as a prompt makes every background event look like a
# fresh task and demands a new summary for a turn in which nobody said anything:
# in the session that added this, 31 of 44 apparent "turns" were notifications
# from a single monitor. A slash command and an interrupt ARE user actions and
# are deliberately NOT listed; nor is the post-compaction carry-over, where the
# session genuinely resumes.
INJECTED_EVENT_PREFIXES = ("<task-notification>", "<ide_opened_file>")


def is_real_user_prompt(raw: str) -> bool:
    try:
        obj = json.loads(raw)
    except Exception:
        return False
    if obj.get("type") != "user" or obj.get("isMeta"):
        return False
    content = obj.get("message", {}).get("content")
    if isinstance(content, list):
        # A tool_result rides on a type:user entry; it is not the user speaking.
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return False
        text = "".join(
            b.get("text", "") for b in content if isinstance(b, dict) and b.get("text")
        )
    else:
        text = content if isinstance(content, str) else ""
    return not text.lstrip().startswith(INJECTED_EVENT_PREFIXES)

start = 0
for i, raw in enumerate(lines):
    if is_real_user_prompt(raw):
        start = i
turn = "".join(lines[start:])
sys.exit(0 if ("[MOBILE_SUMMARY]" in turn and "[/MOBILE_SUMMARY]" in turn) else 1)
PY
then
    # Markers found — allow stopping and clean up counter
    COUNTER_FILE="/tmp/mobile_summary_hook_$(echo "$TRANSCRIPT_PATH" | md5 -q 2>/dev/null || md5sum <<< "$TRANSCRIPT_PATH" | cut -d' ' -f1)"
    rm -f "$COUNTER_FILE" 2>/dev/null
    guard_allow 'this turn emitted the summary markers'
fi

# Markers NOT found — decide whether to block based on retry count
COUNTER_FILE="/tmp/mobile_summary_hook_$(echo "$TRANSCRIPT_PATH" | md5 -q 2>/dev/null || md5sum <<< "$TRANSCRIPT_PATH" | cut -d' ' -f1)"
COUNT=$(cat "$COUNTER_FILE" 2>/dev/null || echo "0")
COUNT=$((COUNT + 1))
echo "$COUNT" > "$COUNTER_FILE"

if [ "$COUNT" -ge 3 ]; then
    # Already blocked twice — allow to prevent infinite loop, clean up
    rm -f "$COUNTER_FILE" 2>/dev/null
    guard_allow 'already blocked twice — releasing to avoid a loop'
fi

if [ "$COUNT" -eq 1 ]; then
    # First block — detailed format instructions
    echo "MANDATORY: You must output a mobile-friendly summary as your FINAL output before stopping. This project is used from a mobile phone and every session must end with this block.

Use this exact format:

[MOBILE_SUMMARY]
Status: SUCCESS | FAILED | NEEDS_INPUT
<concise 2-5 sentence summary for mobile>
[/MOBILE_SUMMARY]

For simple questions, summarize the answer. For tasks, state what was done and whether it worked." >&2
    guard_block 'no summary markers in this turn (first reminder)'
fi

# Second block — urgent reminder
echo "URGENT: You still have not output the [MOBILE_SUMMARY] block. Output it NOW as your very next message, then stop. Example:

[MOBILE_SUMMARY]
Status: SUCCESS
Your answer summary here in 1-2 sentences.
[/MOBILE_SUMMARY]" >&2
guard_block 'no summary markers in this turn (urgent reminder)'
