from __future__ import annotations

import json
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

import afk

SCRIPT = Path(afk.__file__).resolve()
SESSION = "sess-1"


def git(cwd: Path, *args: str) -> str:
    out = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True)
    return out.stdout.strip()


def commit(repo: Path, name: str) -> None:
    (repo / name).write_text(name)
    git(repo, "add", name)
    git(repo, "commit", "-q", "-m", name)


@pytest.fixture(autouse=True)
def git_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        monkeypatch.setenv(var, "t")
    for var in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        monkeypatch.setenv(var, "t@example.com")


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    git(tmp_path, "init", "-q")
    commit(tmp_path, "seed")
    monkeypatch.chdir(tmp_path)
    return tmp_path.resolve()


def start(*extra: str, session: str = SESSION) -> None:
    code = afk.main(["start", "--goal", "ship it", "--session", session, *extra])
    assert code == 0


def run_json(root: Path) -> dict[str, Any]:
    return json.loads((root / ".afk" / "run.json").read_text())


def edit_run(root: Path, **changes: Any) -> None:
    run = run_json(root)
    run.update(changes)
    afk.save_run(root, run)


def payload(root: Path, session: str = SESSION, **extra: Any) -> dict[str, Any]:
    return {"session_id": session, "cwd": str(root), **extra}


def hook_subprocess(name: str, stdin: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "hook", name],
        input=stdin,
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=30,
        check=False,
    )


# --- Stop hook: allows the stop -------------------------------------------------------------


def test_stop_allows_when_no_run(repo: Path) -> None:
    assert afk.hook_stop(payload(repo)) is None


@pytest.mark.parametrize("status", ["paused", "done", "blocked"])
def test_stop_allows_when_not_active(repo: Path, status: str) -> None:
    start()
    edit_run(repo, status=status)
    assert afk.hook_stop(payload(repo)) is None


def test_stop_allows_for_a_different_session(repo: Path) -> None:
    start()
    assert afk.hook_stop(payload(repo, session="other")) is None
    assert run_json(repo)["continues"] == 0


def test_stop_allows_after_deadline(repo: Path) -> None:
    start("--hours", "1")
    later = afk.utcnow() + timedelta(hours=2)
    assert afk.hook_stop(payload(repo), now=later) is None
    assert run_json(repo)["status"] == "paused"


def test_stop_allows_at_max_continues(repo: Path) -> None:
    start("--max-continues", "2")
    edit_run(repo, continues=2)
    assert afk.hook_stop(payload(repo)) is None
    assert run_json(repo)["status"] == "paused"


# --- Stop hook: blocks ----------------------------------------------------------------------


def test_stop_blocks_while_progressing_and_counts(repo: Path) -> None:
    start()
    first = afk.hook_stop(payload(repo))
    assert first is not None
    assert first["decision"] == "block"
    assert "ship it" in first["reason"]
    assert run_json(repo)["continues"] == 1

    commit(repo, "work-1")  # progress between stops
    assert afk.hook_stop(payload(repo)) is not None
    run = run_json(repo)
    assert (run["continues"], run["no_progress"], run["status"]) == (2, 0, "active")


def test_stop_blocks_via_subprocess_with_valid_json(repo: Path) -> None:
    start()
    proc = hook_subprocess("stop", json.dumps(payload(repo)), repo)
    assert proc.returncode == 0
    out = json.loads(proc.stdout)
    assert out["decision"] == "block"
    assert isinstance(out["reason"], str) and out["reason"]


def test_stuck_run_is_marked_blocked_after_three_blocks(repo: Path) -> None:
    start()
    for _ in range(3):
        assert afk.hook_stop(payload(repo)) is not None
    assert afk.hook_stop(payload(repo)) is None
    run = run_json(repo)
    assert run["status"] == "blocked"
    assert "stuck" in run["end_reason"]
    assert run["continues"] == 3


def test_recording_a_unit_counts_as_progress(repo: Path) -> None:
    start()
    for i in range(5):
        assert afk.hook_stop(payload(repo)) is not None
        afk.main(["unit", "--summary", f"u{i}"])
    assert run_json(repo)["status"] == "active"


def test_non_git_dir_progress_uses_questions_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    root = tmp_path.resolve()
    start()
    assert not (root / ".git").exists()
    assert afk.hook_stop(payload(root)) is not None
    (root / "QUESTIONS.md").write_text("- q1\n" * 5)  # changes size/mtime: progress
    assert afk.hook_stop(payload(root)) is not None
    assert run_json(root)["no_progress"] == 0
    for _ in range(2):
        assert afk.hook_stop(payload(root)) is not None
    assert afk.hook_stop(payload(root)) is None
    assert run_json(root)["status"] == "blocked"


def test_hook_finds_root_from_subdirectory(repo: Path) -> None:
    start()
    sub = repo / "pkg" / "deep"
    sub.mkdir(parents=True)
    assert afk.hook_stop(payload(sub)) is not None


