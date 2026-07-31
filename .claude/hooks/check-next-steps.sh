#!/usr/bin/env bash
# .claude/hooks/check-next-steps.sh — Stop hook (session-end "next steps" backstop).
#
# Makes the session-end habit deterministic: after substantive work in an
# INTERACTIVE session, don't just stop — propose the sensible next steps via
# AskUserQuestion (which, with Remote Control connected, pushes to the phone AND
# keeps the loop alive). The four option slots ALWAYS pair a follow-on task with
# the most-related roadmap item and a North-Star idea, plus an escape worded
# "None of these — propose different directions" — NEVER a "stop here" / "pause"
# option. Stopping is the user's choice
# (they disengage or type it via the auto-added "Other"), never a menu default the
# agent proposes. This hook is the BACKSTOP that fires ONCE if the agent forgets;
# the always-propose habit itself lives in CLAUDE.md ("Session-End Next-Steps Loop").
#
# It NEVER fires in headless automation (cw / cwr / cwo / overnight / loop hub):
# those runs are `claude ... -p` and have no human to answer a question. Firing an
# AskUserQuestion there would hang or corrupt the pipeline, so ruling out headless
# is the first substantive thing this hook does.
#
# SAFE BY DESIGN — same skeleton as check-pr-finished.sh:
#   • Honors `stop_hook_active` → never blocks a stop that continues a prior block.
#   • Per-USER-TURN marker → blocks at most ONCE per turn (re-arms on the next
#     prompt, so a multi-day session gets a menu per task, not one menu ever).
#   • Fails OPEN on every error / ambiguity (not a repo, no transcript, ps fails).
#   • Skips headless (`-p`) runs and pure-Q&A (no edits on the main checkout).
#   • Skips when an AskUserQuestion was already fired since the user last spoke,
#     and when an open PR still needs finishing (check-pr-finished owns that stop).
# Net worst case is a single dismissable reminder — it can never trap a session in
# a loop. To disable entirely: CC_NEXTSTEPS_GUARD=off.
set -uo pipefail

# ── Decision tracing (diagnostic only) ───────────────────────────────────────
# Records WHY this guard went quiet, so "satisfied" and "broken" stop looking
# identical from outside. See .claude/hooks/lib/guard-trace.sh for the full
# rationale (this hook was silently disabled for 47 hours and nothing could tell).
#
# The stubs are LOAD-BEARING. If the lib is missing or unreadable, guard_allow
# must still exit 0 — an undefined command would let execution fall THROUGH a
# gate that was supposed to stop there, turning a silent guard into a nagging
# one. Tracing may never change what this hook decides.
if ! . "${BASH_SOURCE[0]%/*}/lib/guard-trace.sh" 2>/dev/null; then
  guard_trace() { :; }
  guard_arm()   { :; }
  guard_allow() { exit 0; }
  guard_block() { exit 2; }
fi

# stdin is read ONCE, here. Reading it again later yields nothing.
INPUT="$(cat 2>/dev/null || true)"
SESSION_ID="$(printf '%s' "$INPUT" | jq -r '.session_id // "nosession"' 2>/dev/null || echo nosession)"
guard_arm check-next-steps "$SESSION_ID"

[ "${CC_NEXTSTEPS_GUARD:-on}" = "off" ] && guard_allow 'disabled via CC_NEXTSTEPS_GUARD=off'

# 1. Official re-entrancy guard: never block a Stop that continues a prior block.
if printf '%s' "$INPUT" | jq -e '.stop_hook_active == true' >/dev/null 2>&1; then
  guard_allow 'stop_hook_active — continuation of a prior block'
fi

# 2. HEADLESS guard (the important one). Secondary env markers first (cheap), then
#    walk a few process ancestors for a `claude` invocation carrying -p/--print.
#    All cw/cwr/cwo/overnight/loop runs spawn `claude ... -p`; interactive and
#    remote-controlled UI sessions never do (a remote session is intentionally
#    indistinguishable from a local one — both can answer a question).
if [ -n "${OVERNIGHT_STREAM_LOGS:-}${CW_SKIP_WATCH:-}${CW_SKIP_TEST_GATE:-}" ]; then
  guard_allow 'headless run — cw/cwr/cwo env marker present'
