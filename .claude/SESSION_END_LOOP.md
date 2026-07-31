# Session-End Next-Steps Loop — the canonical core

**This file is byte-identical in every repo of the fleet, and CI enforces that.**
It is compared against the canonical copy in `command-central` on every pull
request; a divergent copy, or a missing one, fails the build. Do not edit it in
one repo alone — change the hub copy and propagate the same bytes everywhere, in
the same session.

It holds the rules that are true in **every** repo: what an interactive session
must do when it would otherwise stop. Repo-specific machinery — batch dispatch
commands, context-reset helpers, worked examples — lives in that repo's own
companion doc and is deliberately **not** here, because a rule that names a
command some repos do not have is a rule that fails on paste.

---

## 1. The one idea

**An interactive session never ends by silently stopping while sensible next
steps remain.** The end of a task is a fork in a persistent loop, not a full
stop. The operator works from a phone: a session that quietly halts has, from
their side, produced nothing they can act on.

So resolve "what next?" into exactly one of three moves.

---

## 2. The three moves

### Move 1 — CONTINUE (no stop, no question)

If concrete next steps were already **sanctioned** — explicitly agreed earlier
("do PRs 1–5"), or a trivially-implied direct follow-on — just **do the next
one**. Don't ask. Each new task is new work, so it gets a **fresh worktree and
branch** (§5).

### Move 2 — ASK (`AskUserQuestion`, never prose) — the default ending

**Always `multiSelect: true`. No exceptions.** It costs nothing and unlocks
three replies: check **one** option and you work it in-session; check one **and
type into "Other"** to steer it, and you fold that constraint in; check
**several** and you fan them out in parallel, each on its own branch.

#### The four option slots

Every menu offers four, together:

1. **One SUBSTANTIVE next task** from what you just did — the next *meaningful
   increment*, sized as one coherent PR. Not a trivial sliver: "update the docs"
   or a 20 %-of-the-feature chore is never the headline (a doc gap rides as a
   **second question**, §4). Never big for the sake of big either.
2. **One item drawn from this repo's task backlog**, picked by **relatedness** to
   the session's work — ready to implement now, or blocked on a decision and
   framed as *"work out its open decision so it becomes runnable"*. Draw from a
   **wide random pool**, never the top of a list: the randomness is what keeps
   parallel sessions from converging on the same item. Exclude anything another
   session has already claimed.
3. **One VISION-derived option**, every menu — a concrete, session-sized step
   toward a stated long-term goal, citing which goal it advances. The vision
   layer gets a standing seat, not only the escape.
4. **An escape worded exactly "None of these — propose different directions"** —
   never a bare "stop here".

**When a repo has no backlog file and no vision document, slots 2 and 3 are
YOURS to fill.** Offer two further genuinely *different* directions — another
subsystem, a known rough edge, a capability the repo visibly lacks — not three
rewordings of the first. **The menu stays four options wide; only the sources
change.** A menu that quietly shrinks to two reads as "two is fine", and that is
how the loop decays.

#### Wording — mandatory, every option

Write for a **technically-literate outsider**:

- **plain-language outcome first** — what changes for a person, not what file moves;
- keep **exactly one** precise internal term, in parentheses;
- **never** a bare file path or repo shorthand as a label.

Exemplar: *"TestFlight beta build — how real people install a pre-release on real
phones: does it feel right?"*

#### Work the pick, don't file it

**A chosen item is DIRECT work for this session.** Do not default to filing it
away for later; deferring is right only when the operator asked to defer, or the
item is genuinely too big to start now. "Never implement unshaped" is a rule for
*unattended automation*, which has no human to ask — the interactive loop is
exactly where building directly is allowed.

### Move 3 — Genuine STOP

Only when the work was a trivial one-line answer, or the **operator actually
disengages** — dismisses the question, ignores it, or types a stop via "Other".
Then emit the mobile summary and push. **Stopping is the operator's choice; it is
never an option you propose.**

---

## 3. The escape keeps the loop alive — it does NOT stop

When the operator picks **"None of these — propose different directions"**, do
not stop. Go looking somewhere you have **not** already proposed from — a
different subsystem, the vision document beyond the idea slot 3 already offered,
a re-sampled backlog excluding everything shown this session — then fire a
**fresh** `AskUserQuestion` (again `multiSelect: true`) with 2–4 **different**
items. Rotate, so the same candidates are not re-offered.

