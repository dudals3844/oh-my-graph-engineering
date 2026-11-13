"""Every wf test gets its own DB (`WF_DB`), library (`WF_LIBRARY`) and, on request,
a throwaway checkout.

The real DB and library live under `~/.local/share/omge/`; nothing here may touch them.
"""

from __future__ import annotations

import json
import subprocess
import textwrap

import pytest

from omge import checks, graph, hooks, library, store

REAL_EXEMPT_ROOTS = hooks.EXEMPT_ROOTS

TEMPLATE = textwrap.dedent(
    """
    type: demo
    match: test graph
    params:
      name: '^[a-z;]+$'
    nodes:
      - id: a
        do: make a.ok
        check: test -f a.ok
        on_fail: retry
        pitfalls: [remember a]
      - id: b
        after: [a]
        do: make b.ok
        check: test -f b.ok
        on_fail: retry
      - id: h
        kind: human
        after: [b]
        do: a human step
      - id: m
        kind: human
        after: [b]
        do: a human step proven by a check
        check: test -f merged.ok
        on_fail: retry
      - id: retro
        after: [h, m]
    """
)


@pytest.fixture(autouse=True)
def wf_db(tmp_path, monkeypatch):
    monkeypatch.setenv("WF_DB", str(tmp_path / "wf.db"))
    monkeypatch.setenv("WF_LIBRARY", str(tmp_path / "library"))
    monkeypatch.setattr(library, "issue_text", lambda url: ("", ""))  # the real one calls gh
    # The drawing snapshots here are the chain renderer's; box has its own (test_render_box).
    monkeypatch.setenv("WF_RENDER", "chain")
    # Set when the suite itself runs under Claude Code; it would override `cwd`.
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    for var in ("OMGE_LANG", "LC_ALL", "LC_MESSAGES"):  # messages default to English
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("LANG", "C.UTF-8")
    # tmp_path is under /tmp, which the PreToolUse guard exempts, so writes into a
    # test worktree would never be guarded. `test_real_exempt_roots` covers the real set.
    monkeypatch.setattr(hooks, "EXEMPT_ROOTS", (tmp_path / "exempt",))
    # Every finished task's retro is due here; the skip rules have their own test with
    # their own RETRO_EVERY.
    monkeypatch.setattr(checks, "RETRO_EVERY", 1)
    # Tests never read the user's real home (~/.claude, ~/.local/share).
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


@pytest.fixture
def workflows(tmp_path, monkeypatch):
    d = tmp_path / "workflows"
    d.mkdir()
    (d / "demo.yaml").write_text(TEMPLATE)
    monkeypatch.setattr(library, "WORKFLOWS", d)
    monkeypatch.setattr(graph, "RETRO_CHECK", "true")  # the real one calls gh
    return d


def make_repo(path, origin="git@github.com:example/demo.git"):
    path.mkdir()
    for args in (["init", "-q", "-b", "demo-1"], ["remote", "add", "origin", origin],
                 ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q",
                  "--allow-empty", "-m", "init"]):
        subprocess.run(["git", "-C", str(path), *args], check=True)
    return str(path.resolve())


@pytest.fixture
def repo(tmp_path):
    return make_repo(tmp_path / "repo")


ISSUE = "https://github.com/example/demo/issues/1"


def start(dir_: str, type_: str = "demo", issue: str = ISSUE, **kw) -> int:
    return graph.start(store.connect(), type_, dir_, issue, kw.pop("params", {}), **kw)


def close(tid: int, *nodes: str, status: str = "done") -> None:
    """Close nodes as their check would, routes and all."""
    conn = store.connect()
    for n in nodes:
        checks.close(conn, store.get_task(conn, tid), n, status=status, done_by="check",
                     evidence="")


def state(tid: int) -> str:
    return store.get_task(store.connect(), tid)["state"]


def statuses(tid: int) -> dict[str, str]:
    return {k: r["status"] for k, r in store.node_rows(store.connect(), tid).items()}


def reviewed_retro(conn, task, verdict, deviations, **kw):
    """Direct callers still consume an independent successful hook receipt, without MCP2."""
    context = graph.structural_context(conn, task)
    review = {"prior_changes": [{"node": n, "verdict": "confounded",
                                 "evidence": "isolated execution cannot attribute effect"}
                                for n in context["prior_changes"]],
              "task": task["id"], "snapshot": context["snapshot"],
              "axes": {"order": "compared the task's preconditions with the run order",
                       "duplication": "compared each node's responsibility",
                       "missing": "the required steps are present",
                       "rework": "told run failure/back apart from external waits"},
              "findings": kw.pop("findings", []),
              "reuse_graph": kw.get("graph") or task["graph"], "adopted": []}
    ack = graph.structural_ack(conn, task, review)
    hooks.post_tool_use(conn, "test-parent", "mcp__wf__wf_retro",
                        {"task": task["id"], "structural_review": review}, json.dumps(ack),
                        in_agent=True, agent_id="test-reviewer")
    if verdict == "no_change" and (graph.NO_DEVIATION.fullmatch(deviations) or deviations == "-"):
        kw.setdefault("reason", "the four-axis review keeps the existing structure")
    return graph.record_retro(conn, task, verdict, deviations, review_id=ack["review_id"], **kw)