# --- Fail open ------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["stop", "session-start", "prompt", "bogus"])
@pytest.mark.parametrize("stdin", ["", "not json", "[1, 2]", "null"])
def test_hooks_ignore_malformed_stdin(repo: Path, name: str, stdin: str) -> None:
    start()
    proc = hook_subprocess(name, stdin, repo)
    assert (proc.returncode, proc.stdout) == (0, "")


@pytest.mark.parametrize("name", ["stop", "session-start", "prompt"])
def test_hooks_ignore_corrupt_run_json(repo: Path, name: str) -> None:
    start()
    (repo / ".afk" / "run.json").write_text("{not json")
    proc = hook_subprocess(name, json.dumps(payload(repo, prompt="hi", source="compact")), repo)
    assert (proc.returncode, proc.stdout) == (0, "")


# --- SessionStart ---------------------------------------------------------------------------


def test_session_start_compact_injects_goal_and_questions_tail(repo: Path) -> None:
    start()
    (repo / "QUESTIONS.md").write_text("# QUESTIONS\n\n- Q: tabs or spaces? chose spaces\n")
    out = afk.hook_session_start(payload(repo, source="compact"))
    assert out is not None
    special = out["hookSpecificOutput"]
    assert special["hookEventName"] == "SessionStart"
    text = special["additionalContext"]
    assert "ship it" in text
    assert "tabs or spaces" in text
    assert "AFK contract" in text


def test_session_start_foreign_session_gets_only_a_note(repo: Path) -> None:
    start()
    out = afk.hook_session_start(payload(repo, session="other", source="startup"))
    assert out is not None
    text = out["hookSpecificOutput"]["additionalContext"]
    assert "/afk resume" in text
    assert "AFK contract" not in text
    assert len(text.splitlines()) == 1


def test_session_start_silent_without_a_live_run(repo: Path) -> None:
    assert afk.hook_session_start(payload(repo, source="startup")) is None
    start()
    edit_run(repo, status="done")
    assert afk.hook_session_start(payload(repo, source="startup")) is None


# --- UserPromptSubmit -----------------------------------------------------------------------


def test_prompt_pauses_run_on_normal_input(repo: Path) -> None:
    start()
    out = afk.hook_prompt(payload(repo, prompt="hey, how is it going?"))
    assert out is not None
    assert out["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    assert "QUESTIONS.md" in out["hookSpecificOutput"]["additionalContext"]
    run = run_json(repo)
    assert (run["status"], run["paused_reason"]) == ("paused", "user is back")
    assert afk.hook_stop(payload(repo)) is None  # a paused run no longer blocks


@pytest.mark.parametrize("prompt", ["/afk status", "/afk", "/cc-afk:afk stop", "  /afk resume"])
def test_prompt_ignores_afk_commands(repo: Path, prompt: str) -> None:
    start()
    assert afk.hook_prompt(payload(repo, prompt=prompt)) is None
    assert run_json(repo)["status"] == "active"


def test_prompt_from_another_session_does_not_pause(repo: Path) -> None:
    start()
    assert afk.hook_prompt(payload(repo, session="other", prompt="hello")) is None
    assert run_json(repo)["status"] == "active"


# --- CLI ------------------------------------------------------------------------------------


def test_unit_flags_checkpoint_every_n(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    start("--checkpoint-every", "3")
    capsys.readouterr()
    due = []
    for i in range(6):
        afk.main(["unit", "--summary", f"u{i}"])
        due.append("CHECKPOINT DUE" in capsys.readouterr().out)
    assert due == [False, False, True, False, False, True]
    assert run_json(repo)["units_done"] == 6


def test_start_writes_git_exclude_once_and_creates_questions(repo: Path) -> None:
    start()
    start()
    exclude = (repo / ".git" / "info" / "exclude").read_text().splitlines()
    assert exclude.count(".afk/") == 1
    assert (repo / "QUESTIONS.md").read_text().startswith("# QUESTIONS")
    assert ".afk" not in git(repo, "status", "--porcelain").replace("QUESTIONS.md", "")


def test_start_keeps_existing_questions_file(repo: Path) -> None:
    (repo / "QUESTIONS.md").write_text("keep me\n")
    start()
    assert (repo / "QUESTIONS.md").read_text() == "keep me\n"


def test_resume_rebinds_session_and_resets_counters(repo: Path) -> None:
    start()
    for _ in range(3):
        afk.hook_stop(payload(repo))
    assert afk.hook_stop(payload(repo)) is None  # now blocked
    assert afk.main(["resume", "--session", "new-sess"]) == 0
    run = run_json(repo)
    assert (run["status"], run["session_id"], run["continues"]) == ("active", "new-sess", 0)
    assert afk.hook_stop(payload(repo, session="new-sess")) is not None


def test_end_and_stop_set_terminal_states(repo: Path) -> None:
    start()
    assert afk.main(["stop"]) == 0
    assert run_json(repo)["status"] == "paused"
    assert afk.main(["end", "--status", "done", "--reason", "all green"]) == 0
    run = run_json(repo)
    assert (run["status"], run["end_reason"]) == ("done", "all green")
    assert afk.main(["resume", "--session", "s2"]) == 1  # a done run is not resumable