fi
_pid="${PPID:-1}"; _depth=0
while [ -n "$_pid" ] && [ "$_pid" -gt 1 ] 2>/dev/null && [ "$_depth" -lt 8 ]; do
  _args="$(ps -o args= -p "$_pid" 2>/dev/null || true)"
  _argv0="${_args%%[[:space:]]*}"                 # the executable (argv[0])
  case "${_argv0##*/}" in
    claude|claude-*)
      # Only the claude process itself — NOT a shell/wrapper whose command line
      # merely *mentions* "claude … -p" as data (that would false-positive).
      # Headless (`-p`/`--print`) appears before the long prompt arg, so it is
      # safe from `ps` truncation.
      if printf '%s' "$_args" | grep -qE '(^|[[:space:]])(-p|--print)([[:space:]]|$)'; then
        guard_allow 'headless run — claude -p found in process ancestry'
      fi
      ;;
  esac
  _ppid="$(ps -o ppid= -p "$_pid" 2>/dev/null | tr -d '[:space:]')"
  if [ -z "$_ppid" ] || [ "$_ppid" = "$_pid" ]; then break; fi
  _pid="$_ppid"; _depth=$((_depth + 1))
done

# 3. Must be a git repo (fail open otherwise).
TOPLEVEL="$(git rev-parse --show-toplevel 2>/dev/null)" || guard_allow 'not inside a git repo'
[ -n "$TOPLEVEL" ] || guard_allow 'git toplevel unavailable'
COMMON="$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" || guard_allow 'git common dir unavailable'
# symbolic-ref FIRST, deliberately. On an unborn HEAD (repo with no commits)
# `git rev-parse --abbrev-ref HEAD` writes "HEAD" to STDOUT *and* exits 128, so
# `$(rev-parse ... || echo HEAD)` captures both and yields "HEAD\nHEAD" — which
# matches none of the branch names below, silently skipping the gate. The
# `cmd || fallback` idiom is only safe when the failing command prints nothing.
# symbolic-ref --quiet honours that, and resolves an unborn branch to its name;
# rev-parse remains the fallback for a detached HEAD, where it prints "HEAD"
# and succeeds.
BRANCH="$(git symbolic-ref --quiet --short HEAD 2>/dev/null \
  || git rev-parse --abbrev-ref HEAD 2>/dev/null \
  || echo HEAD)"

TRANSCRIPT_PATH="$(printf '%s' "$INPUT" | jq -r '.transcript_path // ""' 2>/dev/null || echo "")"

# 4. Need a transcript to judge the turn; without one, fail open.
{ [ -n "$TRANSCRIPT_PATH" ] && [ -f "$TRANSCRIPT_PATH" ]; } || guard_allow 'no transcript — cannot judge the turn'

# 5. Locate the CURRENT USER TURN — the last genuine user prompt (a type:user
#    message that is NOT a tool_result carrier and NOT injected meta). Every
#    question below is asked about THIS TURN, not about the whole session: a
#    remote-controlled session stays open for DAYS across many separate tasks,
#    so "the session" is the wrong unit for all of them.
#    NOT every type:user entry is the user speaking. Three kinds ride on it:
#      • tool_result carriers        — the harness returning a tool's output
#      • isMeta entries              — injected context
#      • harness-injected EVENTS     — <task-notification> (a background task
#                                      reporting in) and <ide_opened_file>
#    The third kind is the one that bit us. Counting a task-notification as a
#    prompt makes every background event look like a fresh task: in the session
#    that added this, 31 of 44 apparent "turns" were notifications from a single
#    monitor, each one re-arming this hook and the mobile-summary guard for a
#    turn in which the user said nothing at all.
#    A slash command (<command-message>) and an interrupt ARE user actions and
#    stay in, as does the post-compaction carry-over — the session genuinely
#    resumes there. The exclusions below are anchored to the START of content so
#    a prompt that merely mentions one of these words is unaffected.
LAST_USER_ENTRY="$(grep -n '"type":"user"' "$TRANSCRIPT_PATH" 2>/dev/null \
  | grep -v 'tool_result' | grep -v '"isMeta":true' \
  | grep -v '"content":"<task-notification>' \
  | grep -v '"content":"<ide_opened_file>' \
  | tail -1 || true)"
