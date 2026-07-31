#!/usr/bin/env bash
# .claude/hooks/lib/guard-trace.sh — decision tracing for the Stop guards.
#
# WHY THIS EXISTS
# ---------------
# A guard hook that silently exits 0 is INDISTINGUISHABLE, from outside, from a
# guard that ran and was satisfied. Both produce exactly nothing. Every guard bug
# found on 2026-07-30 hid in that gap:
#
#   • check-next-steps.sh was disabled for 47 consecutive hours of a 52-hour
#     session by a session-scoped marker. It exited 0 every time. Nobody noticed.
#   • The same hook then read every match as a miss for ~5 hours because `grep -q`
#     SIGPIPEd its upstream `tail` under pipefail. It exited 0 every time.
#   • check-roadmap-status.sh was documented in CLAUDE.md as a MANDATORY Stop hook
#     that HARD-blocks while no file had ever been committed — it "exited 0" in the
#     most complete way possible, until it was written (with a mutation-tested
#     suite) and wired here.
#
# Each was found by deliberately breaking code and seeing whether a test noticed —
# never by observing a session. This file closes that: every guard records WHY it
# went quiet, so "satisfied" and "broken" stop looking alike. Read the trace with
# `python3 scripts/ci/guard-report.py`.
#
# THE HARD REQUIREMENT
# --------------------
# Tracing must be structurally incapable of changing what a guard decides. A
# diagnostic that can break the thing it observes is worse than no diagnostic. So:
#
#   • every function returns 0, always — the callers run under `set -e`
#     (check-mobile-summary.sh) and a non-zero return would kill the hook;
#   • every write is best-effort and swallowed — a read-only or full disk must
#     not turn a silent guard into a crashed one;
#   • the callers define fallback stubs when this file cannot be sourced, so a
#     missing/corrupt lib degrades to exactly the pre-tracing behaviour.
#
# To disable tracing entirely: CC_GUARD_TRACE=off

# Where the trace lands. Kept beside the other per-session guard markers in the
# COMMON git dir, which is shared by the main checkout and every worktree — so a
# session that moves between worktrees still writes one coherent trace.
_guard_trace_file() {
  local dir
  dir="${GUARD_TRACE_DIR:-}"
  if [ -z "$dir" ]; then
    dir="$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" || return 0
  fi
  [ -n "$dir" ] && [ -d "$dir" ] || return 0
  printf '%s/claude-guard-trace-%s.tsv' "$dir" "${GUARD_TRACE_SESSION:-nosession}"
  return 0
}

# guard_trace <hook> <decision> <reason>
# Appends one TSV row. Never fails, never prints to stdout/stderr.
guard_trace() {
  [ "${CC_GUARD_TRACE:-on}" = "off" ] && return 0
  local file
  file="$(_guard_trace_file)" || return 0
  [ -n "$file" ] || return 0
  # Reap our own stale traces so the git dir does not accumulate them forever —
  # the nudge markers went unreaped for months before anyone looked.
  find "${file%/*}" -maxdepth 1 -name 'claude-guard-trace-*.tsv' -mtime +14 -delete 2>/dev/null || true
  printf '%s\t%s\t%s\t%s\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo unknown)" \
    "${1:-unknown}" "${2:-unknown}" "${3:-unspecified}" >> "$file" 2>/dev/null || true
  return 0
}

# guard_arm <hook-name> [session-id]
# Call once, early, after the session id is known. Records the hook NAME for the
# later guard_allow/guard_block calls.
guard_arm() {
  GUARD_HOOK_NAME="${1:-unknown}"
  [ -n "${2:-}" ] && GUARD_TRACE_SESSION="$2"
  return 0
}

# guard_allow <reason>  — trace, then exit 0 (the guard stays silent).
# guard_block <reason>  — trace, then exit 2 (the guard blocks the stop).
#
# These REPLACE a bare `exit 0` / `exit 2`, so annotating a gate is a one-line
# change that cannot alter control flow: `exit` inside a shell function still
# exits the script.
guard_allow() {
  guard_trace "${GUARD_HOOK_NAME:-unknown}" allow "${1:-unspecified}"
  exit 0
}

guard_block() {
  guard_trace "${GUARD_HOOK_NAME:-unknown}" block "${1:-unspecified}"
  exit 2
}
