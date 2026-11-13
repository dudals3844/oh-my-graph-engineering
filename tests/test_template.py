"""Shipped templates load, and every check renders to valid bash."""

from __future__ import annotations

import re
import subprocess
import sys

import pytest

from omge import checks, graph, library, store
from tests.conftest import ISSUE

PARAMS = {
    "pr": "12",
    "complexity": "simple",
    "test_scope": "tests/unit tests/integration",
    "notes": "/tmp/wf-notes.md",
    "plan": "/tmp/plan.md",
    "test_cmd": "pytest -q",
    "review": "/tmp/review.md",
}


@pytest.mark.parametrize("path", sorted(library.WORKFLOWS.glob("*.yaml")), ids=lambda p: p.stem)
def test_template_loads_and_shells_are_valid_bash(path):
    text = path.read_text(encoding="utf-8")
    # Strict: a seed is saved back by update_type, which lints it like a submission.
    t = graph.parse(text, strict=True)
    # In a plain scalar ` #` starts a comment and silently truncates the value.
    assert not re.findall(r"^\s*-?\s*\w+: [^'\"|>\s][^\n]* #", text, re.M)
    assert t["type"] == path.stem
    for node in t["nodes"]:
        for shell in [node.get("check"), *(e.get("when") for e in node["next"])]:
            if shell:
                cmd = checks.render(shell, PARAMS, t["params"])
                subprocess.run(["bash", "-n", "-c", cmd], check=True)


def test_every_shipped_template_is_listed_and_startable(tmp_path):
    """The library seeds every packaged template, wf_list_types shows it, and it starts."""
    conn = store.connect()
    listed = library.listing(conn)
    for path in sorted(library.WORKFLOWS.glob("*.yaml")):
        assert f"- {path.stem}: " in listed
        t = graph.parse(path.read_text(encoding="utf-8"))
        issue = None if t.get("optional_issue") or not any(
            "WF_ISSUE" in str(n.get("check")) for n in t["nodes"] if n["id"] != "retro") else ISSUE
        tid = graph.start(conn, path.stem, str(tmp_path), issue, {},
                          title=None if issue else path.stem)
        task = store.get_task(conn, tid)
        assert task["type"] == path.stem and task["state"] == "active"
        assert graph.ready(t, store.node_rows(conn, tid))


def test_code_merging_types_review_before_pr_and_retro_after_cleanup():
    """Review the tested implementation before PR/CI, preserving the cleanup gate."""
    t = graph.parse((library.WORKFLOWS / "issue-resolution.yaml").read_text(), strict=True)
    nodes = {n["id"]: n for n in t["nodes"]}
    assert nodes["review"]["after"] == ["test"]
    assert nodes["pr"]["after"] == ["review"]
    assert nodes["review"]["carry"] == "code"
    assert nodes["ci"]["after"] == ["pr"]
    assert nodes["merge"]["after"] == ["ci"]
    assert nodes["retro"]["after"] == ["cleanup"]
    assert "git ls-remote --exit-code --heads origin" in nodes["cleanup"]["check"]


def _git(path, *args):
    subprocess.run(["git", "-C", str(path), *args], check=True)


@pytest.fixture
def issue_test_checkout(tmp_path):
    """Execute the shipped shell against real pytest/Ruff and a committed diff."""
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / ".gitignore").write_text(".venv\n__pycache__/\n.ruff_cache/\n")
    for directory in ("tests/first", "tests/second", "unselected"):
        path = tmp_path / directory
        path.mkdir(parents=True)
        (path / f"test_{path.name}.py").write_text(
            f"def test_example():\n    assert {directory != 'unselected'}\n")
    (tmp_path / "old_debt.py").write_text("import os\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "base")
    _git(tmp_path, "update-ref", "refs/remotes/origin/main", "HEAD")
    (tmp_path / ".venv").symlink_to(sys.prefix, target_is_directory=True)
    return tmp_path


def _issue_test_command(scope):
    t = graph.parse((library.WORKFLOWS / "issue-resolution.yaml").read_text(), strict=True)
    node = next(n for n in t["nodes"] if n["id"] == "test")
    assert node["timeout"] == 900
    return checks.render(node["check"], {"test_scope": scope}, t["params"])


@pytest.mark.parametrize(("scope", "ok"), [
    ("tests/first tests/second", True), ("tests/first missing", False),
    ("tests/first unselected", False),
])
def test_issue_scoped_pytest_executes_path_argv_and_preserves_failure(
        issue_test_checkout, scope, ok):
    cmd = _issue_test_command(scope)
    code, output = checks.run(cmd, str(issue_test_checkout), {}, 30)
    assert (code == 0) is ok, output
    if ok:
        assert "2 passed" in output


@pytest.mark.parametrize("scope", [
    "", ".", "../tests", "tests/../outside", "/tmp/tests", "-q", "tests -k pass",
    "tests; true", "tests\ntrue", "$(touch injected)", "tests/*.py", "tests && true",
])
def test_issue_test_scope_rejects_commands_options_and_traversal(scope):
    with pytest.raises(graph.WfError):
        _issue_test_command(scope)


@pytest.mark.parametrize(("case", "ok"), [
    ("valid", True), ("lint_error", False), ("deleted", True),
    ("no_config", True), ("bad_base", False),
])
def test_issue_ruff_checks_only_configured_changed_surviving_files(issue_test_checkout, case, ok):
    path = issue_test_checkout
    if case != "no_config":
        (path / "ruff.toml").write_text('lint.select = ["F"]\n')
    if case == "deleted":
        (path / "old_debt.py").unlink()
    else:
        (path / "changed.py").write_text("x = 1\n" if case == "valid" else "import os\n")
    _git(path, "add", ".")
    _git(path, "commit", "-q", "-m", "change")
    if case == "bad_base":
        _git(path, "update-ref", "-d", "refs/remotes/origin/main")
    code, output = checks.run(_issue_test_command("tests/first tests/second"), str(path), {}, 30)
    assert (code == 0) is ok, output
    if case == "lint_error":
        assert "F401" in output and "old_debt.py" not in output
