#!/usr/bin/env bash
# .claude/hooks/session-start.sh — UNIFIED across all repos.
# Auto-runs on every Claude Code session start (CLI, VS Code extension, Desktop,
# remote-controlled sessions) via the SessionStart hook in .claude/settings.json.
#
# Purpose (the deterministic guardrails, so they don't rely on agent memory):
#   0. Session guard — detect OTHER live Claude sessions sharing this checkout
#                      ("one writer per HEAD" — PARALLEL_SESSIONS.md). When
#                      contended: loud banner, NO pull, janitor stays passive.
#   1. Freshness    — fetch --prune (drops stale remote-tracking refs), then
#                     pull latest origin/main when on main AND uncontended.
#   2. Janitor      — scripts/ci/git-janitor.sh --mode session: deletes local
#                     branches confirmed merged (ancestry OR squash-merge
#                     confirmed via merged-PR head SHA), removes clean merged
#                     worktrees, returns the checkout to main when parked on a
#                     merged branch. Session-aware: never touches a live
#                     session's tree.
#   3. Branch guard — LOUD warning when HEAD is (still) not main after cleanup.
#   4. Orphan check — report uncommitted leftovers (only if the skill exists).
#   5. Worktree bootstrap — when this session runs inside a linked worktree
#                     that is missing its env, run setup-worktree.sh in the
#                     background so pre-commit/test envs work.
#   6. Guard self-check — do the hooks this repo WIRES actually exist on disk
#                     here? Committed-but-absent is invisible to `git status`
#                     and to any gate reading committed state; only the machine
#                     about to run them can tell, and only right now.
#
# SessionStart hooks run before the agent sees any user message — fast (15s
# budget) + non-destructive.

set -euo pipefail

PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
cd "$PROJECT_DIR" || exit 0

# The claude session process is this hook's parent. Children (janitor, guard)
# inherit it so "self" is consistent across all checks in this session.
export CC_SESSION_PID="${CC_SESSION_PID:-$PPID}"

BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
GUARD="$PROJECT_DIR/scripts/ci/git-session-guard.sh"; [ -e "$GUARD" ] || GUARD="$PROJECT_DIR/scripts/git-session-guard.sh"
OTHERS=0
if [ -x "$GUARD" ]; then
    OTHERS="$("$GUARD" others 2>/dev/null || echo 0)"
    "$GUARD" banner || true
fi

# ── 1. Freshness ─────────────────────────────────────────────────────────
# --prune drops remote-tracking refs whose branch GitHub already deleted.
# Advance local main to origin/main on EVERY session WITHOUT ever clobbering
# unsaved work. The key realisation: a fast-forward tolerates a DIRTY tree, so a
# checkout that permanently carries loop-hub scratch files (docs/LOOP_HUB/
# loop-memory/*.md) — or any half-finished edit — still catches up instead of
# silently lagging "behind by N":
#   • no local commits on main (behind or up-to-date — the overwhelming case) →
#       `git merge --ff-only origin/main`. A fast-forward rewrites no unsaved work,
#       and git REFUSES to overwrite any file the FF must touch, so it is safe with
#       a dirty tree AND under contention ($OTHERS live sessions). This is the fix
#       for the "Pull skipped → local main never advances" bug: the old
#       `pull --rebase` aborted on ANY dirty tracked file (exit 128), so a tree that
#       always carries scratch files never moved.
#   • solo + local commits on main to replay → `git pull --rebase` (genuinely needs
#       a clean tree; if dirty it skips — only the direct-to-main pipeline agents
#       ever have local main commits, and replaying over a dirty tree is unsafe).
#   • diverged / a dirty OR untracked file blocks the FF → reported LOUDLY in a
#       banner that NAMES the offending path(s) (parsed from git's own abort text),
#       never auto-resolved, so a stuck checkout is visible instead of silently
#       lagging. NOTE the FF also aborts on an UNTRACKED file that shadows a path an
#       incoming commit ADDS (git refuses to clobber untracked work) — not only on a
#       dirty tracked file; the banner wording covers both.
if git fetch --prune origin 2>/dev/null; then
    if [ "$BRANCH" = "main" ] && git rev-parse --verify -q origin/main >/dev/null 2>&1; then
        BEHIND="$(git rev-list --count HEAD..origin/main 2>/dev/null || echo 0)"
        AHEAD="$(git rev-list --count origin/main..HEAD 2>/dev/null || echo 0)"
        if [ "${OTHERS:-0}" -eq 0 ] && [ "${AHEAD:-0}" -gt 0 ]; then
            # Solo AND local commits on main → rebase replays them (needs clean tree).
            if git pull --rebase origin main 2>/dev/null; then
                echo "[session-start] Pulled latest main (rebased $AHEAD local commit(s))."
            else
                # A dirty tree OR a conflicting replay leaves an in-progress rebase;
                # abort it so the checkout is restored to its exact prior HEAD (a
                # true no-op) instead of a half-finished conflicted state.
                git rebase --abort 2>/dev/null || true
                echo "[session-start] Pull skipped (local main commits couldn't replay — dirty tree or conflict). Will sync at push time."
            fi
        # No local commits to replay → a fast-forward advances main and tolerates
        # non-conflicting dirt (git guards any file the FF touches). Safe solo AND
        # under contention. THIS is what keeps local main from lagging behind. We
        # run it capturing stderr (stdout→/dev/null) so that WHEN it aborts we can
        # NAME the blocking path(s) in the banner instead of a vague "dirty file".
        elif FF_ERR="$(git merge --ff-only origin/main 2>&1 1>/dev/null)"; then
            if [ "${BEHIND:-0}" -gt 0 ]; then
                echo "[session-start] Fast-forwarded main to origin/main (+$BEHIND; shared-with: $OTHERS)."
            else
                echo "[session-start] main already up to date with origin/main."
            fi
        elif [ "${AHEAD:-0}" -gt 0 ]; then
            echo "[session-start] ⚠️  main DIVERGED from origin/main (local +$AHEAD / origin +$BEHIND) — resolve via a PR."
        else
            # git lists the offending files indented under its "would be overwritten"
            # header (both the dirty-tracked and untracked-collision variants). Pull
            # those indented lines out to name what is actually pinning the checkout.
            BLOCKERS="$(printf '%s\n' "$FF_ERR" | grep -E '^[[:space:]]' | sed 's/^[[:space:]]*//' | grep -v '^$' | head -3 | tr '\n' ' ')"
            echo "════════════════════════════════════════════════════════════════"
            echo "[session-start] ⚠️  main is $BEHIND behind origin/main — a dirty or untracked file blocks the fast-forward."
            if [ -n "$BLOCKERS" ]; then
                echo "                blocked by: $BLOCKERS"
            fi
            echo "                → back it up & advance: scripts/ci/git-session-guard.sh safe-sync"
            echo "════════════════════════════════════════════════════════════════"
        fi
    fi
