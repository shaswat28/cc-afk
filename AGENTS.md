# cc-afk: project instructions

## Purpose

A Claude Code plugin with one skill, `/afk`. The user sets the "I'm away, keep working"
contract once; the session then works toward a goal unattended: it never asks blocking
questions, queues decisions in `QUESTIONS.md`, commits once per unit of work, checkpoints
to `AGENTS.md`, survives compaction, and stops cleanly when done, blocked or the user
returns. Plan: `Plans/v0.1-afk-skill.md`.

**This repository is public.** Never commit private or employer information: no client
names, internal hostnames, real ticket IDs from other projects, or paths that reveal them.
Examples and fixtures use neutral, invented names.

## Architecture

| Path | What it is |
| --- | --- |
| `.claude-plugin/` | Plugin manifest and single-plugin marketplace manifest |
| `skills/afk/SKILL.md` | The contract; thin, calls `scripts/afk.py` |
| `hooks/hooks.json` | Stop, SessionStart (`compact\|resume\|startup`), UserPromptSubmit |
| `scripts/afk.py` | All logic. Stdlib only, Python 3.9+, single file, typed |
| `tests/test_afk.py` | pytest; direct calls plus subprocess runs of the hooks |
| `Plans/` | Implementation plans |

State lives in the project being worked on, not here: `.afk/run.json` (added to
`.git/info/exclude`, not the user's `.gitignore`) and `QUESTIONS.md` at the project root.

## Build and test

```bash
uv sync
make check          # ruff check + ruff format --check + pytest
make install-hooks  # enable .githooks/commit-msg
```

If the sandbox blocks uv's default cache, use `UV_CACHE_DIR=.uv-cache` (gitignored).

## Workflow rules

- Base branch is `main`. One concern per commit, with tests in the same commit.
- **No assistant attribution anywhere**: not in commits, PR bodies, code, docs or branch
  names. `.githooks/commit-msg` enforces the commit case.
- Skills stay thin; testable logic lives in `scripts/afk.py`, not in Markdown.
- Every hook fails open: any exception exits 0 with no output. Never let a bug trap a user.
- Hook stdout must be valid JSON per the Claude Code docs: Stop block is top-level
  `{"decision":"block","reason":...}`; SessionStart and UserPromptSubmit injection is
  `{"hookSpecificOutput":{"hookEventName":...,"additionalContext":...}}`.
- Never push to a remote from the tool's own logic.

## Standing decisions

- **No hook can read context usage** (only the statusline can). Context handling is:
  state is always on disk, built-in auto-compaction plus SessionStart re-injects the
  contract, and `/afk resume` picks the run up in a fresh session.
- **Stop hook** blocks only while the run is active and bound to the same session. It
  allows the stop on: no run, non-active status, session mismatch, deadline passed,
  `continues >= max_continues`, or 3 consecutive blocks with no progress (HEAD,
  QUESTIONS.md mtime/size and `units_done` all unchanged), which marks the run `blocked`.
  Claude Code itself ends the turn after 8 consecutive blocks with no tool call.
- **Typing anything pauses the run** (UserPromptSubmit), except `/afk ...` commands.
- **`disable-model-invocation: true`**: only the user starts an AFK run.
- **No built-in setting keeps the model working.** Auto permission mode only removes
  prompts; the skill recommends it but never changes settings.
- **`/afk` writes its own `handoff.md`** using an inlined compact template. It must not
  depend on any private skill.
- Run located by the hook's stdin `cwd`, walking up to the git root, else `cwd` itself.
- Non-goals: pushing, opening PRs, choosing work beyond the goal, changing permissions.

## Current state

- v0.1 shipped to `main`: `make check` green (46 tests), `claude plugin validate` passes,
  hook lifecycle driven by hand with the real `hooks.json` commands.
- Not yet verified: a live unattended session (Stop-hook continuation inside Claude Code)
  and `${CLAUDE_PLUGIN_ROOT}` expansion inside the skill's `allowed-tools`.
- Next: install via the marketplace, run `/afk` on a small real goal, fix what it finds.