LAST_USER_LINE="${LAST_USER_ENTRY%%:*}"
TURN_ID="$(printf '%s' "${LAST_USER_ENTRY#*:}" | jq -r '.uuid // empty' 2>/dev/null || true)"
# Unparseable transcript → degrade to the old once-per-session latch rather than
# risk nagging on every stop. Quieter is the safe direction to fail.
[ -n "$TURN_ID" ] || TURN_ID="whole-session"

# 6. Block at most ONCE per USER TURN — deliberately NOT once per session.
#    The marker stores WHICH turn was nudged and re-arms when a new prompt
#    arrives, so task #7 of a long session gets the same menu task #1 got.
#    This was a real outage, not a hypothetical: a session-scoped marker
#    silently disabled this hook for 47 consecutive hours of a 52-hour session
#    (it fired once, then every later stop matched the same file and exited 0).
#    "Already nudged" could not distinguish "for THIS task" from "for a
#    different task two days ago" — an unmeasured condition reported as a
#    confident negative.
#    The anti-loop property is unchanged: within one turn it still fires at most
#    once, and `stop_hook_active` above already covers immediate re-entry.
NUDGE="$COMMON/claude-nextsteps-nudged-$SESSION_ID"
# Markers are one tiny file per session and were never reaped; drop our own
# stale ones so $COMMON does not accumulate them forever.
find "$COMMON" -maxdepth 1 -name 'claude-nextsteps-nudged-*' -mtime +14 -delete 2>/dev/null || true
[ "$(cat "$NUDGE" 2>/dev/null || true)" = "$TURN_ID" ] && guard_allow 'already nudged for this user turn'

# 6b. Does the CURRENT TURN contain <pattern>?
#
#     `grep -q` is DELIBERATELY not used here, and must never be reintroduced.
#     It exits the moment it matches, which SIGPIPEs the upstream `tail`
#     (status 141); under `set -o pipefail` that becomes the pipeline's status,
#     so a MATCH is reported as a MISS. Plain `grep` reads its input to the end,
#     so `tail` finishes normally and the status is grep's own.
#
#     This only bites on a transcript large enough that `tail` is still writing
#     when grep would have bailed — under the 64 KB pipe buffer everything fits
#     and the bug is invisible. That is exactly why it survived a green test
#     suite: the fixtures are a handful of lines, the real transcript is 10 MB.
#     It made gates 7 and 8 below answer "no" to every question on any session
#     long enough to matter — silently, since both fail toward exit 0.
turn_contains() {
  [ -n "$LAST_USER_LINE" ] || return 1
  tail -n "+$LAST_USER_LINE" "$TRANSCRIPT_PATH" 2>/dev/null \
    | grep -E -- "$1" >/dev/null
}

# 7. Conversational turn? A turn that called no tool at all is an answer, not a
#    task — there is no work to propose a follow-on to. Branch-independent.
if [ -n "$LAST_USER_LINE" ]; then
  turn_contains '"type":"tool_use"' || guard_allow 'conversational turn — no tool call, nothing to follow on from'
fi

# 8. Substantive-work gate. On the shared main checkout, only nag if THIS TURN
#    actually edited files (pure Q&A on main is not substantive). Real code work
#    always happens on a feature/worktree branch (validate-edit forces it there).
#    Scoped to the turn: a days-long session accumulates edits from tasks long
#    since finished, and those must not vouch for the turn ending right now.
case "$BRANCH" in
  main|master|HEAD|unknown)
    if [ -n "$LAST_USER_LINE" ]; then
      turn_contains '"name":"(Edit|Write|MultiEdit|NotebookEdit)"' || guard_allow 'on main with no edits this turn — pure Q&A'
    else
      # No pipe here — grep reads the file itself, so -q is safe.
      grep -qE '"name":"(Edit|Write|MultiEdit|NotebookEdit)"' "$TRANSCRIPT_PATH" 2>/dev/null || guard_allow 'on main with no edits anywhere in transcript'
    fi
    ;;
