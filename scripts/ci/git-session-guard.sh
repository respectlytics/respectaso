#!/usr/bin/env bash
# scripts/ci/git-session-guard.sh — UNIFIED across all repos (same file everywhere).
#
# Detection + arbitration for parallel Claude Code sessions sharing one machine.
# See docs/IDE/CLAUDE_CODE/PARALLEL_SESSIONS.md ("one writer per HEAD").
#
# Detection is a PROCESS SWEEP (ps + lsof of live claude processes), not a
# registration file — so it also protects sessions that were already running
# before this script existed. Results are cached for 10s per machine.
#
# Commands:
#   others          Count of OTHER live Claude sessions whose checkout root is
#                   the SAME checkout as $PWD (main checkout or worktree).
#                   Prints an integer. Sessions in different worktrees of the
#                   same repo do NOT count (separate HEAD = no contention).
#   cwds            All live Claude-session cwds on this machine (one per line).
#                   Used by the janitor to never touch a live session's tree.
#   lease-acquire   Acquire the main-writer lease for this session
#                   (pid = $CC_SESSION_PID, default $PPID). Succeeds if the
#                   lease is free, stale (holder pid dead), or already ours.
#                   Exit 0 = held by us; exit 1 = held by another LIVE session
#                   (holder pid printed).
#   lease-status    Print "free" or "held by pid <pid> (alive|stale)".
#   banner          Print a loud warning block when others > 0 (for hooks).
#   safe-sync       Return THIS checkout to clean origin/<main> WITHOUT any
#                   [--ff-only]   destructive op (never reset --hard / clean -f /
#                   checkout -f / history rewrite). Refuses if another live
#                   session shares the checkout. Clean+behind → ff-only merge;
#                   dirty → revert tracked edits by name + keep only untracked
#                   files already identical on origin, else ABORT (change
#                   nothing). --ff-only does ONLY the clean fast-forward
#                   (the mode safe for automation). Exit 0 = synced.
#
# Env:
#   CC_PARALLEL_GUARD=off   Kill switch: others -> 0, lease-acquire -> success.
#   CC_GUARD_FAKE_OTHERS=N  Test override for `others`.
#   CC_SESSION_PID          The session's claude process pid. session-start.sh
#                           exports it ($PPID); PreToolUse hooks inherit or
#                           default to their own $PPID (claude spawns hooks).
#
# Always exits 0 except lease-acquire contention (exit 1), safe-sync
# refusal/abort (non-zero), and usage (exit 64).

set -uo pipefail

CMD="${1:-}"
SELF_PID="${CC_SESSION_PID:-$PPID}"
CACHE_DIR="${TMPDIR:-/tmp}/cc-session-guard-$(id -u)"
CACHE="$CACHE_DIR/sweep"
TTL=10

mkdir -p "$CACHE_DIR" 2>/dev/null || true

# ── sweep: list "pid|checkout_root" for every live claude process ──────────
sweep() {
    # Serve from cache when fresh.
    if [ -f "$CACHE" ] && [ -n "$(find "$CACHE" -newermt "-${TTL} seconds" 2>/dev/null)" ]; then
        cat "$CACHE"
        return 0
    fi
    local out=""
    local pids
    pids="$(ps -axo pid=,command= 2>/dev/null | awk '
        $2 ~ /(^|\/)claude$/ || $0 ~ /native-binary\/claude/ { print $1 }')"
    local pid cwd root
    for pid in $pids; do
        cwd="$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -1)"
        [ -z "$cwd" ] && continue
        root="$(git -C "$cwd" rev-parse --show-toplevel 2>/dev/null)" || continue
        out="${out}${pid}|${root}|${cwd}"$'\n'
    done
    printf '%s' "$out" > "$CACHE" 2>/dev/null || true
    printf '%s' "$out"
}

others() {
    if [ "${CC_PARALLEL_GUARD:-on}" = "off" ]; then echo 0; return 0; fi
    if [ -n "${CC_GUARD_FAKE_OTHERS:-}" ]; then echo "$CC_GUARD_FAKE_OTHERS"; return 0; fi
    local my_root
    my_root="$(git rev-parse --show-toplevel 2>/dev/null)" || { echo 0; return 0; }
    # Self-exclusion is by PID *and* by cwd ($3): a --resume/re-exec or a
    # launcher+worker pair changes our pid out from under the cached sweep row,
    # so a row whose cwd == our $PWD is still US — never count it as "another"
    # session (regression: that misread blocked all work, 2026-06).
    sweep | awk -F'|' -v root="$my_root" -v self="$SELF_PID" -v mycwd="$PWD" '
        $2 == root && $1 != self && $3 != mycwd { n++ } END { print n+0 }'
}

