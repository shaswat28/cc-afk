# cc-afk

**Set the "I'm away, keep working" contract once.** A [Claude Code](https://claude.com/claude-code)
plugin with one skill, `/afk`.

After `/afk <goal>` the session works unattended. It does not ask blocking questions.
It queues decisions in `QUESTIONS.md`, commits once per unit of work, and checkpoints to
`AGENTS.md`. It keeps going after compaction, and it stops cleanly when the work is done,
it is blocked, or you come back.

## Install

```
/plugin marketplace add shaswat28/cc-afk
/plugin install cc-afk@cc-afk
```

Tip: switch to auto permission mode before you leave, so the run is not stalled by
permission prompts. The plugin never changes your settings.

## Usage

```
/afk fix the failing parser tests and get the suite green --hours 3
/afk status
/afk stop
/afk resume
```

Flags (all optional): `--hours N` sets a deadline, `--max-continues N` caps how many
times the Stop hook may push the session onward (default 40), and
`--checkpoint-every N` sets how many committed units pass between `AGENTS.md`
checkpoints (default 3).

The contract the session follows:

1. Never ask, never wait; log the reversible default in `QUESTIONS.md` and move on.
2. Work in units: tests, a plain commit, then record the unit.
3. On every Nth unit, rewrite the "Current state" section of `AGENTS.md` in place.
4. Never push, rewrite history, delete user data, change settings, spend money or send
   messages; queue those in `QUESTIONS.md` instead.
5. If only blocked or human-decision work remains, finish the rest, then end as `blocked`.
6. When the goal is met: full test suite, write `handoff.md`, end as `done`, summarise.
7. Keep tool output small; after compaction trust `.afk/run.json`, git and `QUESTIONS.md`.

## How stopping works

- **`/afk stop`** pauses the run.
- **Typing anything** (other than an `/afk` command) pauses it: you are back, so the
  session answers you and summarises progress and open questions. `/afk resume` continues.
- **Deadline** (`--hours`) passed, or **max continues** reached: the Stop hook lets the
  session end and the run is marked paused.
- **Stuck guard**: three consecutive stops with no new commit, no recorded unit and no
  change to `QUESTIONS.md` mark the run `blocked` and let the session end.
- **Done or blocked**: the session calls `end` itself.

A Stop hook only acts for the session that started the run. Every hook fails open: if
anything goes wrong inside the plugin, it exits silently and your session is untouched.

## Files it writes

In the project being worked on, not in this plugin:

- `.afk/run.json`: run state (status, goal, counters, deadline). `/afk` adds `.afk/` to
  `.git/info/exclude`, so your `.gitignore` is untouched.
- `QUESTIONS.md`: queued decisions (question, options, choice, why, how to undo). Created
  with a header if missing; not committed unless your project already tracks it.
- `handoff.md`: written when the run finishes, for the next session.

## Limitations

- No hook can read context usage (only the statusline can), so there is no "hand off
  when context runs low". The plugin relies on built-in auto-compaction: state is always
  on disk, a SessionStart hook re-injects the contract after compaction, and
  `/afk resume` picks the run back up in a fresh session.
- Claude Code ends a turn anyway after 8 consecutive Stop-hook blocks with no tool call
  in between, and Stop does not fire on user interrupts.
- Progress is judged from git HEAD, `QUESTIONS.md` and recorded units. In a directory
  that is not a git repo, only the last two count.

## Development

```bash
uv sync
make check   # ruff + format check + pytest
```

Python 3.9+, standard library only; see `AGENTS.md`.

## License

AGPL-3.0, see [LICENSE](LICENSE).