else
    echo "[session-start] Fetch skipped (no network or no remote). Proceeding on current base."
fi

# ── 2. Janitor sweep (squash-merge-aware, session-aware cleanup) ──────────
JANITOR="$PROJECT_DIR/scripts/ci/git-janitor.sh"; [ -e "$JANITOR" ] || JANITOR="$PROJECT_DIR/scripts/git-janitor.sh"
if [ -x "$JANITOR" ]; then
    "$JANITOR" --mode session || true
else
    # Fallback for repos without the janitor: ancestry-merged branches only.
    # (Does NOT catch squash merges — port scripts/ci/git-janitor.sh for that.)
    if [ "${OTHERS:-0}" -eq 0 ] && git rev-parse --verify origin/main >/dev/null 2>&1; then
        while IFS= read -r b; do
            [ -z "$b" ] && continue
            [ "$b" = "main" ] && continue
            [ "$b" = "$BRANCH" ] && continue
            if git merge-base --is-ancestor "$b" origin/main 2>/dev/null; then
                if git branch -d "$b" >/dev/null 2>&1; then
                    echo "[session-start] Pruned merged branch: $b"
                fi
            fi
        done < <(git for-each-ref --format='%(refname:short)' refs/heads/ 2>/dev/null)
    fi
    git worktree prune >/dev/null 2>&1 || true
fi

# Janitor may have returned the checkout to main — re-read before the guard.
BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"

# ── 3. Branch guard — loud warning when (still) NOT on main ──────────────
if [ "$BRANCH" != "main" ] && [ "$BRANCH" != "unknown" ]; then
    ahead="$(git rev-list --count origin/main..HEAD 2>/dev/null || echo '?')"
    behind="$(git rev-list --count HEAD..origin/main 2>/dev/null || echo '?')"
    echo "────────────────────────────────────────────────────────────────"
    echo "[session-start] ⚠️  You are on branch '$BRANCH', NOT main."
    echo "                ahead of main: $ahead   behind main: $behind"
    echo "                The janitor left it alone (unmerged work, dirty tree, or live sessions)."
    echo "                If this work is abandoned or already merged elsewhere:"
    echo "                → git switch main && git pull --rebase origin main"
    echo "                (Standard: one task = one branch = one PR merged with --delete-branch.)"
    echo "────────────────────────────────────────────────────────────────"
fi

# ── 4. Orphan check (only if the commit-leftovers skill is installed) ─────
if [ -x "$PROJECT_DIR/.claude/skills/commit-leftovers/scripts/check-orphans.sh" ]; then
    if ! "$PROJECT_DIR/.claude/skills/commit-leftovers/scripts/check-orphans.sh" --quiet 2>/dev/null; then
        echo "[session-start] ORPHANS DETECTED: Run /commit-leftovers to commit forgotten work."
    fi
