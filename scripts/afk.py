#!/usr/bin/env python3
"""cc-afk: state and hooks for the /afk skill.

Single file, stdlib only, Python 3.9+. State lives in ``<project>/.afk/run.json``.
Every hook subcommand fails open: any exception exits 0 with no output, so a bug
here can never trap the user in a session.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

Run = dict[str, Any]

STATE_DIR = ".afk"
RUN_FILE = "run.json"
QUESTIONS_FILE = "QUESTIONS.md"
STUCK_LIMIT = 3  # consecutive blocks without progress before the run is marked blocked
DEFAULT_MAX_CONTINUES = 40
DEFAULT_CHECKPOINT_EVERY = 3
GIT_TIMEOUT = 5
QUESTIONS_TAIL_LINES = 40
QUESTIONS_TAIL_CHARS = 3000

QUESTIONS_HEADER = """# QUESTIONS

Decisions queued by an unattended /afk run. Newest last. For each: the question, the
options, what was chosen, why, and how to undo it.
"""

SELF = Path(__file__).resolve()
CMD = f'python3 "{SELF}"'

PROMPT_IGNORE = re.compile(r"^\s*/(cc-afk:)?afk(\s|$)")


# ---------------------------------------------------------------------------
# Time and paths
# ---------------------------------------------------------------------------


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def find_root(cwd: str) -> Path:
    """Git root containing ``cwd`` if there is one, else ``cwd`` itself."""
    start = Path(cwd).resolve()
    for candidate in (start, *start.parents):
        if (candidate / ".git").exists():
            return candidate
    return start


def run_path(root: Path) -> Path:
    return root / STATE_DIR / RUN_FILE


def questions_path(root: Path) -> Path:
    return root / QUESTIONS_FILE


# ---------------------------------------------------------------------------
# Run state
# ---------------------------------------------------------------------------


def load_run(root: Path) -> Run | None:
    """Return the run, None if there is none. Raises ValueError if run.json is corrupt."""
    path = run_path(root)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"corrupt {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"corrupt {path}: not a JSON object")
    return data


def save_run(root: Path, run: Run) -> None:
    """Atomic write: temp file in the same directory, then os.replace."""
    path = run_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".run-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(run, handle, indent=2, sort_keys=True)
            handle.write("\n")
        Path(tmp).replace(path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def as_int(run: Run, key: str, default: int) -> int:
    value = run.get(key, default)
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def pause(run: Run, reason: str) -> None:
    run["status"] = "paused"
    run["paused_reason"] = reason


# ---------------------------------------------------------------------------
# Git and progress
# ---------------------------------------------------------------------------


def git(root: Path, *args: str) -> str | None:
    """Run git in ``root``. None on any failure, including a non-git directory."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def questions_signature(root: Path) -> str | None:
    try:
        stat = questions_path(root).stat()
    except OSError:
        return None
    return f"{stat.st_mtime_ns}:{stat.st_size}"


def progress_signature(root: Path, run: Run) -> list[Any]:
    """HEAD, QUESTIONS.md mtime/size, units_done. Any change counts as progress."""
    return [git(root, "rev-parse", "HEAD"), questions_signature(root), as_int(run, "units_done", 0)]


def ensure_git_exclude(root: Path) -> bool:
    """Add ``.afk/`` to the repo's local exclude file exactly once. False if not a git repo."""
    rel = git(root, "rev-parse", "--git-path", "info/exclude")
    if not rel:
        return False
    exclude = Path(rel)
    if not exclude.is_absolute():
        exclude = root / exclude
    existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    if any(line.strip() in (".afk", ".afk/", "/.afk", "/.afk/") for line in existing.splitlines()):
        return True
    exclude.parent.mkdir(parents=True, exist_ok=True)
    prefix = "" if not existing or existing.endswith("\n") else "\n"
    exclude.write_text(f"{existing}{prefix}.afk/\n", encoding="utf-8")
    return True


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------