esac

# 9. Already proposed? If an AskUserQuestion was fired since the user last spoke,
#    the agent did the right thing (the user's answer — including disengaging —
#    is a valid stop) → allow.
#    (This one failed the OTHER way under the SIGPIPE bug — a question that WAS
#    asked read as "not asked", so the hook nudged anyway. Noisy rather than
#    silent, but the same root cause.)
if [ -n "$LAST_USER_LINE" ]; then
  if turn_contains '"name":"AskUserQuestion"'; then
    guard_allow 'next steps already proposed this turn'
  fi
elif grep -qE '"name":"AskUserQuestion"' "$TRANSCRIPT_PATH" 2>/dev/null; then
  guard_allow 'next steps already proposed (whole-session fallback)'
fi

# 10. If a PR opened by this session is still OPEN, check-pr-finished.sh owns this
#     stop — let the PR get finished first; next-steps fires on a later stop.
if [ "${CC_PARALLEL_GUARD:-on}" != "off" ]; then
  PR_STATE="$(gh pr view --json state --jq '.state' 2>/dev/null || echo NONE)"
  [ "$PR_STATE" = "OPEN" ] && guard_allow 'an open PR is unfinished — check-pr-finished owns this stop'
fi

# 11. Substantive interactive stop with no next-steps question → nudge ONCE for
#     this turn. Stamp the turn id FIRST so the very next Stop is allowed no
#     matter what happens below.
printf '%s' "$TURN_ID" > "$NUDGE" 2>/dev/null || true

# The MENU SHAPE is host-specific; not one gate above is. Two of the four
# standing option slots name repo-local machinery — a roadmap sampler and a
# north-star document — that only ios-engine and command-central actually have.
# Printing them unconditionally in every repo would tell an agent to run a script
# that is not there and cite a file it cannot read, and advice that fails on
# paste does not degrade gracefully: it teaches the reader to skim the whole
# reminder. So the two slots are PROBED FOR, and a repo with neither is told
# plainly that both are its own to fill — rather than being handed a quietly
# shorter menu with no explanation, which is the same "absence rendered as a
# confident answer" this hook family exists to stamp out.
HAS_ROADMAP=0
if [ -d "$TOPLEVEL/docs/ROADMAP" ] && [ -f "$TOPLEVEL/scripts/roadmap/roadmap-sample.py" ]; then
  HAS_ROADMAP=1
fi
HAS_NORTHSTAR=0
if [ -f "$TOPLEVEL/docs/NORTHSTAR.md" ]; then
  HAS_NORTHSTAR=1
fi

cat >&2 <<'EOF'
You finished substantive work and are about to stop without proposing next steps.

Per "Session-End Next-Steps Loop" (CLAUDE.md): don't just write a summary, and do
NOT offer a "stop here" / "pause" option. Fire an AskUserQuestion — ALWAYS with
multiSelect: true, no exceptions — whose options pair, together:

  • 1 SUBSTANTIVE next task — the next meaningful increment given what you just
    did, sized as one coherent PR. NOT a trivial sliver: don't make "update the
    docs" or a 20%-of-the-feature chore the headline (a doc gap rides as a SECOND
    question naming its proposed location). Never big for the sake of big
    either — real, motivated work that visibly moves it forward.
EOF

