"""A `skill:` node closes only on a recorded Skill call made after it became ready,
and its check sees that call's time as WF_SKILL_AT."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from omge import checks, graph, hooks, library, store

from .conftest import ISSUE, close, run_hook, start, statuses

SKILLED = """
type: skilled
match: skill gate
nodes:
  - {id: a, check: 'test -f a.ok', on_fail: retry}
  - id: r
    after: [a]
    skill: demo:review
    on_fail: retry
    check: '[[ "$WF_SKILL_AT" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:]{8}Z$ ]]'
  - {id: retro, after: [r]}
"""


@pytest.fixture
def tid(workflows, repo):
    (workflows / "skilled.yaml").write_text(SKILLED)
    return start(repo, type_="skilled")


def _hook(event: str, cwd: str, **payload) -> None:
    store.register(store.connect(), "s1")
    assert run_hook(event, {"cwd": cwd, "session_id": "s1", **payload}) == 0


def _skill(repo: str, name: str, **payload) -> None:
    """The session owns the task, then calls the Skill tool."""
    _hook("pre-tool-use", repo, tool_name="mcp__wf__wf_attach", tool_input={"issue_url": ISSUE})
    _hook("post-tool-use", repo, tool_name="Skill", tool_input={"skill": name}, **payload)


def _complete(tid: int, node: str = "r") -> str:
    conn = store.connect()
    return checks.complete(conn, store.get_task(conn, tid), node, "ev")


def _status(tid: int) -> str:
    conn = store.connect()
    return graph.status_text(conn, store.get_task(conn, tid))


def test_a_skill_node_needs_the_call_after_it_became_ready(tid, repo):
    conn = store.connect()
    store.event(conn, tid, "r", "skill", "demo:review")  # before `a` closed: does not count
    Path(repo, "a.ok").touch()
    _complete(tid, "a")  # a node without `skill` closes as before
    assert "skill: demo:review (not called)" in _status(tid)
    with pytest.raises(graph.WfError, match="no record of a call to skill `demo:review`"):
        _complete(tid)
    _skill(repo, "other:skill")
    with pytest.raises(graph.WfError, match="no record of a call"):
        _complete(tid)
    # A call no ready node names is not recorded at all.
    assert not conn.execute("SELECT 1 FROM event WHERE detail LIKE 'other:%'").fetchall()
    _skill(repo, "demo:review", agent_id="sub1")  # a subagent's call, under the parent session
    rows = conn.execute("SELECT detail FROM event WHERE kind = 'skill' ORDER BY rowid").fetchall()
    assert [r[0] for r in rows][-1] == "demo:review (agent)"
    assert "skill: demo:review (called)" in _status(tid)
    assert _complete(tid) == "`r` done"  # its check saw a well-formed WF_SKILL_AT


def test_a_loop_back_needs_a_new_call(tid, repo):
    Path(repo, "a.ok").touch()
    close(tid, "a")
    _skill(repo, "demo:review")
    conn = store.connect()
    checks._go_back(conn, graph.parse(store.get_task(conn, tid)["graph"]), tid, "r", "a")
    close(tid, "a")
    with pytest.raises(graph.WfError, match="no record of a call"):
        _complete(tid)


@pytest.mark.parametrize(("seed", "node_id", "prefix"), [
    ("issue-resolution", "review", "review"),
    ("issue-resolution", "review", "ReViEw"),
])
@pytest.mark.parametrize(("header", "ok"), [
    ("{head}", True),
    ("{short}", True),
    ("{stale}\nwarning: the current HEAD is {head}", False),
    ("{wrong_full}", False),
    ("{longer}", False),
    ("{partial}\ncurrent HEAD: {head}", False),
    ("pending\ncurrent HEAD: {head}", False),
])
def test_seed_review_binds_head_to_first_line(seed, node_id, prefix, header, ok, repo, tmp_path):
    t = graph.parse((library.WORKFLOWS / f"{seed}.yaml").read_text(), strict=True)
    node = next(n for n in t["nodes"] if n["id"] == node_id)
    head = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"], capture_output=True,
                          text=True, check=True).stdout.strip()
    stale = ("0" if head[0] != "0" else "1") + head[1:]
    wrong_full = head[:7] + ("0" if head[7] != "0" else "1") + head[8:]
    # An 8-hex prefix that differs from HEAD only in its last digit: `{short}a` was a
    # 1-in-16 flake, since a random sha's 8th digit may be that very letter.
    longer = head[:7] + ("0" if head[7] != "0" else "1")
    body = f"{prefix} " + header.format(head=head, short=head[:7], stale=stale,
                                      wrong_full=wrong_full, longer=longer,
                                      partial=head[:6]) + "\nVerdict: pass"
    env = {"PATH": _gh(tmp_path, "2026-10-10T12:00:05Z", body), "WF_ISSUE": ISSUE,
           "WF_SKILL_AT": "2026-10-10T12:00:00Z"}
    assert (checks.run(node["check"], repo, env, 30)[0] == 0) is ok


def _gh(tmp_path: Path, created: str, body: str, extra: tuple[str, str] | None = None) -> str:
    """A PATH whose `gh api ... --jq F` is jq F over stub issue comments."""
    rows = [{"id": 7, "created_at": created, "body": body}]
    if extra:
        rows.append({"id": 8, "created_at": extra[0], "body": extra[1]})
    comments = tmp_path / "comments.json"
    comments.write_text(json.dumps(rows))
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    gh = bindir / "gh"
    gh.write_text('#!/bin/bash\nwhile [ $# -gt 0 ]; do [ "$1" = --jq ] && exec jq -r "$2"'
                  f' {comments}; shift; done\nexit 1\n')
    gh.chmod(0o755)
    return f"{bindir}:{os.environ['PATH']}"


@pytest.mark.parametrize("skill", ["", "    skill: [demo:review, demo:easy]\n"])
def test_revise_cannot_drop_or_change_a_pending_skill(tid, skill):
    conn = store.connect()
    with pytest.raises(graph.WfError, match="cannot change or remove the skill"):
        graph.revise(conn, store.get_task(conn, tid),
                     SKILLED.replace("    skill: demo:review\n", skill), "skill change")


def test_any_listed_skill_opens_the_gate(workflows, repo):
    """`skill:` may list alternatives; one call of any of them will do."""
    (workflows / "skilled.yaml").write_text(
        SKILLED.replace("skill: demo:review", "skill: [demo:review, demo:other]"))
    tid = start(repo, type_="skilled")
    Path(repo, "a.ok").touch()
    close(tid, "a")
    assert "skill: demo:review | demo:other (not called)" in _status(tid)
    _skill(repo, "demo:other")
    assert "skill: demo:review | demo:other (called)" in _status(tid)
    assert _complete(tid) == "`r` done"


@pytest.mark.parametrize("bad", ["[]", "[demo:a, 3]", "{a: b}", "''"])
def test_a_skill_is_a_name_or_a_non_empty_list_of_names(bad):
    with pytest.raises(graph.WfError, match="is a skill name string or a list of them"):
        graph.parse(SKILLED.replace("skill: demo:review", f"skill: {bad}"))


def _seeded(repo: str, complexity: str) -> int:
    """An issue-resolution task past `scope_review`, classified `complexity`."""
    tid = start(repo, type_="issue-resolution")
    conn = store.connect()
    graph.set_param(conn, store.get_task(conn, tid), "complexity", complexity)
    close(tid, "claim", "spec", "scope_review")
    return tid


def _ready(tid: int) -> list[str]:
    conn = store.connect()
    task = store.get_task(conn, tid)
    return [n["id"] for n in graph.ready(graph.parse(task["graph"]), store.node_rows(conn, tid))]


def test_simple_goes_straight_to_implement(repo):
    tid = _seeded(repo, "simple")
    assert statuses(tid)["plan"] == "skipped" and _ready(tid) == ["implement"]


def test_complex_plan_closes_on_a_receipt_after_the_scope_pass(repo, tmp_path, monkeypatch):
    tid = _seeded(repo, "complex")
    assert _ready(tid) == ["plan"]
    monkeypatch.setenv("PATH", _gh(
        tmp_path, "2026-10-10T12:00:00Z", "scope\nVerdict: pass",
        ("2026-10-10T12:00:05Z", "PLAN done\n1. design")))
    assert _complete(tid, "plan") == "`plan` done"
    assert _ready(tid) == ["implement"]


_SCOPE_AT = "2026-10-10T12:00:00Z"
_SCOPE = "scope\nVerdict: pass"
_CREATED = "2026-10-10T12:00:05Z"
_BEFORE = "2026-10-10T11:59:59Z"


@pytest.mark.parametrize(("scope_at", "scope", "created", "body", "ok"), [
    (_SCOPE_AT, _SCOPE, _CREATED, "PLAN done\n1. design", True),
    (_SCOPE_AT, _SCOPE, _CREATED, "PLAN done\nplan doc: plan/x-2026-10-10.md", True),
    (_SCOPE_AT, _SCOPE, _CREATED, "PLAN done\r\nplan/x.md", True),
    (_SCOPE_AT, "scope\r\nVerdict: pass\r", _CREATED, "PLAN done\nplan/x.md", True),
    (_SCOPE_AT, _SCOPE, _CREATED, "PLAN in progress\nCritic reviewing", False),
    (_SCOPE_AT, _SCOPE, _CREATED, "PLAN\n1. design", False),
    (_SCOPE_AT, _SCOPE, _CREATED, "plan doc: plan/x-2026-10-10.md", False),
    (_SCOPE_AT, _SCOPE, _CREATED, "PLAN done soon\nplan/x-2026-10-10.md", False),
    (_SCOPE_AT, _SCOPE, _CREATED, "PLAN in progress\nplan/x-2026-10-10.md", False),
    (_SCOPE_AT, _SCOPE, _CREATED, "starting the implementation", False),
    (_SCOPE_AT, _SCOPE, _BEFORE, "PLAN done\n1. design", False),  # before the scope pass
    (_SCOPE_AT, _SCOPE, _SCOPE_AT, "PLAN done\n1. design", False),  # not strictly after
    (_SCOPE_AT, _SCOPE, _CREATED, "Planned for later", False),  # not a PLAN heading
    (_SCOPE_AT, _SCOPE, _CREATED, "see https://x/workplan/", False),  # not a plan/ doc link
    (None, None, _CREATED, "PLAN done\nplan/x.md", False),  # no scope pass
])
def test_seed_plan_wants_a_plan_comment_after_the_scope_pass(
        scope_at, scope, created, body, ok, repo, tmp_path):
    t = graph.parse((library.WORKFLOWS / "issue-resolution.yaml").read_text(), strict=True)
    node = next(n for n in t["nodes"] if n["id"] == "plan")
    assert "skill" not in node
    if scope_at is None:
        env = {"PATH": _gh(tmp_path, created, body), "WF_ISSUE": ISSUE}
    else:
        env = {"PATH": _gh(tmp_path, scope_at, scope, (created, body)), "WF_ISSUE": ISSUE}
    assert (checks.run(node["check"], repo, env, 30)[0] == 0) is ok


@pytest.mark.parametrize(("created", "skill_at", "body", "ok"), [
    ("2026-10-10T12:00:05Z", "2026-10-10T12:00:00Z", "scope\nVerdict: pass", True),
    ("2026-10-10T12:00:00Z", "2026-10-10T12:00:00Z", "SCOPE review\nverdict: pass", True),
    ("2026-10-10T11:59:59Z", "2026-10-10T12:00:00Z", "scope\nVerdict: pass", False),
    ("2026-10-10T12:00:05Z", "2026-10-10T12:00:00Z", "scoped\nVerdict: pass", False),
    ("2026-10-10T12:00:05Z", "2026-10-10T12:00:00Z", "review\nscope\nVerdict: pass", False),
    ("2026-10-10T12:00:05Z", "2026-10-10T12:00:00Z", "scope\nVerdict: revise", False),
    ("2026-10-10T12:00:05Z", "2026-10-10T12:00:00Z",
     "scope\nprevious Verdict: pass\nVerdict: pass\nthe new module is unneeded\nVerdict: revise", False),
    ("2026-10-10T12:00:05Z", "2026-10-10T12:00:00Z", "scope\n- a → b\nVerdict: pass\n\n", True),
])
def test_seed_scope_review_wants_a_receipt_after_it_became_ready(
        created, skill_at, body, ok, repo, tmp_path):
    t = graph.parse((library.WORKFLOWS / "issue-resolution.yaml").read_text(), strict=True)
    node = next(n for n in t["nodes"] if n["id"] == "scope_review")
    assert "skill" not in node
    env = {"PATH": _gh(tmp_path, created, body), "WF_ISSUE": ISSUE,
           "WF_READY_SINCE": skill_at}
    assert (checks.run(node["check"], repo, env, 30)[0] == 0) is ok


def test_a_later_call_does_not_move_the_anchor(tid, repo):
    """Another task's call recorded here later must not push WF_SKILL_AT past a
    receipt already written after the first real call."""
    Path(repo, "a.ok").touch()
    close(tid, "a")
    _skill(repo, "demo:review")
    _skill(repo, "demo:review", agent_id="sub")
    conn = store.connect()
    rows = conn.execute("SELECT rowid FROM event WHERE task = ? AND kind = 'skill' ORDER BY rowid",
                        (tid,)).fetchall()
    conn.execute("UPDATE event SET ts = ts + 600 WHERE rowid = ?", (rows[-1][0],))
    conn.commit()
    task = store.get_task(conn, tid)
    t = graph.parse(task["graph"])
    node = next(n for n in t["nodes"] if n["id"] == "r")
    first = conn.execute("SELECT MIN(ts) FROM event WHERE task = ? AND kind = 'skill'",
                         (tid,)).fetchone()[0]
    assert graph.skill_at(conn, task, t, node) == first


def _sha(repo: str) -> str:
    return subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"], capture_output=True,
                          text=True, check=True).stdout.strip()


def _commit(repo: str, name: str, text: str) -> None:
    Path(repo, name).write_text(text)
    subprocess.run(["git", "-C", repo, "add", name], check=True)
    subprocess.run(["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@t",
                    "commit", "-qm", name], check=True)


def test_review_carries_on_equivalent_python_only(repo, tmp_path, monkeypatch):
    """A passed `carry: code` review carries over an AST-equivalent commit; a semantic
    change, or a review that failed since, needs a new receipt."""
    _commit(repo, "m.py", "x = 1\n")
    tid = _seeded(repo, "simple")
    close(tid, "implement", "test")
    with pytest.raises(graph.WfError, match="is not ready"):
        _complete(tid, "pr")
    monkeypatch.setenv("PATH", _gh(tmp_path, "2999-01-01T00:00:00Z", f"review {_sha(repo)}\nVerdict: pass"))
    assert _complete(tid, "review") == "`review` done"
    assert _ready(tid) == ["pr"]
    conn = store.connect()
    _commit(repo, "m.py", "x  =  1\n")  # same AST: the old receipt carries
    for n in ("review", "pr"):
        store.set_node(conn, tid, n, status="pending")
    assert _complete(tid, "review") == "`review` done"
    _commit(repo, "m.py", "x = 2\n")  # changed code
    for n in ("review", "pr"):
        store.set_node(conn, tid, n, status="pending")
    with pytest.raises(graph.WfError, match="check failed"):
        _complete(tid, "review")
    assert checks.fail(conn, store.get_task(conn, tid), "review", "review rejected")
    assert statuses(tid)["implement"] == statuses(tid)["review"] == "pending"
    close(tid, "implement", "test")
    _commit(repo, "m.py", "x  =  2\n")  # equivalent again, but the review failed since
    with pytest.raises(graph.WfError, match="check failed"):
        _complete(tid, "review")
    assert "pr" not in _ready(tid)


def test_a_skill_call_before_its_node_is_ready_is_refused(tid, repo, capsys):
    """A call that would not count is refused up front, not silently redone."""
    def call(name: str = "demo:review") -> int:
        store.register(store.connect(), "s1")
        return run_hook("pre-tool-use", {
            "cwd": repo, "session_id": "s1", "tool_name": "Skill", "tool_input": {"skill": name}})
    assert call() == 0  # no task yet
    _hook("pre-tool-use", repo, tool_name="mcp__wf__wf_attach", tool_input={"issue_url": ISSUE})
    assert call() == 2
    assert "preceding nodes (a)" in capsys.readouterr().err
    assert call("other:skill") == 0
    store.set_state(store.connect(), tid, "paused")
    assert call() == 0
    store.set_state(store.connect(), tid, "active")
    close(tid, "a")
    assert call() == 0