def contract() -> str:
    return f"""AFK contract (unattended run; the user is away):
1. Never ask the user anything and never wait. When something is ambiguous, pick the \
reversible default, log it in QUESTIONS.md (question, options, choice, why, how to undo) \
and move on.
2. Work in units: one coherent change each. Run its tests, commit it with a plain message \
(stage only the files you changed, never .afk/), then run \
`{CMD} unit --summary "<one line>"`.
3. When `unit` prints CHECKPOINT DUE, rewrite the AGENTS.md "Current state" section in place \
(at most ~10 lines, no history) and the backlog status line, if the project has them.
4. Never: push or force-push, rewrite history, delete user data, change settings or \
permissions, spend money, send messages. Queue any of these in QUESTIONS.md instead.
5. If the next step needs one of those or a real human decision, finish all other work first. \
If nothing is left, run `{CMD} end --status blocked --reason "<why>"`.
6. When the goal is met and verified: run the full test suite, write handoff.md (template in \
the /afk skill), run `{CMD} end --status done --reason "<one line>"`, then give a short summary.
7. Keep tool output small. After compaction, trust .afk/run.json, git and QUESTIONS.md over \
memory."""


def questions_tail(root: Path) -> str:
    try:
        text = questions_path(root).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "(no QUESTIONS.md yet)"
    tail = "\n".join(text.splitlines()[-QUESTIONS_TAIL_LINES:])
    return tail[-QUESTIONS_TAIL_CHARS:]


def state_summary(run: Run) -> str:
    lines = [
        f"status: {run.get('status')}",
        f"goal: {run.get('goal')}",
        f"session: {run.get('session_id')}",
        f"started: {run.get('started_at')}",
        f"deadline: {run.get('deadline') or 'none'}",
        f"continues: {as_int(run, 'continues', 0)}/{as_int(run, 'max_continues', 0)}",
        f"units done: {as_int(run, 'units_done', 0)} "
        f"(checkpoint every {as_int(run, 'checkpoint_every', 0)})",
    ]
    for key, label in (
        ("last_summary", "last unit"),
        ("paused_reason", "paused because"),
        ("end_reason", "ended because"),
    ):
        if run.get(key):
            lines.append(f"{label}: {run[key]}")
    return "\n".join(lines)


def context_json(event: str, text: str) -> dict[str, Any]:
    return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}


# ---------------------------------------------------------------------------
# Hooks (return the JSON-serialisable output, or None for "say nothing")
# ---------------------------------------------------------------------------


def payload_root(payload: dict[str, Any]) -> Path:
    cwd = payload.get("cwd")
    return find_root(cwd if isinstance(cwd, str) and cwd else str(Path.cwd()))


def hook_stop(payload: dict[str, Any], now: datetime | None = None) -> dict[str, Any] | None:
    root = payload_root(payload)
    run = load_run(root)
    if run is None or run.get("status") != "active":
        return None
    if not run.get("session_id") or run.get("session_id") != payload.get("session_id"):
        return None
    now = now or utcnow()
    deadline = parse_time(run.get("deadline"))
    if deadline is not None and now >= deadline:
        pause(run, "deadline reached")
        save_run(root, run)
        return None
    continues = as_int(run, "continues", 0)
    max_continues = as_int(run, "max_continues", DEFAULT_MAX_CONTINUES)
    if continues >= max_continues:
        pause(run, "max continues reached")
        save_run(root, run)
        return None

    signature = progress_signature(root, run)
    stalled = as_int(run, "no_progress", 0) + 1 if run.get("last_sig") == signature else 0
    if stalled >= STUCK_LIMIT:
        run["status"] = "blocked"
        run["end_reason"] = (
            f"stuck: no commit, unit or QUESTIONS.md change across {STUCK_LIMIT} stops"
        )
        run["no_progress"] = stalled
        save_run(root, run)
        return None

    run["continues"] = continues + 1
    run["no_progress"] = stalled
    run["last_sig"] = signature
    run["last_head"] = signature[0]
    save_run(root, run)

    reason = (
        f"AFK run is active (stop {continues + 1}/{max_continues}). Goal: {run.get('goal')}. "
        "The user is away: do not ask questions or wait. Pick the next unit of work toward the "
        f'goal and do it (test, commit, then `{CMD} unit --summary "..."`). If the goal is met '
        f'and verified, finish with `{CMD} end --status done --reason "..."`; if only blocked '
        f'or human-decision work remains, `{CMD} end --status blocked --reason "..."`.'
    )
    if stalled:
        reason += (
            f" WARNING: no commit, unit or QUESTIONS.md change since the last stop "
            f"({stalled}/{STUCK_LIMIT}); the run will be marked blocked if this continues."
        )
    return {"decision": "block", "reason": reason}