fi

# ── 5. Worktree env bootstrap (linked worktrees only, background) ─────────
COMMON="$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null || echo "$PROJECT_DIR/.git")"
MAIN_ROOT="${COMMON%/.git}"
if [ "$PROJECT_DIR" != "$MAIN_ROOT" ] && [ -x "$PROJECT_DIR/.claude/hooks/setup-worktree.sh" ]; then
    if [ ! -e "$PROJECT_DIR/.env" ] && [ ! -e "$PROJECT_DIR/backend/.env" ] && [ ! -e "$PROJECT_DIR/mac/daemon/.env" ]; then
        nohup "$PROJECT_DIR/.claude/hooks/setup-worktree.sh" "$PROJECT_DIR" "$MAIN_ROOT" >/dev/null 2>&1 &
        echo "[session-start] Worktree env bootstrap started in background (setup-worktree.sh)."
    fi
fi

# ── 6. Guard self-check — is this checkout RUNNING the guards it declares? ──
# On 2026-07-31 all three sibling repos had every phone-facing Stop hook COMMITTED
# and not one of them on disk. Every audit agreed they were protected: `git status`
# was clean (it compares the tree to LOCAL HEAD, and those checkouts were merely
# behind), and CI's fleet gate reads committed state, so it passed too. Sessions
# opened there ran completely ungated. Nothing that could actually EXECUTE a hook
# ever checked whether the file was there — which is what this does, on the one
# machine and at the one moment where it is answerable.
#
# The loud signal keys on PRESENCE, not content. An absent hook cannot fire on any
# branch, so that has no false positives; a hook whose bytes differ from the shared
# ref is normal on a feature branch (this very hook is edited that way), so it gets
# a quiet line instead of a banner.
GUARD_REF=""
for _r in origin/main main origin/master master; do
    if git rev-parse --verify -q "$_r" >/dev/null 2>&1; then GUARD_REF="$_r"; break; fi
done
if [ -z "$GUARD_REF" ]; then
    # Three-valued on purpose: "could not look" is not "nothing wrong".
    echo "[session-start] guards: CANNOT VERIFY (no main/master ref) — not a pass."
else
    GUARD_MISSING=""; GUARD_DIFFERS=0; GUARD_PRESENT=0
    while IFS= read -r rel; do
        [ -n "$rel" ] || continue
        # A hook only matters here if this repo actually wires it; the playbook is
        # not wired anywhere, so it is checked unconditionally.
        case "$rel" in
            *.sh) grep -q "$(basename "$rel")" "$PROJECT_DIR/.claude/settings.json" 2>/dev/null || continue ;;
        esac
        if [ ! -f "$PROJECT_DIR/$rel" ]; then
            GUARD_MISSING="$GUARD_MISSING $rel"
        else
            GUARD_PRESENT=$((GUARD_PRESENT + 1))
            git show "$GUARD_REF:$rel" 2>/dev/null | cmp -s - "$PROJECT_DIR/$rel" \
                || GUARD_DIFFERS=$((GUARD_DIFFERS + 1))
        fi
    done <<EOF
$(git ls-tree -r --name-only "$GUARD_REF" -- .claude/hooks .claude/SESSION_END_LOOP.md 2>/dev/null \
    | grep -E '\.sh$|SESSION_END_LOOP\.md$' || true)
EOF
    if [ -n "$GUARD_MISSING" ]; then
        echo "────────────────────────────────────────────────────────────────"
        echo "[session-start] ⚠️  THIS SESSION IS RUNNING WITHOUT GUARDS IT DECLARES."
        for _m in $GUARD_MISSING; do
            echo "                absent from disk: $_m"
        done
        echo "                $GUARD_REF commits them; this checkout does not have them,"
        echo "                so nothing enforces them here no matter what the docs say."
        echo "                → git pull --ff-only    (then restart this session)"
        echo "────────────────────────────────────────────────────────────────"
    elif [ "$GUARD_PRESENT" -eq 0 ]; then
        # Vacuity: examining nothing must never render as a clean bill of health.
        echo "[session-start] guards: CANNOT VERIFY (found none to check) — not a pass."
    elif [ "$GUARD_DIFFERS" -gt 0 ]; then
        echo "[session-start] guards: $GUARD_PRESENT present, $GUARD_DIFFERS differing from $GUARD_REF (expected on a branch that edits one)."
    else
        echo "[session-start] guards: $GUARD_PRESENT present and matching $GUARD_REF."
    fi
fi

echo "[session-start] Session ready (branch: $BRANCH, shared-with: $OTHERS)."
