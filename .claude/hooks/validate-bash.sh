#!/bin/bash
# .claude/hooks/validate-bash.sh — UNIFIED across all repos.
# Pre-tool hook: block dangerous bash commands.
# Receives JSON on stdin with tool_input.command.
#
# Layer 1: destructive system commands (always blocked).
# Layer 2: force push to main/master (always blocked).
# Layer 2b: destructive git tree-resets — `git reset --hard` / forced
#          `git clean -f` (always blocked; they eat a parallel session's
#          uncommitted work — use git-session-guard.sh safe-sync instead).
# Layer 2c: local Docker ban — installing, downloading, or launching Docker
#          (Desktop) or a substitute daemon (colima) is always blocked.
#          Apple `container` is the fleet's SOLE local runtime; CI + Railway
#          build Dockerfiles on Linux, where this hook never runs.
# Layer 3: parallel-session safety ("one writer per HEAD",
#          docs/IDE/CLAUDE_CODE/PARALLEL_SESSIONS.md): when OTHER live Claude
#          sessions share this same checkout, block working-tree-rewriting git
#          operations (branch switch, checkout, rebase, pull, bare stash) in
#          the SHARED MAIN CHECKOUT — they would silently clobber the other
#          sessions' uncommitted tracked edits. Linked worktrees are exempt
#          (own HEAD). CC_PARALLEL_GUARD=off disables layer 3 only.

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty')

# ── Layer 1: destructive system commands ─────────────────────────────────
# Note: no bare "format " pattern — it matched innocent text like
# "ruff-format canonical" in commit messages; mkfs/diskutil cover real
# disk-formatting. The fork-bomb pattern is properly escaped (":(){" is
# regex metachars when unescaped).
# (No trailing word-boundary after "/" or "~" — \b never matches between a
# symbol and a space, which silently disabled the bare "rm -rf /" case.)
if echo "$COMMAND" | grep -qE '\brm\s+-rf\s+(/|~)|\bdd\s+if=|\bmkfs|\bdiskutil\s+erase|:\(\)\s*\{'; then
    echo "Blocked: Potentially destructive system command" >&2
    exit 2
fi

# ── Layer 2: git force push to main/master ────────────────────────────────
if echo "$COMMAND" | grep -qE 'git\s+push\s+.*--force.*\s+(main|master)\b'; then
    echo "Blocked: Force push to main/master is not allowed" >&2
    exit 2
fi

# ── Layer 2b: destructive tree-resets (ALWAYS blocked, like Layer 1) ──────
# `git reset --hard` and any forced `git clean` (-f, -fd, -xf, -fdx, …) are the
# two one-liners that silently destroy a PARALLEL session's uncommitted work
# (this exact data-loss happened 2026-06-10). Unlike Layer 3 these are blocked
# unconditionally — they are never the right tool here. NOT blocked: plain
# `git reset` / `--soft` / `--mixed` (HEAD move, keeps the tree) and
# `git clean -n` (dry-run). The `-[dxX]*f` cluster matches any short-flag group
# CONTAINING f (so -f, -fd, -df, -xf, -fdx) but never -n alone; `--force`/
# `--hard` are also matched explicitly via word boundaries.
if echo "$COMMAND" | grep -qE 'git(\s+-C\s+\S+)?\s+reset\s+(.*\s)?--hard\b' \
   || echo "$COMMAND" | grep -qE 'git(\s+-C\s+\S+)?\s+clean\s+(.*\s)?-[dxX]*f[dxXf]*\b' \
   || echo "$COMMAND" | grep -qE 'git(\s+-C\s+\S+)?\s+clean\s+(.*\s)?--force\b'; then
    echo "Blocked: 'git reset --hard' / 'git clean -f' can destroy a parallel" >&2
    echo "session's uncommitted work. Use" >&2
    echo "  scripts/ci/git-session-guard.sh safe-sync" >&2
    echo "to return to clean main (non-destructive). See" >&2
    echo "docs/IDE/CLAUDE_CODE/PARALLEL_SESSIONS.md" >&2
    exit 2
fi