def hook_session_start(payload: dict[str, Any]) -> dict[str, Any] | None:
    root = payload_root(payload)
    run = load_run(root)
    if run is None:
        return None
    status = run.get("status")
    mine = bool(run.get("session_id")) and run.get("session_id") == payload.get("session_id")
    if status == "active" and mine:
        text = (
            "An /afk run is ACTIVE in this session. Keep working unattended.\n\n"
            f"{contract()}\n\nRun state:\n{state_summary(run)}\n\n"
            f"QUESTIONS.md (tail):\n{questions_tail(root)}"
        )
        return context_json("SessionStart", text)
    if status in ("active", "paused"):
        text = (
            f"An /afk run is {status} in this project (goal: {run.get('goal')}). "
            "`/afk resume` to continue it."
        )
        return context_json("SessionStart", text)
    return None


def hook_prompt(payload: dict[str, Any]) -> dict[str, Any] | None:
    root = payload_root(payload)
    run = load_run(root)
    if run is None or run.get("status") != "active":
        return None
    if not run.get("session_id") or run.get("session_id") != payload.get("session_id"):
        return None
    prompt = payload.get("prompt")
    if isinstance(prompt, str) and PROMPT_IGNORE.match(prompt):
        return None
    pause(run, "user is back")
    save_run(root, run)
    text = (
        "The user is back, so the /afk run is now PAUSED. Answer their message. "
        "Briefly summarise progress (units done, last commit) and the open items in "
        "QUESTIONS.md. `/afk resume` continues the run."
    )
    return context_json("UserPromptSubmit", text)


def run_hook(name: str) -> int:
    """Always returns 0; any failure is silent."""
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            return 0
        handlers = {"stop": hook_stop, "session-start": hook_session_start, "prompt": hook_prompt}
        handler = handlers.get(name)
        if handler is None:
            return 0
        out = handler(payload)
        if out is not None:
            sys.stdout.write(json.dumps(out) + "\n")
    except Exception:
        return 0
    return 0


# ---------------------------------------------------------------------------
# CLI subcommands
# ---------------------------------------------------------------------------


def fail(message: str) -> int:
    print(f"afk: {message}", file=sys.stderr)
    return 1


def current_root() -> Path:
    return find_root(str(Path.cwd()))


def cmd_start(args: argparse.Namespace) -> int:
    root = current_root()
    now = utcnow()
    previous = None
    with contextlib.suppress(ValueError):
        previous = load_run(root)
    run: Run = {
        "status": "active",
        "goal": args.goal,
        "session_id": args.session,
        "started_at": now.isoformat(timespec="seconds"),
        "deadline": (now + timedelta(hours=args.hours)).isoformat(timespec="seconds")
        if args.hours
        else None,
        "max_continues": args.max_continues,
        "continues": 0,
        "units_done": 0,
        "checkpoint_every": args.checkpoint_every,
        "last_head": None,
        "last_sig": None,
        "no_progress": 0,
    }
    save_run(root, run)
    in_git = ensure_git_exclude(root)
    questions = questions_path(root)
    if not questions.exists():
        questions.write_text(QUESTIONS_HEADER, encoding="utf-8")
    if previous is not None:
        print(f"(replaced a previous run with status {previous.get('status')})")
    print(f"AFK run started in {root}" + ("" if in_git else " (not a git repo)"))
    print(state_summary(run))
    print()
    print(contract())
    print("\nBegin the first unit of work now.")
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    root = current_root()
    try:
        run = load_run(root)
    except ValueError as exc:
        return fail(str(exc))
    if run is None:
        return fail("no run to resume here; start one with /afk <goal>")
    if run.get("status") == "done":
        return fail("the last run is done; start a new one with /afk <goal>")
    now = utcnow()
    note = ""
    if args.hours:
        run["deadline"] = (now + timedelta(hours=args.hours)).isoformat(timespec="seconds")
    else:
        deadline = parse_time(run.get("deadline"))
        if deadline is not None and now >= deadline:
            run["deadline"] = None
            note = "(the old deadline had passed and was cleared)\n"
    run.update(status="active", session_id=args.session, continues=0, no_progress=0, last_sig=None)
    run.pop("paused_reason", None)
    run.pop("end_reason", None)
    save_run(root, run)
    ensure_git_exclude(root)
    print(f"AFK run resumed in {root}\n{note}{state_summary(run)}\n")
    print(contract())
    print(f"\nQUESTIONS.md (tail):\n{questions_tail(root)}")
    print(
        "\nRe-read AGENTS.md (Current state), handoff.md if present, and `git log -5`, "
        "then continue with the next unit."
    )
    return 0