cwds() {
    sweep | awk -F'|' '{ print $3 }' | sort -u
}

lease_file() {
    local common
    common="$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" || return 1
    echo "$common/claude-main-writer.lock"
}

lease_acquire() {
    if [ "${CC_PARALLEL_GUARD:-on}" = "off" ]; then return 0; fi
    local f holder
    f="$(lease_file)" || return 0
    if [ -f "$f" ]; then
        holder="$(cat "$f" 2>/dev/null | tr -dc '0-9')"
        # ps -p (not kill -0): kill -0 returns EPERM for other users' pids,
        # which would misread a live foreign holder as stale.
        if [ -n "$holder" ] && [ "$holder" != "$SELF_PID" ] && ps -p "$holder" >/dev/null 2>&1; then
            echo "$holder"
            return 1
        fi
    fi
    echo "$SELF_PID" > "$f" 2>/dev/null || true
    return 0
}

lease_status() {
    local f holder
    f="$(lease_file)" || { echo "free"; return 0; }
    if [ ! -f "$f" ]; then echo "free"; return 0; fi
    holder="$(cat "$f" 2>/dev/null | tr -dc '0-9')"
    if [ -z "$holder" ]; then echo "free"; return 0; fi
    if ps -p "$holder" >/dev/null 2>&1; then
        echo "held by pid $holder (alive)"
    else
        echo "held by pid $holder (stale)"
    fi
}

# ── default main branch name (portable, no hardcoded "main") ───────────────
# Prefer origin/HEAD's symbolic target; fall back to a local main|master, then
# to "main". Never fails the caller — printing a best-effort name is enough.
default_main() {
    local ref name
    ref="$(git symbolic-ref -q refs/remotes/origin/HEAD 2>/dev/null)"
    if [ -n "$ref" ]; then
        echo "${ref##*/}"
        return 0
    fi
    for name in main master; do
        if git show-ref --verify -q "refs/heads/$name" 2>/dev/null; then
            echo "$name"
            return 0
        fi
    done
    echo "main"
}

# ── safe-sync backup: make the one destructive step recoverable ─────────────
# safe_sync ABORTS rather than destroy everywhere EXCEPT one place: it reverts
# tracked working-tree edits by name, and `git checkout -- <f>` leaves no trace.
# These two helpers snapshot those edits first, so the revert is always undoable.
#
# `git stash create` is the right primitive: it builds a real commit object from
# the working tree WITHOUT touching the tree, the index, or the stash stack — so
# it cannot disturb a parallel session (a bare `git stash` would). The object is
# dangling, so we anchor it under refs/safe-sync/ to protect it from gc, and also
# emit a plain .patch for the common case where a human just wants `git apply`.
SAFE_SYNC_BACKUP_REF=""
SAFE_SYNC_BACKUP_PATCH=""

safe_sync_backup() {
    local tracked_changed="$1"
    SAFE_SYNC_BACKUP_REF=""
    SAFE_SYNC_BACKUP_PATCH=""
    [ -n "$tracked_changed" ] || return 0

    local git_dir stamp sha patch_dir patch_file
    git_dir="$(git rev-parse --git-dir 2>/dev/null)" || {
        echo "[safe-sync] ABORT: cannot locate .git dir to store a backup" >&2; return 1; }
    stamp="$(date '+%Y%m%d-%H%M%S')-$$"

    # 1. Human-readable patch — written BEFORE the revert, verified non-empty.
    patch_dir="$git_dir/safe-sync-backups"
    mkdir -p "$patch_dir" 2>/dev/null || {
        echo "[safe-sync] ABORT: cannot create $patch_dir" >&2; return 1; }
    patch_file="$patch_dir/$stamp.patch"
    git diff > "$patch_file" 2>/dev/null
    if [ ! -s "$patch_file" ]; then
        # tracked_changed was non-empty, so an empty diff means we did NOT capture
        # what we are about to destroy. Refuse — never revert an uncaptured edit.
        rm -f "$patch_file" 2>/dev/null
        echo "[safe-sync] ABORT: could not capture a backup patch of tracked edits;" >&2
        echo "            refusing to revert work we cannot restore." >&2
        return 1
    fi
    SAFE_SYNC_BACKUP_PATCH="$patch_file"

    # 2. Git object + ref — survives even if the patch file is cleaned up, and
    #    restores exactly (binary files, modes) where a patch can be lossy.
    sha="$(git stash create "safe-sync backup $stamp" 2>/dev/null)"
    if [ -n "$sha" ]; then
        if git update-ref "refs/safe-sync/$stamp" "$sha" 2>/dev/null; then
            SAFE_SYNC_BACKUP_REF="refs/safe-sync/$stamp"
        fi
    fi
    # The patch is the guaranteed backup; the ref is a bonus. We already proved
    # the patch is non-empty above, so a missing ref is not fatal.
    return 0
}