# ── Layer 2c: local Docker ban (ALWAYS blocked; fleet container policy) ───
# Docker Desktop was decommissioned fleet-wide on 2026-07-08; Apple `container`
# is the SOLE local container runtime (docs/DEPLOYMENT/CONTAINER_PRINCIPLES.md
# in every core repo). Low-RAM Macs cannot even host Docker's ~4.5 GB VM — an
# agent must never (re)install Docker, download its installer, launch Docker
# Desktop, or stand up a substitute daemon (colima). Plain `docker ...` CLI
# calls are NOT blocked: without a daemon they fail loudly and harmlessly, and
# blocking the bare word would false-positive on docker_smoke_test.sh etc.
# Like Layer 2b this is a substring match — a commit/PR body that merely
# QUOTES one of these commands trips it too; use `--body-file` for such text.
if echo "$COMMAND" | grep -qiE '\b(brew|port)\s+(re)?install\s+(--cask\s+)?docker' \
   || echo "$COMMAND" | grep -qE 'open\s+(-[a-zA-Z]+\s+)*-a\s+"?Docker\b' \
   || echo "$COMMAND" | grep -qiE 'desktop\.docker\.com|get\.docker\.com|docker\.com/products|Docker\.dmg' \
   || echo "$COMMAND" | grep -qiE '\b(brew|port)\s+(re)?install\s+(--cask\s+)?colima\b|\bcolima\s+start\b'; then
    echo "Blocked: Docker must never be installed or run on a local Mac — the fleet" >&2
    echo "decommissioned Docker Desktop (2026-07-08); Apple 'container' is the sole" >&2
    echo "local runtime, and low-RAM Macs cannot host Docker's VM at all." >&2
    echo "CI + Railway still build Dockerfiles server-side (unchanged)." >&2
    echo "→ Local images: 'container system start' + the just container-* recipes." >&2
    echo "See docs/DEPLOYMENT/CONTAINER_PRINCIPLES.md" >&2
    exit 2
fi

# ── Layer 3: tree-rewriting git ops in a contended shared checkout ────────
[ "${CC_PARALLEL_GUARD:-on}" = "off" ] && exit 0

# Match the dangerous operations. Allowed: anything in a `git worktree add`
# line, `git stash push -- <paths>` (file-scoped backup) and read-only stash
# subcommands. `git checkout -- <paths>` stays blocked — it rewrites tracked
# files another session may be editing.
if echo "$COMMAND" | grep -qE 'git(\s+-C\s+\S+)?\s+(switch|rebase|pull)\b|git(\s+-C\s+\S+)?\s+checkout\s' \
   && ! echo "$COMMAND" | grep -qE 'git\s+worktree\s+add'; then
    GIT_OP=1
elif echo "$COMMAND" | grep -qE 'git(\s+-C\s+\S+)?\s+stash\b' \
   && ! echo "$COMMAND" | grep -qE 'git(\s+-C\s+\S+)?\s+stash\s+(push\s+(-m\s+\S+\s+)?--\s|list|show)'; then
    GIT_OP=1
else
    GIT_OP=0
fi
[ "$GIT_OP" = 1 ] || exit 0

# Where does the operation act? Honor an explicit `git -C <path>`, else cwd.
TARGET_DIR=$(echo "$COMMAND" | sed -nE 's/.*git[[:space:]]+-C[[:space:]]+("([^"]+)"|([^[:space:]]+)).*/\2\3/p' | head -1)
TARGET_DIR="${TARGET_DIR:-$PWD}"
[ -d "$TARGET_DIR" ] || exit 0

ROOT=$(git -C "$TARGET_DIR" rev-parse --show-toplevel 2>/dev/null) || exit 0
COMMON=$(git -C "$TARGET_DIR" rev-parse --path-format=absolute --git-common-dir 2>/dev/null) || exit 0
MAIN_ROOT="${COMMON%/.git}"

# Linked worktree → own HEAD, no contention with the main checkout.
[ "$ROOT" != "$MAIN_ROOT" ] && exit 0

GUARD="$ROOT/scripts/ci/git-session-guard.sh"; [ -e "$GUARD" ] || GUARD="$ROOT/scripts/git-session-guard.sh"
[ -x "$GUARD" ] || exit 0

OTHERS=$(cd "$ROOT" && "$GUARD" others)
[ "${OTHERS:-0}" -gt 0 ] || exit 0

cat >&2 <<EOF
Blocked: this git operation rewrites the working tree of the SHARED main
checkout, and $OTHERS other live Claude session(s) are using it right now —
their uncommitted tracked edits would be silently clobbered (this exact
incident happened on 2026-06-10).
→ Work in your own worktree instead:
    EnterWorktree (preferred), or: git worktree add <path> -b <branch> origin/main
  Branch switching, rebase, pull and bare stash are all fine inside a worktree.
(Emergency override: CC_PARALLEL_GUARD=off. See docs/IDE/CLAUDE_CODE/PARALLEL_SESSIONS.md)
EOF
exit 2
