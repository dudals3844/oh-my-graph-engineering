"""`omge hook <event>` and `omge status` — the entry the settings.json hooks run."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from omge import cli, store

from .conftest import ISSUE, start


def test_hook_reads_stdin_and_stop_blocks(workflows, repo, monkeypatch, capsys):
    start(repo)
    store.register(store.connect(), "s")
    own = {"cwd": repo, "session_id": "s", "tool_name": "mcp__wf__wf_attach",
           "tool_input": {"issue_url": ISSUE}}
    for payload, event in ((own, "pre-tool-use"), ({"cwd": repo, "session_id": "s"}, "stop")):
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
        code = cli.main(["hook", event])
    assert code == 2
    assert "`a` exit=1" in capsys.readouterr().err


def test_status(workflows, repo, capsys, monkeypatch):
    assert cli.main(["status"]) == 0
    assert "no open tasks" in capsys.readouterr().out
    start(repo)
    monkeypatch.chdir(repo)
    cli.main(["status"])
    out = capsys.readouterr().out
    assert "ready `a`" in out and "[▶ a]" not in out and "no other open tasks" in out
    cli.main(["status", "--all"])
    assert "[▶ a]" in capsys.readouterr().out
    sub = Path(repo) / "pkg" / "src"
    sub.mkdir(parents=True)
    monkeypatch.chdir(sub)
    cli.main(["status"])
    nested = capsys.readouterr().out
    assert "ready `a`" in nested and "no other open tasks" in nested
    other = Path(repo) / "other-tree"
    other.mkdir()
    import subprocess
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", str(other), "HEAD"],
                   check=True)
    monkeypatch.chdir(other)
    cli.main(["status"])
    away = capsys.readouterr().out
    assert "no open task in this directory" in away and "other directories: 1" in away
    assert "ready `a`" not in away
    # the task dir is a subdirectory of the checkout, not the git root
    nested_task = Path(repo) / "pkg"
    start(str(nested_task), issue=ISSUE.replace("/1", "/2"))
    monkeypatch.chdir(nested_task / "src")
    cli.main(["status"])
    under = capsys.readouterr().out
    assert under.count("ready `a`") == 2 and "no other open tasks" in under
    monkeypatch.chdir(repo)
    cli.main(["status"])
    above = capsys.readouterr().out
    assert above.count("ready `a`") == 1 and "open tasks in other directories: 1" in above


@pytest.mark.parametrize("argv, fragments", [
    (["--help"], ("omge status", "omge-mcp", "wf_start", "wf_complete", "wf_fail", "wf_retro",
                  "Skill tool", "HEAD", "Plan Mode", "first line", "7/40 chars", "PLAN done")),
    (["status", "--help"], ("current directory", "--all", "never starts or completes")),
    (["hook", "--help"], ("JSON stdin", "starting work", "post-tool-use")),
])
def test_help_explains_usage_without_opening_db(argv, fragments, monkeypatch, capsys):
    monkeypatch.setattr(store, "connect", lambda: pytest.fail("help must not open the DB"))
    with pytest.raises(SystemExit) as exc:
        cli.main(argv)
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert all(fragment in out for fragment in fragments)
    assert "omge start" not in out and "omge complete" not in out
