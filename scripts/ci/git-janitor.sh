#!/usr/bin/env bash
# scripts/ci/git-janitor.sh — UNIFIED across all repos (same file in every repo).
#
# Keeps the repository converged on a clean, up-to-date main:
#   1. Prunes stale remote-tracking refs (git fetch --prune; nightly mode only —
#      in session mode the session-start hook has already fetched with --prune).
#   2. Deletes local branches whose work is confirmed merged:
#        a) ancestry-merged branches (rebase/ff/merge-commit)  → git branch -d
#        b) SQUASH-merged branches: upstream is "[gone]" AND a merged PR with
#           that head branch exists on GitHub                   → git branch -D
#      Ancestry tests (merge-base --is-ancestor, branch -d, branch --merged)
#      can NEVER detect squash merges — the squashed commit has a different SHA,
#      so the branch tip is not an ancestor of main. That is why (b) exists and
#      why branches used to accumulate forever.
#   3. Removes leftover worktrees whose branch is confirmed merged and whose
#      tree is fully clean. Dirty/locked worktrees are only ever REPORTED.
#   4. Returns the MAIN checkout to main when it is parked on a branch that is
#      confirmed merged and has no uncommitted tracked changes.
#   5. Nightly mode: pulls latest main (--rebase) after cleanup.
#
# NEVER touched: main/master, any branch checked out in a worktree, any branch
# with an OPEN PR (this automatically protects all dependabot PRs), dependabot/*
# branches, anything dirty, and anything whose merge cannot be confirmed.
#
# Modes:
#   --mode session   fast path for the SessionStart hook (no fetch; gh is only
#                    called when there are actual candidates). Must stay well
#                    under the hook's 15s timeout.
#   --mode nightly   full sweep for the nightly hub (fetch --prune + pull main).
#   --dry-run        report what would happen; mutate NOTHING (not even
#                    remote-tracking refs or worktree metadata).
#   --json           print a JSON summary to stdout (human lines go to stderr).
#
# Always exits 0 (best-effort hygiene must never break a session or the hub).

set -uo pipefail

MODE="session"
DRY=0
JSON=0
while [ $# -gt 0 ]; do
    case "$1" in
        --mode) MODE="${2:-session}"; shift 2 ;;
        --dry-run) DRY=1; shift ;;
        --json) JSON=1; shift ;;
        *) echo "[janitor] unknown arg: $1" >&2; shift ;;
    esac
done

# Human-readable lines: stdout normally, stderr when stdout must be pure JSON.
say() { if [ "$JSON" = 1 ]; then echo "[janitor] $*" >&2; else echo "[janitor] $*"; fi; }

# ── Resolve the MAIN checkout root (works when invoked from inside a worktree) ─
COMMON_DIR="$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" || { say "not a git repo — nothing to do"; exit 0; }
ROOT="${COMMON_DIR%/.git}"
[ -d "$ROOT" ] || { say "cannot resolve main checkout root"; exit 0; }
g() { git -C "$ROOT" "$@"; }

MAIN="main"
g rev-parse --verify -q "refs/remotes/origin/$MAIN" >/dev/null 2>&1 || MAIN="master"
CURRENT="$(g rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"

# ── Session-awareness (PARALLEL_SESSIONS.md): never touch a live session's
# tree. LIVE_CWDS = cwds of all live Claude sessions on this machine;
# OTHERS_MAIN = other live sessions whose checkout IS this main checkout.
GUARD="$ROOT/scripts/ci/git-session-guard.sh"
LIVE_CWDS=""
OTHERS_MAIN=0
if [ -x "$GUARD" ]; then
    LIVE_CWDS="$("$GUARD" cwds 2>/dev/null || true)"
    OTHERS_MAIN="$(cd "$ROOT" && "$GUARD" others 2>/dev/null || echo 0)"
fi