if [ "$HAS_ROADMAP" = 1 ]; then
  cat >&2 <<'EOF'
  • 1 roadmap item picked by RELATEDNESS to this session's work, from a WIDE
    random pool:
        python3 scripts/roadmap/roadmap-sample.py --count 6 --json
    — group siblings first (the optional forward-only group: tag), else the most
    related item in the pool, else any sampled one (say it's unrelated). The POOL
    stays random so parallel sessions diverge; never eyeball the top of
    docs/ROADMAP/ROADMAP.md.
EOF
fi

if [ "$HAS_NORTHSTAR" = 1 ]; then
  cat >&2 <<'EOF'
  • 1 NORTH-STAR option, EVERY menu — derive one concrete session-sized idea from
    docs/NORTHSTAR.md BEFORE composing the menu (prefer a pillar / open block near
    this session's work) and cite the pillar id in the description.
EOF
fi

if [ "$HAS_ROADMAP" = 0 ] && [ "$HAS_NORTHSTAR" = 0 ]; then
  cat >&2 <<'EOF'
  • 2 FURTHER proposals of your own. This repo has no roadmap sampler and no
    north-star document, so the two slots those normally fill are YOURS: offer a
    second and third genuinely DIFFERENT direction — another subsystem, a known
    rough edge, a capability the repo visibly lacks — not three rewordings of one
    idea. The menu stays four options wide; only the sources changed.
EOF
elif [ "$HAS_ROADMAP" = 0 ] || [ "$HAS_NORTHSTAR" = 0 ]; then
  cat >&2 <<'EOF'
  • 1 FURTHER proposal of your own, filling the standing slot this repo has no
    source for — a genuinely different direction, not a variation of the first.
EOF
fi

cat >&2 <<'EOF'
  • an escape worded exactly "None of these — propose different directions" — on
    that path, go looking somewhere you have NOT already proposed from, then
    re-ask with different candidates.
EOF

if [ "$HAS_ROADMAP" = 1 ]; then
  cat >&2 <<'EOF'
    On the escape, re-sample: roadmap-sample.py --exclude <already-shown>.
EOF
fi
if [ "$HAS_NORTHSTAR" = 1 ]; then
  cat >&2 <<'EOF'
    On the escape, also dig into docs/NORTHSTAR.md beyond the standing North-Star
    option — a pillar with zero live roadmap items is a vision gap.
EOF
fi

cat >&2 <<'EOF'

  Word every option for a technically-literate outsider: plain-language outcome
  first, keep exactly ONE precise internal term, never bare file paths or repo
  shorthand as labels.

    ToolSearch: "select:AskUserQuestion"   →   call AskUserQuestion

Work a chosen item as DIRECT work — implement it yourself this session. Do NOT
default to filing it away for later; deferring is right only when the USER asked
to defer it, or it is genuinely too big to start now.
EOF

if [ "$HAS_ROADMAP" = 1 ]; then
  cat >&2 <<'EOF'
(Here that deferral is /roadmap-add — a reflex worth resisting.)
EOF
fi

cat >&2 <<'EOF'
With Remote Control connected
the question pushes to the phone and keeps the session alive: the user launches a
pre-laid-out task with one tap, steers a pick by also typing into "Other", or asks
for different directions. Stopping is the user's choice — they disengage or type a stop via
"Other"; it is NEVER a menu option you propose.

Acting on the picks:
  • ONE pick → work it in-session: finish the current PR, ExitWorktree, then
    EnterWorktree for a fresh branch and implement it.
EOF

if [ "$HAS_ROADMAP" = 1 ]; then
  cat >&2 <<'EOF'
  • 2+ independent picks → dispatch each as its own background run (cwr <id> /
    cw "<description>") — fire in parallel, not one-at-a-time. If a pick was steered
    via "Other", bake that constraint into its dispatched prompt.
EOF
else
  cat >&2 <<'EOF'
  • 2+ independent picks → dispatch each as its own background session, in
    parallel rather than one-at-a-time, each on its own branch. If a pick was
    steered via "Other", bake that constraint into its dispatched prompt.
EOF
fi

cat >&2 <<'EOF'

If the next steps were already agreed earlier (e.g. "do PRs 1-5"), DON'T ask — just
continue with the next one (in its own worktree). Only skip the question entirely
when this was a trivial one-line answer.

This reminder fires ONCE per user turn — after you ask (or if you legitimately
have nothing to propose), stopping again will proceed. It re-arms on the next
prompt, so each task in a long session gets its own menu.
EOF
guard_block 'substantive turn ended with no next-steps question'