Where a vision document exists, it is the layer above the backlog: a long-term
goal, its pillars (each with an end state and building blocks), and explicit
non-goals. **A pillar with zero live backlog items is a vision gap** — propose
the first concrete step toward its end state and cite the pillar. Never propose
anything a non-goal rules out. **Prefer to BUILD such a gap directly** this
session; file it only to defer it.

---

## 4. The documentation check — did you write it down?

**Before composing the menu (and after each CONTINUE step), verify that what you
just did is documented.** Documentation is a first-class deliverable: if it is
not written down, it does not exist.

1. **Consult the repo's documentation index.** Is the surface you changed — an
   endpoint, a service, a convention, a security posture — already covered?
2. **If yes**, no doc option is needed.
3. **If no**, add a **second question** to the same `AskUserQuestion` call that
   **names the exact proposed location**: an existing file it fits into, or —
   when nothing fits — a proposed **folder and filename**, plus adding it to the
   index. Secondary, never the recommended headline.

**Name the place so the operator can correct it.** A wrong-area proposal is
exactly what they should be able to catch, and "Other" lets them redirect it in
one reply.

**Never let "a scheduled pass will catch it later" be the reason you didn't.**
A later automated sweep works from a diff, without the conversation that produced
it — so it can heal *currency*, but it can never ask where a doc belongs. Write
it while you still have the context.

---

## 5. The worktree-continuation rule (critical)

If the operator picks a task, you are in the **same session** but starting **new
work**. Never build it on the branch you just finished.

- **One pick** → finish the current task's PR first (watch CI to resolution and
  confirm the merge), leave that worktree, then create a **fresh** worktree and
  branch before implementing.
- **Two or more picks** → dispatch each as its own background run, **in parallel,
  not one after another**. Each still becomes its own worktree and its own PR.
  If a pick was steered via "Other", bake that constraint into its prompt — a
  detached run has none of this session's context, so write for a cold reader:
  the outcome, the why-now, and any handles you already hold.
- At the end of *that* task, ask again. The session becomes a **chain** of
  properly isolated, PR'd tasks, each gated by a phone-answerable question.

---

## 6. Two cases — know which you're in

- **Already-agreed multi-step work** ("do PRs 1–5") → **don't ask**; CONTINUE with
  the next one in a fresh worktree. Ask once the batch is exhausted, or a genuine
  fork appears.
- **Everything else** → ASK, in the format above.

---

## 7. Why `AskUserQuestion` is the primitive

Not prose ("say continue"), and not a notification alone. With remote control
connected, a fired question **pushes to the phone AND keeps the session alive**
waiting for an answer. So every ending is one tap — launch a task, steer one via
"Other", or ask for different directions. That is optionality, not noise, and
never a silent halt with work left on the table.

Consequently: **when you end on an `AskUserQuestion`, do not also fire a push
notification** — the question already pushed, and a second one is a double-ping.
Emit the mobile summary only on Move 3, when the operator actually disengages.

`AskUserQuestion` is a **main-agent-only** tool. Subagents relay proposals up to
the lead, which asks.

---

## 8. Scope & enforcement

**Interactive / remote-controlled sessions only.** Unattended automation has no
human to answer and keeps its existing behaviour: summary, then done.

Three Stop hooks back this up. They are **backstops, not the habit** — each
blocks at most once, honours the official `stop_hook_active` re-entrancy flag,
and fails **open** on every ambiguity, so the worst case is one dismissable
reminder and none can trap a session in a loop:

| hook | catches |
|---|---|
| `.claude/hooks/check-next-steps.sh` | stopping after substantive work with no question asked |
| `.claude/hooks/check-pr-finished.sh` | opening a PR and stopping without watching CI to resolution |
| `.claude/hooks/check-mobile-summary.sh` | finishing without the summary block the phone reads |

**They re-arm per task, not per session.** A session that stays open for days
gets a menu per task. This was session-scoped once, which silently disabled the
next-steps guard for 47 consecutive hours of a 52-hour session: an empty marker
file cannot say *which* thing it already warned about. That is why the PR guard
is keyed per pull request and the summary guard checks only the current turn.

The hooks adapt their advice to the host repo — they detect at runtime whether a
backlog sampler, a vision document, or a PR-finishing script is present, so what
they recommend is always a command that exists where they are running.

---

## 9. Repo-specific companions

This file is the core. Where a repo has extra machinery — batch dispatch,
context-reset helpers, worked examples — it documents that alongside its own
project instructions, and those docs defer to this one on the rules above.
