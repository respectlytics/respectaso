#!/usr/bin/env bash
# .claude/hooks/check-pr-finished.sh — Stop hook.
#
# Nudges (at most ONCE per PULL REQUEST) when this session opened a PR but is
# stopping WITHOUT watching it to resolution (the "In-Session PR Finish —
# MANDATORY" rule).
#
# PORTABLE ACROSS REPOS. Every gate below depends only on git + gh, so this file
# is byte-identical in every repo that ships it; the one host-specific thing —
# WHICH watch command to recommend — is detected at runtime in §5. That split is
# the point: a hook copied around with its remedy hardcoded either names a script
# the host does not have, or gets hand-edited per repo and silently drifts.
#
# Scope is per-PR, not per-session — see §4. This header said "once per session"
# for a while AFTER §4 was changed, so the file contradicted itself about its own
# behaviour. That is the same class of defect as the bugs it guards against: a
# stated scope nobody re-checked against the code.
#
# SAFE BY DESIGN — learned the hard way from blog-engine's
#   `5ed0239a disable learnings-integrator hooks to fix recurring infinite loops`:
#   • Honors `stop_hook_active` (the official re-entrancy flag) → never blocks a
#     stop that is itself a continuation of a prior Stop-hook block.
#   • Per-PR nudge marker → blocks at most ONCE per pull request, ever.
#   • Silent while a CI watcher for this PR is running (§3b).
#   • Fails OPEN on every error / ambiguity (not a repo, on main, no PR, gh
#     offline, …).
# Net worst case is a single dismissable reminder — it can never trap a session
# in a loop. To disable entirely: CC_PARALLEL_GUARD=off.
set -uo pipefail

# ── Decision tracing (diagnostic only) ───────────────────────────────────────
# Records WHY this guard went quiet. See .claude/hooks/lib/guard-trace.sh.
# The stubs are LOAD-BEARING: without the lib, guard_allow must still exit 0, or
# execution would fall THROUGH a gate that was supposed to stop there. Tracing
# may never change what this hook decides.
if ! . "${BASH_SOURCE[0]%/*}/lib/guard-trace.sh" 2>/dev/null; then
  guard_trace() { :; }
  guard_arm()   { :; }
  guard_allow() { exit 0; }
  guard_block() { exit 2; }
fi

# stdin is read ONCE, here. Reading it again later yields nothing.
INPUT="$(cat 2>/dev/null || true)"
SESSION_ID="$(printf '%s' "$INPUT" | jq -r '.session_id // "nosession"' 2>/dev/null || echo nosession)"
guard_arm check-pr-finished "$SESSION_ID"

[ "${CC_PARALLEL_GUARD:-on}" = "off" ] && guard_allow 'disabled via CC_PARALLEL_GUARD=off'

# 1. Official re-entrancy guard: if we are already inside a Stop-hook
#    continuation, never block again.
if printf '%s' "$INPUT" | jq -e '.stop_hook_active == true' >/dev/null 2>&1; then
  guard_allow 'stop_hook_active — continuation of a prior block'
fi

# 2. Must be inside a git repo, on a feature branch (never nudge on main).
TOPLEVEL="$(git rev-parse --show-toplevel 2>/dev/null)" || guard_allow 'not inside a git repo'
[ -n "$TOPLEVEL" ] || guard_allow 'git toplevel unavailable'
COMMON="$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" || guard_allow 'git common dir unavailable'
# symbolic-ref FIRST, deliberately. On an unborn HEAD (a repo with no commits)
# `git rev-parse --abbrev-ref HEAD` writes "HEAD" to STDOUT *and* exits 128, so
# `$(rev-parse ... || echo HEAD)` captures both and yields "HEAD\nHEAD" — which
# matches none of the names below, so the branch guard is skipped and the hook
# nudges on a repo that has no branch at all. The `cmd || fallback` idiom is
# only safe when the failing command prints nothing; symbolic-ref --quiet
# honours that and resolves an unborn branch to its real name. rev-parse stays
# as the fallback for a detached HEAD, where it prints "HEAD" and succeeds.
BRANCH="$(git symbolic-ref --quiet --short HEAD 2>/dev/null \
  || git rev-parse --abbrev-ref HEAD 2>/dev/null \
  || echo HEAD)"
case "$BRANCH" in main|master|HEAD|unknown) guard_allow "on branch '$BRANCH' — never nudge off a feature branch" ;; esac

# 3. Does the current branch have an OPEN PR? Fail OPEN on any gh error/offline.
#    This has to come BEFORE the nudge check, because the marker is keyed on the
#    PR number.
PR_JSON="$(gh pr view --json number,state 2>/dev/null)" || guard_allow 'no PR for this branch (or gh offline)'
STATE="$(printf '%s' "$PR_JSON" | jq -r '.state // empty' 2>/dev/null || true)"
NUM="$(printf '%s' "$PR_JSON" | jq -r '.number // empty' 2>/dev/null || true)"
[ "$STATE" = "OPEN" ] || guard_allow "PR is $STATE, not OPEN — nothing to watch"
[ -n "$NUM" ] || guard_allow 'PR number missing from gh output'

