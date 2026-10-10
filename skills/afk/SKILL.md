---
name: afk
description: Run unattended. Set the away-from-keyboard contract once, then work toward a goal without asking questions, queue decisions in QUESTIONS.md, commit per unit of work, checkpoint to AGENTS.md, and stop cleanly when done, blocked or the user returns.
argument-hint: "<goal> [--hours N] [--max-continues N] [--checkpoint-every N] | resume | stop | status"
disable-model-invocation: true
allowed-tools: Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/afk.py" *) Bash(git status *) Bash(git diff *) Bash(git log *) Bash(git add *) Bash(git commit *) Read Write Edit
---

# /afk

The user is stepping away. Arguments: `$ARGUMENTS`

Tip for the user (do not change settings yourself): auto permission mode removes
permission prompts, which keeps an unattended run from stalling.

All state goes through one helper. Run it from the project root:

`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/afk.py" <subcommand>`

## Dispatch on the arguments

- Empty: say `/afk <goal>` is the usage, and stop.
- `status`: run `... status` and show the output. Nothing else.
- `stop`: run `... stop`, confirm it is paused, and stop working.
- `resume`: run `... resume --session "${CLAUDE_SESSION_ID}"` (add `--hours N` if given).
  Read its output (contract, state, QUESTIONS.md tail), then continue the run.
- Anything else is a goal. Pull any `--hours N`, `--max-continues N` or
  `--checkpoint-every N` out of the text; the rest is the goal. Run
  `... start --goal '<goal>' --session "${CLAUDE_SESSION_ID}"` with those flags. Quote
  the goal safely: single quotes, with each embedded `'` written as `'\''`. Then begin
  the first unit.

Hooks keep the run going: when you try to stop, a Stop hook feeds you the next step
until you call `end`, the deadline or max-continues passes, or the user types.

## The contract

1. **Never ask, never wait.** When something is ambiguous, pick the reversible default,
   append an entry to `QUESTIONS.md` (question, options, choice, why, how to undo), and
   move on.
2. **Work in units.** One unit is one coherent change. Run its tests, commit it with a
   plain message (follow the repo's attribution rules; stage only files you changed,
   never `.afk/`; add `QUESTIONS.md` or `handoff.md` only if the project tracks them),
   then run `... unit --summary "<one line>"`.
3. **Checkpoint.** When `unit` prints `CHECKPOINT DUE`, rewrite the `AGENTS.md`
   "Current state" section in place (at most ~10 lines, no history) and the backlog
   status line, if the project has them.
4. **Never** push or force-push, rewrite history, delete user data, change settings or
   permissions, spend money, or send messages. Queue any of these in `QUESTIONS.md`.
5. **Blocked.** If the next step needs one of those or a real human decision, finish all
   other work first. If nothing is left, run `... end --status blocked --reason "<why>"`.
6. **Done.** When the goal is met and verified: run the full test suite, write
   `handoff.md` (template below, overwrite any old one, do not commit it), run
   `... end --status done --reason "<one line>"`, then give a short summary: what was
   done, what was verified, and what is waiting in `QUESTIONS.md`.
7. **Context.** Keep tool output small (head, tail, targeted greps). After compaction or
   in a resumed session, trust `.afk/run.json`, git and `QUESTIONS.md` over memory.

## handoff.md template

```markdown
# Handoff: <project> - <YYYY-MM-DD HH:MM>

> Written by the previous session for the next one. Trust the code over this file.

## TL;DR
<3 lines max: goal, where it stands, the very next action>

## State of play
- Done: <what, and how it was verified>
- In progress: <what, exact place it stopped>
- Uncommitted / unpushed: <branch name, anything not pushed>

## Next steps
1. <concrete, ordered; file:line; command to run>

## Decisions made (and rejected options)
- <decision> - because <reason>. Rejected: <option> because <reason>.

## Open questions for the user
- <anything in QUESTIONS.md that needs a human>

## Verify quickly
<the 1-3 commands that show the current state: tests, build, run>
```

Mark anything not verified as *(unverified)*. Never include secrets or credentials.