def cmd_unit(args: argparse.Namespace) -> int:
    root = current_root()
    try:
        run = load_run(root)
    except ValueError as exc:
        return fail(str(exc))
    if run is None:
        return fail("no AFK run in this project")
    run["units_done"] = as_int(run, "units_done", 0) + 1
    run["last_summary"] = args.summary
    save_run(root, run)
    every = max(as_int(run, "checkpoint_every", DEFAULT_CHECKPOINT_EVERY), 1)
    print(f"unit {run['units_done']} recorded: {args.summary}")
    if run["units_done"] % every == 0:
        print("CHECKPOINT DUE: rewrite the AGENTS.md Current state section (and backlog status).")
    return 0


def cmd_end(args: argparse.Namespace) -> int:
    root = current_root()
    try:
        run = load_run(root)
    except ValueError as exc:
        return fail(str(exc))
    if run is None:
        return fail("no AFK run in this project")
    run["status"] = args.status
    run["end_reason"] = args.reason
    run["ended_at"] = utcnow().isoformat(timespec="seconds")
    save_run(root, run)
    print(f"AFK run {args.status}: {args.reason}")
    return 0


def cmd_stop(_args: argparse.Namespace) -> int:
    root = current_root()
    try:
        run = load_run(root)
    except ValueError as exc:
        return fail(str(exc))
    if run is None:
        print("No AFK run in this project.")
        return 0
    pause(run, "stopped by user")
    save_run(root, run)
    print("AFK run paused. `/afk resume` continues it.")
    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    root = current_root()
    try:
        run = load_run(root)
    except ValueError as exc:
        return fail(str(exc))
    if run is None:
        print("No AFK run in this project.")
        return 0
    print(state_summary(run))
    return 0


def positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be >= 1")
    return number


def positive_float(value: str) -> float:
    number = float(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be > 0")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="afk.py", description="State helper for /afk.")
    sub = parser.add_subparsers(dest="command", required=True)

    start = sub.add_parser("start", help="begin a run")
    start.add_argument("--goal", required=True)
    start.add_argument("--session", required=True)
    start.add_argument("--hours", type=positive_float)
    start.add_argument("--max-continues", type=positive_int, default=DEFAULT_MAX_CONTINUES)
    start.add_argument("--checkpoint-every", type=positive_int, default=DEFAULT_CHECKPOINT_EVERY)
    start.set_defaults(func=cmd_start)

    resume = sub.add_parser("resume", help="rebind the run to this session")
    resume.add_argument("--session", required=True)
    resume.add_argument("--hours", type=positive_float)
    resume.set_defaults(func=cmd_resume)

    unit = sub.add_parser("unit", help="record a committed unit of work")
    unit.add_argument("--summary", required=True)
    unit.set_defaults(func=cmd_unit)

    end = sub.add_parser("end", help="end the run")
    end.add_argument("--status", required=True, choices=["done", "blocked"])
    end.add_argument("--reason", required=True)
    end.set_defaults(func=cmd_end)

    sub.add_parser("stop", help="pause the run").set_defaults(func=cmd_stop)
    sub.add_parser("status", help="print the run state").set_defaults(func=cmd_status)
    return parser


def main(argv: list[str] | None = None) -> int:
    args_in = sys.argv[1:] if argv is None else argv
    if args_in and args_in[0] == "hook":
        return run_hook(args_in[1] if len(args_in) > 1 else "")
    args = build_parser().parse_args(args_in)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