# True if any live session cwd is inside the given directory.
live_session_inside() {
    local dir="$1" c
    [ -z "$LIVE_CWDS" ] && return 1
    while IFS= read -r c; do
        [ -z "$c" ] && continue
        case "$c/" in "$dir"/*) return 0 ;; esac
    done <<< "$LIVE_CWDS"
    return 1
}

EVENTS="$(mktemp -t git-janitor)" || exit 0
trap 'rm -f "$EVENTS"' EXIT
ev() { printf '%s\t%s\n' "$1" "${2:-}" >> "$EVENTS"; }

REMOTE_PRUNED=0
ONLINE=1

# ── 1. Fetch + prune remote-tracking refs (nightly only; session relies on hook) ─
if [ "$MODE" = "nightly" ]; then
    if [ "$DRY" = 1 ]; then
        REMOTE_PRUNED="$(g remote prune origin --dry-run 2>/dev/null | grep -c 'origin/' || true)"
        ev note "dry-run: would prune $REMOTE_PRUNED stale remote-tracking refs"
    else
        FETCH_OUT="$(g fetch --prune origin 2>&1)" || ONLINE=0
        if [ "$ONLINE" = 1 ]; then
            REMOTE_PRUNED="$(printf '%s\n' "$FETCH_OUT" | grep -c '\[deleted\]' || true)"
            [ "$REMOTE_PRUNED" -gt 0 ] && say "pruned $REMOTE_PRUNED stale remote-tracking refs"
        else
            ev note "fetch failed (offline?) — gh-confirmed deletions skipped this run"
            say "fetch failed (offline?) — conservative local-only pass"
        fi
    fi
fi

# ── 2. Build protection + candidate sets ─────────────────────────────────────
# Branches checked out in ANY worktree (incl. the main checkout) are protected
# from the plain-branch sweep; worktree branches are handled by the worktree sweep.
WT_PORCELAIN="$(g worktree list --porcelain 2>/dev/null || true)"
CHECKED_OUT="$(printf '%s\n' "$WT_PORCELAIN" | awk '/^branch /{sub("refs/heads/","",$2); print $2}')"

# Local branches with their upstream-track state. Format: name|track
BRANCH_STATE="$(g for-each-ref refs/heads --format='%(refname:short)|%(upstream:track)' 2>/dev/null || true)"

GONE_CANDIDATES=""
while IFS='|' read -r b track; do
    [ -z "$b" ] && continue
    case "$b" in "$MAIN"|master|dependabot/*) continue ;; esac
    [ "$track" = "[gone]" ] && GONE_CANDIDATES="$GONE_CANDIDATES$b"$'\n'
done <<< "$BRANCH_STATE"

# gh lookups: ONE bulk call each, and only when something actually needs them.
# MERGED_SHAS is the set of headRefOid of merged PRs — a local branch whose tip
# equals one of these SHAs is provably the EXACT state GitHub squash-merged.
# (Name-based matching would mis-fire on a recreated branch with the same name.)
MERGED_SHAS=""
MERGED_NAMES=""
OPEN_SET=""
GH_OK=0
need_gh=0
[ -n "$GONE_CANDIDATES" ] && need_gh=1
[ "$CURRENT" != "$MAIN" ] && [ "$CURRENT" != "unknown" ] && need_gh=1
printf '%s\n' "$WT_PORCELAIN" | grep -q '^worktree ' && [ "$(printf '%s\n' "$WT_PORCELAIN" | grep -c '^worktree ')" -gt 1 ] && need_gh=1
[ "$MODE" = "nightly" ] && need_gh=1
if [ "$need_gh" = 1 ] && [ "$ONLINE" = 1 ] && command -v gh >/dev/null 2>&1; then
    MERGED_INFO="$(cd "$ROOT" && gh pr list --state merged --limit 1000 --json headRefName,headRefOid --jq '.[] | .headRefOid + " " + .headRefName' 2>/dev/null)" \
        && OPEN_SET="$(cd "$ROOT" && gh pr list --state open --limit 200 --json headRefName --jq '.[].headRefName' 2>/dev/null | sort -u)" \
        && GH_OK=1
    if [ "$GH_OK" = 1 ]; then
        MERGED_SHAS="$(printf '%s\n' "$MERGED_INFO" | awk '{print $1}' | sort -u)"
        MERGED_NAMES="$(printf '%s\n' "$MERGED_INFO" | awk '{print $2}' | sort -u)"
    else
        ev note "gh unavailable — squash-merge confirmations skipped this run"
    fi
fi

in_set() { printf '%s\n' "$2" | grep -qxF "$1"; }

# A branch is "confirmed merged" if ancestry-merged (any merge style that keeps
# the commit reachable) OR squash-confirmed (its tip SHA is the head of a
# merged PR — exact match, immune to branch-name reuse).
confirmed_merged() {
    local b="$1"
    if g merge-base --is-ancestor "$b" "origin/$MAIN" 2>/dev/null; then return 0; fi
    if [ "$GH_OK" = 1 ]; then
        local tip
        tip="$(g rev-parse --verify -q "refs/heads/$b" 2>/dev/null)" || return 1
        in_set "$tip" "$MERGED_SHAS" && return 0
    fi
    return 1
}

# ── 3. Worktree sweep (before branch sweep — their branches are checked out) ─
if [ "$DRY" = 0 ]; then g worktree prune >/dev/null 2>&1 || true; fi
MAIN_WT="$(printf '%s\n' "$WT_PORCELAIN" | awk '/^worktree /{print $2; exit}')"
printf '%s\n' "$WT_PORCELAIN" | awk '/^worktree /{wt=$2} /^branch /{sub("refs/heads/","",$2); print wt"|"$2} /^detached$/{print wt"|(detached)"}' | \
while IFS='|' read -r wt wb; do
    [ -z "$wt" ] && continue
    [ "$wt" = "$MAIN_WT" ] && continue
    case "$PWD/" in "$wt"/*) ev note "skipped worktree we are running inside: $wt"; continue ;; esac
    if live_session_inside "$wt"; then
        ev kept_worktree "$wt (live Claude session inside)"; continue
    fi
    [ "$wb" = "(detached)" ] && { ev kept_worktree "$wt (detached HEAD)"; continue; }
    if printf '%s\n' "$WT_PORCELAIN" | grep -A3 -xF "worktree $wt" | grep -q '^locked'; then
        ev kept_worktree "$wt (locked)"; continue
    fi
    if [ -n "$OPEN_SET" ] && in_set "$wb" "$OPEN_SET"; then
        ev kept_worktree "$wt (open PR: $wb)"; continue
    fi
    if [ ! -d "$wt" ]; then continue; fi
    dirty="$(git -C "$wt" status --porcelain 2>/dev/null | head -1)"
    if [ -n "$dirty" ]; then
        ev dirty_worktree "$wt ($wb)"; continue
    fi
    # NEVER remove a worktree whose branch is ZERO commits ahead of origin/main:
    # such a branch adds nothing to main, so there is NO merged work to reclaim —
    # removing it can only destroy an in-progress setup (a brand-new worktree whose
    # branch still sits at origin/main HEAD), never recover completed work. This
    # closes the zero-commit-worktree race: confirmed_merged() below counts a branch
    # at main's HEAD as "merged" (a commit is its own ancestor), which used to sweep
    # a freshly-created, still-initializing worktree out from under a live subagent.
    # A squash-merged branch keeps >=1 unique commit (count > 0), so it is unaffected
    # and still reclaimed below via the MERGED_SHAS path; git worktree prune (above)
    # still reclaims worktrees whose directory is gone.
    ahead="$(g rev-list --count "origin/$MAIN..$wb" 2>/dev/null || echo unknown)"
    if [ "$ahead" = "0" ]; then
        ev kept_worktree "$wt ($wb -- zero commits ahead; nothing to reclaim)"; continue
    fi
    if confirmed_merged "$wb"; then
        if [ "$DRY" = 1 ]; then
            ev removed_worktree "(dry-run) $wt ($wb)"
        else
            if g worktree remove "$wt" >/dev/null 2>&1; then
                g branch -D "$wb" >/dev/null 2>&1 || true
                ev removed_worktree "$wt ($wb)"
                say "removed merged worktree: $wt ($wb)"
            else
                ev kept_worktree "$wt (remove refused)"
            fi
        fi
    else
        ev kept_worktree "$wt ($wb — not confirmed merged)"
    fi
done

# ── 4. Local branch sweep ────────────────────────────────────────────────────
while IFS='|' read -r b track; do
    [ -z "$b" ] && continue
    case "$b" in "$MAIN"|master|dependabot/*) continue ;; esac
    in_set "$b" "$CHECKED_OUT" && continue
    if [ -n "$OPEN_SET" ] && in_set "$b" "$OPEN_SET"; then
        ev kept_branch "$b (open PR)"; continue
    fi
    if g merge-base --is-ancestor "$b" "origin/$MAIN" 2>/dev/null; then
        if [ "$DRY" = 1 ]; then ev deleted_branch "(dry-run) $b [ancestry]"
        elif g branch -d "$b" >/dev/null 2>&1; then ev deleted_branch "$b [ancestry]"; say "deleted merged branch: $b"
        fi
        continue
    fi
    if confirmed_merged "$b"; then
        # Squash-merged: tip SHA equals a merged PR head. If the remote copy
        # still exists at that same merged SHA, it is a leftover from a merge
        # that skipped branch deletion — clean it too (nightly mode only).
        if [ "$MODE" = "nightly" ] && [ "$track" != "[gone]" ] && [ "$ONLINE" = 1 ]; then
            remote_tip="$(g rev-parse --verify -q "refs/remotes/origin/$b" 2>/dev/null || true)"
            if [ -n "$remote_tip" ] && in_set "$remote_tip" "$MERGED_SHAS"; then
                if [ "$DRY" = 1 ]; then ev deleted_remote "(dry-run) origin/$b [squash-merged leftover]"
                elif g push origin --delete "$b" >/dev/null 2>&1; then ev deleted_remote "origin/$b [squash-merged leftover]"; say "deleted remote leftover: origin/$b"
                fi
            fi
        fi
        if [ "$DRY" = 1 ]; then ev deleted_branch "(dry-run) $b [squash-merged PR]"
        elif g branch -D "$b" >/dev/null 2>&1; then ev deleted_branch "$b [squash-merged PR]"; say "deleted squash-merged branch: $b"
        fi
        continue
    fi
    if [ "$track" = "[gone]" ]; then
        if [ "$GH_OK" = 1 ] && in_set "$b" "$MERGED_NAMES"; then
            ev kept_gone "$b (upstream gone; name matches a merged PR but tip differs — left alone)"
        else
            ev kept_gone "$b (upstream gone, merge NOT confirmed — left alone)"
        fi
    fi
done <<< "$BRANCH_STATE"

# ── 4b. Remote leftover sweep (nightly): squash-merged remote branches that
# have no local counterpart. A remote branch whose tip equals a merged PR's
# head SHA and has no open PR is a leftover from a merge that skipped branch
# deletion. dependabot/* is never touched (its own flow manages those).
if [ "$MODE" = "nightly" ] && [ "$GH_OK" = 1 ] && [ "$ONLINE" = 1 ]; then
    while IFS='|' read -r rb rsha; do
        [ -z "$rb" ] && continue
        case "$rb" in "$MAIN"|master|HEAD|dependabot/*) continue ;; esac
        g rev-parse --verify -q "refs/heads/$rb" >/dev/null 2>&1 && continue  # has local: handled above
        [ -n "$OPEN_SET" ] && in_set "$rb" "$OPEN_SET" && continue
        if in_set "$rsha" "$MERGED_SHAS"; then
            if [ "$DRY" = 1 ]; then ev deleted_remote "(dry-run) origin/$rb [squash-merged leftover]"
            elif g push origin --delete "$rb" >/dev/null 2>&1; then ev deleted_remote "origin/$rb [squash-merged leftover]"; say "deleted remote leftover: origin/$rb"
            fi
        fi
    done < <(g for-each-ref 'refs/remotes/origin' --format='%(refname:short)|%(objectname)' 2>/dev/null | sed 's|^origin/||')
fi

# ── 5. Return the MAIN checkout to main when parked on a merged branch ──────
# Skipped entirely when other live sessions share the main checkout — a
# branch switch would rewrite the tree under them ("one writer per HEAD").
CURRENT="$(g rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
if [ "${OTHERS_MAIN:-0}" -gt 0 ]; then
    [ "$CURRENT" != "$MAIN" ] && ev parked "$CURRENT ($OTHERS_MAIN other live session(s) — no switch)"
elif [ "$CURRENT" != "$MAIN" ] && [ "$CURRENT" != "unknown" ]; then
    tracked_dirty="$(g status --porcelain -uno 2>/dev/null | head -1)"
    if [ -z "$tracked_dirty" ] && confirmed_merged "$CURRENT"; then
        if [ "$DRY" = 1 ]; then
            ev returned_to_main "(dry-run) from $CURRENT"
        elif g switch "$MAIN" >/dev/null 2>&1; then
            g branch -D "$CURRENT" >/dev/null 2>&1 || true
            ev returned_to_main "from merged branch $CURRENT"
            say "returned main checkout to $MAIN (was on merged '$CURRENT')"
        fi
    else
        reason="not confirmed merged"; [ -n "$tracked_dirty" ] && reason="uncommitted changes"
        ev parked "$CURRENT ($reason)"
        say "checkout parked on '$CURRENT' ($reason) — left alone"
    fi
fi

# ── 6. Nightly: pull latest main ─────────────────────────────────────────────
# Same one-writer rule as the session-start hook: solo → --rebase; shared but the
# tracked tree is clean → --ff-only (a fast-forward clobbers no unsaved work);
# shared + uncommitted tracked changes → skip (protect the session mid-edit).
CURRENT="$(g rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
if [ "$MODE" = "nightly" ] && [ "$DRY" = 0 ] && [ "$ONLINE" = 1 ] && [ "$CURRENT" = "$MAIN" ]; then
    if [ "${OTHERS_MAIN:-0}" -eq 0 ]; then
        if g pull --rebase origin "$MAIN" >/dev/null 2>&1; then
            ev pulled_main "ok"
        else
            ev note "pull --rebase skipped (uncommitted changes conflict)"
        fi
    elif [ -z "$(g status --porcelain -uno 2>/dev/null)" ]; then
        if g pull --ff-only origin "$MAIN" >/dev/null 2>&1; then
            ev pulled_main "ff-only (shared checkout, clean tree)"
        else
            ev note "pull skipped (shared checkout; main not fast-forwardable)"
        fi
    else
        ev note "pull skipped ($OTHERS_MAIN live session(s) + uncommitted changes — protected)"
    fi
fi

# ── Summary ──────────────────────────────────────────────────────────────────
deleted=$(grep -c '^deleted_branch' "$EVENTS" 2>/dev/null || true)
removed=$(grep -c '^removed_worktree' "$EVENTS" 2>/dev/null || true)
remote_left=$(grep -c '^deleted_remote' "$EVENTS" 2>/dev/null || true)
kept=$(grep -c '^kept_gone' "$EVENTS" 2>/dev/null || true)
dirty=$(grep -c '^dirty_worktree' "$EVENTS" 2>/dev/null || true)
# $REMOTE_PRUNED counts stale remote-tracking refs dropped by fetch --prune;
# $remote_left counts squash-merged remote leftovers actively deleted by §4b —
# two distinct things, so report both (the JSON path already does via
# deleted_remote_branches; this keeps the human summary honest too).
say "done: $deleted branch(es) deleted, $removed worktree(s) removed, $REMOTE_PRUNED remote ref(s) pruned, $remote_left remote leftover(s) deleted, $kept unconfirmed kept, $dirty dirty worktree(s) reported"

if [ "$JSON" = 1 ]; then
    REPO_NAME="$(basename "$ROOT")" MODE="$MODE" DRY="$DRY" REMOTE_PRUNED="$REMOTE_PRUNED" \
    MAIN_BRANCH="$MAIN" CURRENT_BRANCH="$CURRENT" GH_OK="$GH_OK" ONLINE="$ONLINE" \
    python3 - "$EVENTS" <<'PYEOF'
import json, os, sys
events = []
with open(sys.argv[1]) as f:
    for line in f:
        line = line.rstrip("\n")
        if not line:
            continue
        kind, _, detail = line.partition("\t")
        events.append((kind, detail))
def collect(kind):
    return [d for k, d in events if k == kind]
print(json.dumps({
    "repo": os.environ["REPO_NAME"],
    "mode": os.environ["MODE"],
    "dry_run": os.environ["DRY"] == "1",
    "online": os.environ["ONLINE"] == "1",
    "gh_confirmations": os.environ["GH_OK"] == "1",
    "main_branch": os.environ["MAIN_BRANCH"],
    "current_branch": os.environ["CURRENT_BRANCH"],
    "remote_refs_pruned": int(os.environ["REMOTE_PRUNED"] or 0),
    "deleted_branches": collect("deleted_branch"),
    "deleted_remote_branches": collect("deleted_remote"),
    "removed_worktrees": collect("removed_worktree"),
    "kept_gone_unconfirmed": collect("kept_gone"),
    "kept_branches": collect("kept_branch"),
    "kept_worktrees": collect("kept_worktree"),
    "dirty_worktrees": collect("dirty_worktree"),
    "parked": collect("parked"),
    "returned_to_main": collect("returned_to_main"),
    "pulled_main": bool(collect("pulled_main")),
    "notes": collect("note"),
}, indent=2))
PYEOF
fi

exit 0
