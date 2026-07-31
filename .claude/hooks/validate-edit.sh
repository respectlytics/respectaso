#!/bin/bash
# .claude/hooks/validate-edit.sh — UNIFIED across all repos.
# PreToolUse hook for Edit|Write|MultiEdit|NotebookEdit.
#
# Enforces "one writer per HEAD" (docs/IDE/CLAUDE_CODE/PARALLEL_SESSIONS.md):
# editing a TRACKED file in the SHARED MAIN CHECKOUT requires holding the
# main-writer lease. The first session to edit acquires it automatically
# (solo use feels nothing). If another live session holds it, the edit is
# blocked with instructions to work in a worktree instead.
#
# Never blocks: files in linked worktrees (own HEAD), untracked/new files
# (git switch never touches them), files outside any git repo, or anything
# when CC_PARALLEL_GUARD=off.

set -uo pipefail

[ "${CC_PARALLEL_GUARD:-on}" = "off" ] && exit 0

INPUT=$(cat)
FILE=$(echo "$INPUT" | jq -r '.tool_input.file_path // .tool_input.notebook_path // empty' 2>/dev/null)
[ -z "$FILE" ] && exit 0

# Nearest existing ancestor directory (Write may create new paths).
DIR=$(dirname "$FILE")
while [ ! -d "$DIR" ] && [ "$DIR" != "/" ]; do DIR=$(dirname "$DIR"); done

ROOT=$(git -C "$DIR" rev-parse --show-toplevel 2>/dev/null) || exit 0
COMMON=$(git -C "$DIR" rev-parse --path-format=absolute --git-common-dir 2>/dev/null) || exit 0
MAIN_ROOT="${COMMON%/.git}"

# Linked worktree (own HEAD/index/tree) → no contention, allow.
[ "$ROOT" != "$MAIN_ROOT" ] && exit 0

# Untracked / brand-new file → a branch switch never touches it, allow.
REL="${FILE#"$ROOT"/}"
git -C "$ROOT" ls-files --error-unmatch -- "$REL" >/dev/null 2>&1 || exit 0

GUARD="$ROOT/scripts/ci/git-session-guard.sh"; [ -e "$GUARD" ] || GUARD="$ROOT/scripts/git-session-guard.sh"
[ -x "$GUARD" ] || exit 0

if HOLDER=$(cd "$ROOT" && "$GUARD" lease-acquire); then
    exit 0
fi

cat >&2 <<EOF
Blocked: tracked file in the SHARED main checkout, and the main-writer lease
is held by another live session (pid $HOLDER). Editing here would race that
session's working tree.
→ Do your editing in your own worktree instead:
    EnterWorktree (preferred), or: git worktree add <path> -b <branch> origin/main
  then re-apply this edit there. Untracked/new files are not blocked.
(Emergency override: CC_PARALLEL_GUARD=off. See docs/IDE/CLAUDE_CODE/PARALLEL_SESSIONS.md)
EOF
exit 2