safe_sync_backup_report() {
    [ -n "$SAFE_SYNC_BACKUP_PATCH" ] || return 0
    echo "[safe-sync] those edits were BACKED UP first — nothing is lost:"
    echo "              patch: $SAFE_SYNC_BACKUP_PATCH"
    echo "              restore: git apply -3 $SAFE_SYNC_BACKUP_PATCH"
    if [ -n "$SAFE_SYNC_BACKUP_REF" ]; then
        echo "              ref:   $SAFE_SYNC_BACKUP_REF"
        echo "              inspect: git show $SAFE_SYNC_BACKUP_REF"
    fi
}

# ── safe-sync: return a dirty/diverged checkout to clean origin/<main> ──────
# The ONE tested implementation of the disciplined ladder. It NEVER runs
# `git reset --hard`, `git clean -f`, `git checkout -f`, or any history rewrite.
# Every step either ABORTS (changing nothing) or is RECOVERABLE: unique untracked
# files, a populated index and diverged history all abort, and the one step that
# does discard — reverting tracked working-tree edits by name — is snapshotted to
# a patch + ref first (safe_sync_backup) and the restore command is printed.
#   --ff-only   ONLY do the clean fast-forward; abort otherwise (automation-safe).
safe_sync() {
    local ff_only=0 arg
    for arg in "$@"; do
        case "$arg" in
            --ff-only) ff_only=1 ;;
            *) echo "[safe-sync] unknown arg: $arg" >&2; return 64 ;;
        esac
    done

    local root
    root="$(git rev-parse --show-toplevel 2>/dev/null)" || {
        echo "[safe-sync] not inside a git checkout" >&2; return 1; }

    # 1. Refuse if another LIVE session shares this checkout (reuse `others`).
    local n
    n="$(others)"
    if [ "${n:-0}" -gt 0 ]; then
        echo "[safe-sync] refused: $n other live Claude session(s) share this" >&2
        echo "            checkout — a sync rewrites the shared tree. Work in a" >&2
        echo "            worktree instead (EnterWorktree)." >&2
        return 1
    fi

    local main remote_ref
    main="$(default_main)"
    remote_ref="origin/$main"

    # 2. Read-only fetch.
    echo "[safe-sync] fetching $remote_ref ..."
    git fetch origin "$main" 2>/dev/null || {
        echo "[safe-sync] git fetch failed" >&2; return 1; }

    # Is the tree clean (no staged, unstaged, or untracked changes)?
    local dirty=0
    [ -n "$(git status --porcelain 2>/dev/null)" ] && dirty=1

    # Can we fast-forward? (HEAD is an ancestor of the remote ref.)
    local ff=0
    if git merge-base --is-ancestor HEAD "$remote_ref" 2>/dev/null; then
        ff=1
    fi

    # 3. Clean + fast-forwardable → just fast-forward.
    if [ "$dirty" = 0 ] && [ "$ff" = 1 ]; then
        git merge --ff-only "$remote_ref" >/dev/null 2>&1 || {
            echo "[safe-sync] ff-only merge failed unexpectedly" >&2; return 1; }
        echo "[safe-sync] clean fast-forward to $remote_ref — done."
        return 0
    fi

    if [ "$dirty" = 0 ] && [ "$ff" = 0 ]; then
        echo "[safe-sync] tree is clean but HEAD is not fast-forwardable to" >&2
        echo "            $remote_ref (diverged history). Not rewriting history." >&2
        return 1
    fi

    # --ff-only: the automation-safe mode does ONLY the clean fast-forward.
    if [ "$ff_only" = 1 ]; then
        echo "[safe-sync] --ff-only: tree is dirty (or not fast-forwardable);" >&2
        echo "            refusing to reconcile. Nothing changed." >&2
        return 1
    fi

    # 4. Dirty: reconcile, but ABORT (change nothing) unless provably safe.
    #
    # 4a. UNTRACKED files: keep only those already byte-identical on the remote.
    #     Any untracked file NOT already on the remote = unique work → ABORT.
    local untracked f
    untracked="$(git ls-files --others --exclude-standard 2>/dev/null)"
    if [ -n "$untracked" ]; then
        while IFS= read -r f; do
            [ -z "$f" ] && continue
            if ! git cat-file -e "$remote_ref:$f" 2>/dev/null; then
                echo "[safe-sync] ABORT: untracked file not on $remote_ref:" >&2
                echo "              $f" >&2
                echo "            It is unique work — refusing to delete it." >&2
                return 1
            fi
            # Exists on remote: contents must match byte-for-byte to be safe.
            if ! git cat-file -p "$remote_ref:$f" 2>/dev/null | cmp -s - "$f"; then
                echo "[safe-sync] ABORT: untracked file differs from $remote_ref:" >&2
                echo "              $f" >&2
                echo "            Refusing to overwrite unmerged local content." >&2
                return 1
            fi
        done <<EOF
