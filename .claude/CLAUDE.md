## How this repo is worked — session conventions

This repo is operated **from a mobile phone**. A Claude session here is usually
remote-controlled, which changes what "finishing" means: the session is the only
channel through which the operator learns anything, so a session that quietly
stops has, from the phone's point of view, done nothing.

Three conventions follow from that. Each has a Stop hook under
`.claude/hooks/` that catches the miss — **the hooks are backstops, not the
habit.** Each blocks at most once and fails open on every ambiguity, so the worst
case is one dismissable reminder.

### 1. Finish a substantive task with a push — MANDATORY

At the terminal completion of a substantive task, call the **`PushNotification`**
tool once, as the last action: a status prefix (✅ SUCCESS / ⚠️ FAILED /
❓ NEEDS_INPUT) plus one or two phone-readable sentences. No code, no file paths.

An open question already pushes to the phone; a task that merely *finishes* does
not. `PushNotification` reaches the phone when Remote Control is connected and
degrades to a harmless desktop notification otherwise, so there is no session
where calling it is wrong. Skip it only for a trivial one-line answer.

Backstop: `.claude/hooks/check-mobile-summary.sh`.

### 2. Never stop silently with sensible next steps left — MANDATORY

Default ending for an interactive session: fire an **`AskUserQuestion`** with
`multiSelect: true`, offering a substantive next task, two further genuinely
different directions, and an escape worded exactly
*"None of these — propose different directions"*. Never a "stop here" option —
stopping is the operator's choice, not a menu default.

Why a question rather than a summary: with Remote Control connected it **pushes
to the phone AND keeps the session alive** waiting for an answer, so every ending
is one tap instead of a silent halt.

This repo has **no roadmap file and no north-star document**, so where sister
repos fill two of those four slots automatically, here both are yours to fill —
propose real, different directions rather than shipping a two-option menu.

Write every option for a technically-literate outsider: plain-language outcome
first, at most one precise internal term, never a bare file path as a label.

Backstop: `.claude/hooks/check-next-steps.sh`.

### 3. Watch a PR you opened to resolution — MANDATORY

A UI session has no outer wrapper that outlives it, so after opening a PR it must
watch CI **in-session** rather than ending fire-and-forget — which is exactly how
a red PR silently never merges and nobody notices until the next day:

```bash
gh pr checks <n> --watch
gh pr view <n> --json state,mergedAt   # a green check is NOT a merge
```

Then report the real outcome — MERGED ✓ / CI FAILED ✗ and which check. Never say
"it'll auto-merge" and stop. Backgrounding the watch counts as compliance.

Backstop: `.claude/hooks/check-pr-finished.sh`.

---

## Guard hooks already in force

`.claude/hooks/validate-bash.sh` and `validate-edit.sh` run before every Bash and
every edit. They block destructive commands — history-rewriting resets, forced
working-tree cleans, recursive deletes, force-pushes to `main` — because several
Claude sessions can share this machine and those commands destroy another
session's uncommitted work. **Do not bypass a hook**: no `--no-verify`, no
`--no-gpg-sign`. If a guard blocks you, the answer is a worktree, not a retry.

These two plus `session-start.sh` are **unified across the fleet** — byte-identical
to the canonical copies in command-central, and enforced by a CI gate on every PR
there. Do not edit them here in isolation; a local-only fix protects nobody and
will be reported as drift.

## Commit conventions

- **Never** append `Co-Authored-By` or any attribution trailer to a commit
  message. Subject plus optional body only.
- Work on a branch, open a PR, let CI gate the merge. Do not push to `main`.

## What this repo does NOT have

Stated explicitly, because a session that assumes otherwise wastes a turn
discovering it: there is no roadmap directory, no `docs/NORTHSTAR.md`, no
`scripts/ci/finish-pr.sh`, and no `cw` / `cwr` dispatch commands. The Stop hooks
above detect this at runtime and adapt what they recommend, so their advice is
always a command that exists here.