# 3b. Is a watcher ALREADY running for this PR? Then the session is doing the
#     exact thing this hook exists to demand, and the nudge is a false alarm.
#     The rule is "watch the PR to resolution" — NOT "the PR must be resolved
#     before you may stop". finish-pr.sh blocks on CI for minutes and legitimately
#     outlives a turn, so a session that backgrounds it and reports the outcome
#     later is compliant. This fired against exactly that on 2026-07-30. A guard
#     that scolds correct behaviour teaches the reader to dismiss it, and a
#     dismissed guard is how a genuinely abandoned PR gets through — the failure
#     mode is trust, not logic.
#
#     Checked BEFORE the marker is written, deliberately. Consuming the
#     one-per-PR nudge here would spend it on a false alarm and leave the guard
#     silent for the case it is actually FOR: the same PR abandoned later with no
#     watcher at all.
#
#     The number is matched as a whole token, so a watcher on #9781 never excuses
#     #978. pgrep -f matches full command lines and never matches itself.
#
#     TWO shapes of watcher count, because finish-pr.sh is not present in every
#     repo this hook now ships to (see §5): the wrapper itself, and the plain
#     `gh pr checks --watch` that repos without it are told to run. Matching only
#     the wrapper would have made this section dead code everywhere else — a gate
#     that is structurally incapable of firing, which is precisely the shape of
#     bug this file's own history is about.
#
#     The BARE `gh pr checks --watch` form carries no PR number (it watches the
#     current branch), so it is matched without one, and a machine-wide pgrep
#     cannot tell which repo that process belongs to. The resulting bias toward
#     allowing is deliberate and is this section's whole rationale: a missed
#     reminder costs one nudge, whereas a nudge that scolds correct behaviour
#     teaches the reader to dismiss the guard — and a dismissed guard is how a
#     genuinely abandoned PR gets through.
for _re in \
  "finish-pr\.sh[^0-9]*${NUM}([^0-9]|\$)" \
  "gh pr checks[^0-9]*${NUM}([^0-9]|\$).*--watch" \
  "gh pr checks.*--watch[^0-9]*${NUM}([^0-9]|\$)" \
  "gh pr checks --watch([^0-9]|\$)"
do
  if pgrep -f "$_re" >/dev/null 2>&1; then
    guard_allow "a CI watcher for #$NUM is already running"
  fi
done

# 4. Block at most ONCE per PULL REQUEST — deliberately NOT once per session.
#    A session that stays open for days opens many PRs; a session-scoped marker
#    means the first one gets a nudge and every later one is left unwatched in
#    silence. (This is the same defect that took check-next-steps.sh out for 47
#    consecutive hours of a 52-hour session — an empty touch-file cannot say
#    WHICH thing it already warned about.) The PR number is the natural scope:
#    being told twice about #4242 is nagging, but #4243 is new information.
#    The anti-loop property is unchanged — within one PR it still fires once,
#    and `stop_hook_active` above already covers immediate re-entry.
NUDGE="$COMMON/claude-prfinish-nudged-$SESSION_ID"
[ "$(cat "$NUDGE" 2>/dev/null || true)" = "$NUM" ] && guard_allow "already nudged about #$NUM"
# Reap our own stale markers; they were never cleaned up.
find "$COMMON" -maxdepth 1 -name 'claude-prfinish-nudged-*' -mtime +14 -delete 2>/dev/null || true

# 5. Record the nudge FIRST, so the very next Stop is allowed no matter what the
#    agent does next (CI may even fail — that is a valid stop).
printf '%s' "$NUM" > "$NUDGE" 2>/dev/null || true

# The REMEDY is host-specific; the GATES above are not. Only ios-engine and
# command-central ship scripts/ci/finish-pr.sh, so naming it unconditionally
# would hand every other repo a command that does not exist — advice that reads
# as authoritative and fails on paste. `gh pr checks --watch` is the portable
# equivalent and is what §3b's bare-form pattern looks for.
#
# Tested with -x, not -f, deliberately: the question this answers is "can the
# agent run the exact line printed below", and a present-but-unexecutable script
# cannot be. Losing the +x bit downgrades the advice instead of breaking it.
if [ -x "$TOPLEVEL/scripts/ci/finish-pr.sh" ]; then
  WATCH_CMD="scripts/ci/finish-pr.sh $NUM"
  WATCH_TAIL="It blocks until CI resolves, confirms the merge, then ExitWorktree."
else
  WATCH_CMD="gh pr checks $NUM --watch"
  WATCH_TAIL="It blocks until CI resolves. Then confirm the merge actually landed
(gh pr view $NUM --json state,mergedAt) — a green check is not a merge."
fi

cat >&2 <<EOF
PR #$NUM is open and this session is stopping without watching it to resolution.
Per "In-Session PR Finish — MANDATORY", run:

    $WATCH_CMD

$WATCH_TAIL Report the real outcome (MERGED / CI FAILED). Backgrounding it
counts — this reminder stays silent while a watcher for #$NUM is running. It
fires once per pull request; stopping again will proceed.
EOF
guard_block "open PR #$NUM left unwatched"