$untracked
EOF
    fi

    # 4b. TRACKED modifications: only revert (by name) files whose ONLY diff is
    #     vs the working tree. Staged changes or a divergent index are not
    #     trivially reversible by name → ABORT rather than guess.
    if [ -n "$(git diff --cached --name-only 2>/dev/null)" ]; then
        echo "[safe-sync] ABORT: staged (index) changes present — not auto-" >&2
        echo "            reverting a populated index. Unstage or commit first." >&2
        return 1
    fi
    local tracked_changed
    tracked_changed="$(git diff --name-only 2>/dev/null)"

    # Must end fast-forwardable: after reverting tracked working-tree edits, HEAD
    # still has to be an ancestor of the remote, or a clean merge isn't possible.
    if [ "$ff" = 0 ]; then
        echo "[safe-sync] ABORT: local commits diverge from $remote_ref" >&2
        echo "            (not fast-forwardable). Refusing to rewrite history." >&2
        return 1
    fi

    # All checks passed — now perform the safe, by-name reconcile.
    if [ -n "$tracked_changed" ]; then
        # BACK UP FIRST — the revert below is the one irreversible step in this
        # whole function. Everything above ABORTS rather than destroy (unique
        # untracked, populated index, diverged history); only tracked edits get
        # thrown away, and `git checkout -- <f>` leaves no trace. On 2026-07-17 a
        # sync here destroyed 102 lines of a parked session's uncommitted research;
        # it survived only because the operator happened to take a patch by hand.
        # A backup makes the revert recoverable so that can never depend on luck.
        safe_sync_backup "$tracked_changed" || return 1

        while IFS= read -r f; do
            [ -z "$f" ] && continue
            # `git checkout -- <path>` / `git restore` revert BY NAME only — they
            # never touch other files and are not `checkout -f`.
            git checkout -- "$f" 2>/dev/null \
                || git restore -- "$f" 2>/dev/null \
                || { echo "[safe-sync] ABORT: could not revert $f" >&2; return 1; }
        done <<EOF
$tracked_changed
EOF
        echo "[safe-sync] reverted tracked working-tree edits (by name)."
        safe_sync_backup_report
    fi

    # Untracked files kept were already identical to the remote — no deletion.
    git merge --ff-only "$remote_ref" >/dev/null 2>&1 || {
        echo "[safe-sync] ff-only merge failed after reconcile" >&2; return 1; }
    echo "[safe-sync] reconciled and fast-forwarded to $remote_ref — done."
    return 0
}

banner() {
    local n
    n="$(others)"
    [ "$n" -gt 0 ] || return 0
    cat <<EOF
────────────────────────────────────────────────────────────────
[session-guard] ⚠️  SHARED CHECKOUT — $n other live Claude session(s)
                are using this same checkout (one HEAD, one tree).
                Do NOT edit tracked files or switch branches here.
                → Editing work: call EnterWorktree (or: git worktree add)
                  and work there. A branch is NOT isolation — only a
                  worktree is. See docs/IDE/CLAUDE_CODE/PARALLEL_SESSIONS.md
────────────────────────────────────────────────────────────────
EOF
}

case "$CMD" in
    others)        others ;;
    cwds)          cwds ;;
    lease-acquire) lease_acquire ;;
    lease-status)  lease_status ;;
    banner)        banner ;;
    safe-sync)     shift; safe_sync "$@" ;;
    *) echo "usage: git-session-guard.sh {others|cwds|lease-acquire|lease-status|banner|safe-sync [--ff-only]}" >&2; exit 64 ;;
esac
