"""`protected` and `floor` gates: a waiver stands on a current-HEAD pass, a floor never
waives and is re-run before anything after it closes, and a running task cannot loosen either."""

from __future__ import annotations

import json
import subprocess
import textwrap
from pathlib import Path

import pytest

from omge import checks, graph, server, store
from tests.conftest import start, state, statuses

GATED = textwrap.dedent(
    """
    type: gated
    match: measure then review then ship
    nodes:
      - id: build
        do: make build.ok
        check: test -f build.ok
        on_fail: retry
      - id: measure
        after: [build]
        floor: true
        do: make measure.ok
        check: test -f measure.ok
        on_fail: retry
      - id: review
        after: [measure]
        protected: true
        do: make review.ok
        check: test -f review.ok
        on_fail: retry
      - id: ship
        after: [review]
        do: make ship.ok
        check: test -f ship.ok
        on_fail: retry
      - id: docs
        after: [build]
        do: make docs.ok
        check: test -f docs.ok
        on_fail: retry
      - id: retro
        after: [ship, docs]
    """
)


def _start(workflows, repo, body: str = GATED, name: str = "gated") -> int:
    (workflows / f"{name}.yaml").write_text(body.replace("type: gated", f"type: {name}"))
    tid = start(repo, type_=name)
    conn = store.connect()
    store.own(conn, "s1", store.get_task(conn, tid)["issue_url"])
    return tid


@pytest.fixture
def gated(workflows, repo):
    return _start(workflows, repo)


def _head(repo: str) -> str:
    return subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()


def _complete(tid: int, *nodes: str) -> None:
    conn = store.connect()
    for n in nodes:
        checks.complete(conn, store.get_task(conn, tid), n, "")


def _make(repo: str, *names: str) -> None:
    for name in names:
        Path(repo, name).write_text("")


def _review(tid: int, node: str, text: str, head: str) -> None:
    """What the PostToolUse hook records for a reviewer's reply."""
    conn = store.connect()
    v, sha = graph.verdict(text)
    store.event(conn, tid, node, "reviewer", "reviewer")
    store.event(conn, tid, node, "review", json.dumps(
        {"agent": "reviewer", "verdict": v, "sha": sha, "head": head}))


def _pass(sha: str) -> str:
    return f"Verdict: pass\nReviewed SHA: {sha}\n"


def test_checks_close_the_gated_graph_as_usual(gated, repo):
    _make(repo, "build.ok", "measure.ok", "review.ok", "ship.ok", "docs.ok")
    _complete(gated, "build", "measure", "review", "ship", "docs")
    conn = store.connect()
    checks.skip_retro(conn, store.get_task(conn, gated), "nothing to improve")
    assert state(gated) == "done"


def test_protected_waive_needs_a_pass_for_the_current_head(gated, repo):
    _make(repo, "build.ok", "measure.ok")
    _complete(gated, "build", "measure")
    head = _head(repo)
    store.event(store.connect(), gated, "review", "reviewer", "reviewer")
    with pytest.raises(Exception, match="protected"):  # a call alone is not a pass
        server.wf_waive("review", "reviewed", task=gated)
    _review(gated, "review", _pass("b" * 40), head)  # the reviewer read another commit
    with pytest.raises(Exception, match="protected"):
        server.wf_waive("review", "reviewed", task=gated)
    _review(gated, "review", _pass(head), head)
    subprocess.run(["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit",
                    "-q", "--allow-empty", "-m", "moved"], check=True)
    with pytest.raises(Exception, match="protected"):  # HEAD moved past the pass
        server.wf_waive("review", "reviewed", task=gated)
    head = _head(repo)
    _review(gated, "review", _pass(head), head)
    assert "waived" in server.wf_waive("review", "reviewed", task=gated)
    assert statuses(gated)["review"] == "waived"


def test_a_blocking_review_still_refuses_a_protected_waive(gated, repo):
    _make(repo, "build.ok", "measure.ok")
    _complete(gated, "build", "measure")
    head = _head(repo)
    _review(gated, "review", _pass(head), head)
    conn = store.connect()
    store.event(conn, gated, "review", "review", json.dumps(
        {"agent": "other", "verdict": "blocking", "sha": head, "head": head}))
    with pytest.raises(Exception, match="blocking"):
        server.wf_waive("review", "reviewed", task=gated)


def test_an_ordinary_node_waives_on_a_reviewer_call(gated, repo):
    store.event(store.connect(), gated, "build", "reviewer", "reviewer")
    assert "waived" in server.wf_waive("build", "not needed", task=gated)


def test_a_floor_never_waives(gated, repo):
    _make(repo, "build.ok")
    _complete(gated, "build")
    head = _head(repo)
    _review(gated, "measure", _pass(head), head)
    with pytest.raises(Exception, match="floor"):
        server.wf_waive("measure", "reviewed", task=gated)
    conn = store.connect()
    with pytest.raises(graph.WfError, match="floor"):  # the user's `wf waive` path too
        checks.close(conn, store.get_task(conn, gated), "measure", status="waived",
                     done_by="user", evidence="no")
    assert statuses(gated)["measure"] == "pending"


def test_a_stale_floor_reopens_before_an_ordinary_close(gated, repo):
    _make(repo, "build.ok", "measure.ok", "review.ok")
    _complete(gated, "build", "measure")
    Path(repo, "measure.ok").unlink()  # its stored pass is no longer true
    with pytest.raises(graph.WfError, match="measure"):
        _complete(gated, "review")
    assert statuses(gated) == {"build": "done", "measure": "pending", "review": "pending",
                               "ship": "pending", "docs": "pending", "retro": "pending"}
    _make(repo, "measure.ok")
    _complete(gated, "measure", "review")
    assert statuses(gated)["review"] == "done"


def test_a_stale_floor_reopens_before_a_waive_and_the_retro_skip(gated, repo):
    _make(repo, "build.ok", "measure.ok")
    _complete(gated, "build", "measure")
    Path(repo, "measure.ok").unlink()
    head = _head(repo)
    _review(gated, "review", _pass(head), head)
    with pytest.raises(Exception, match="measure"):
        server.wf_waive("review", "reviewed", task=gated)
    assert statuses(gated)["measure"] == "pending"
    _make(repo, "measure.ok", "review.ok", "ship.ok")
    _complete(gated, "measure", "review", "ship")
    Path(repo, "measure.ok").unlink()
    conn = store.connect()
    with pytest.raises(graph.WfError, match="measure"):
        checks.skip_retro(conn, store.get_task(conn, gated), "nothing to improve")
    assert statuses(gated)["measure"] == "pending" and state(gated) != "done"


def test_a_floor_graph_never_closes_its_retro_unrun(gated, repo, monkeypatch):
    """`docs` is not after the floor: its close re-runs nothing, so the idle retro must wait."""
    monkeypatch.setattr(checks, "RETRO_EVERY", 99)
    _make(repo, "build.ok", "measure.ok", "review.ok", "ship.ok", "docs.ok")
    _complete(gated, "build", "measure", "review", "ship")
    Path(repo, "measure.ok").unlink()
    _complete(gated, "docs")
    assert statuses(gated)["retro"] == "pending" and state(gated) != "done"


def test_a_pending_floor_refuses_a_downstream_user_waiver(gated, repo):
    _make(repo, "build.ok")
    _complete(gated, "build")
    conn = store.connect()
    with pytest.raises(graph.WfError, match="measure"):
        checks.close(conn, store.get_task(conn, gated), "ship", status="waived",
                     done_by="user", evidence="ship it anyway")
    assert statuses(gated)["ship"] == "pending"


BRANCHED = textwrap.dedent(
    """
    type: gated
    match: measure on one branch, then join
    nodes:
      - id: pick
        do: choose
        check: test -d .
        on_fail: retry
        next: [{to: measure, when: 'test -f measure.want'}, {to: other}]
      - id: measure
        floor: true
        do: make measure.ok
        check: test -f measure.ok
        on_fail: retry
      - id: other
        do: nothing
        check: test -d .
        on_fail: retry
      - id: join
        after: [measure, other]
        do: join
        check: test -d .
        on_fail: retry
      - id: retro
        after: [join]
    """
)


def test_a_floor_a_route_skipped_blocks_the_join(workflows, repo):
    tid = _start(workflows, repo, BRANCHED, "branched")
    _complete(tid, "pick", "other")
    assert statuses(tid)["measure"] == "skipped"
    with pytest.raises(graph.WfError, match="measure"):
        _complete(tid, "join")
    assert statuses(tid)["join"] == "pending" and state(tid) != "done"


def test_a_floor_rerun_sees_its_skill_receipt(workflows, repo):
    body = GATED.replace("    floor: true\n", "    floor: true\n    skill: measuring\n"
                         ).replace("check: test -f measure.ok",
                                   "check: '[ -n \"$WF_SKILL_AT\" ] && test -f measure.ok'")
    assert "skill: measuring" in body and "WF_SKILL_AT" in body
    tid = _start(workflows, repo, body, "skilled")
    _make(repo, "build.ok", "measure.ok", "review.ok", "ship.ok")
    _complete(tid, "build")
    store.event(store.connect(), tid, "measure", "skill", "measuring")
    _complete(tid, "measure", "review")  # the rerun passes on the same evidence
    assert statuses(tid)["review"] == "done"
    Path(repo, "measure.ok").unlink()
    with pytest.raises(graph.WfError, match="measure"):
        _complete(tid, "ship")
    _make(repo, "measure.ok")
    with pytest.raises(graph.WfError, match="measuring"):  # the reopen is a new ready
        _complete(tid, "measure")
    store.event(store.connect(), tid, "measure", "skill", "measuring")
    _complete(tid, "measure")


def test_a_floor_reopen_spends_no_declared_loop(workflows, repo):
    body = GATED.replace("    floor: true\n", "    floor: true\n    next: [{to: measure,"
                         " when: 'test -f measure.again', max: 1}, {to: review}]\n")
    assert "measure.again" in body
    tid = _start(workflows, repo, body, "looped")
    _make(repo, "build.ok", "measure.ok", "review.ok")
    _complete(tid, "build", "measure")
    Path(repo, "measure.ok").unlink()
    with pytest.raises(graph.WfError, match="measure"):
        _complete(tid, "review")
    _make(repo, "measure.ok", "measure.again")
    _complete(tid, "measure")  # the one declared loop is still there to take
    assert statuses(tid)["measure"] == "pending"
    Path(repo, "measure.again").unlink()
    _complete(tid, "measure", "review")
    assert statuses(tid)["review"] == "done"


@pytest.mark.parametrize("nodes", [
    {"review": None, "ship": {"after": ["measure"]}},      # delete the gate
    {"measure": {"check": "test -f other.ok"}},             # change a floor's check
    {"review": {"do": "something else"}},                   # change a gate's `do`
    {"review": {"timeout": 5}},                             # change its shape
    {"review": {"protected": False}},                       # clear the flag
    {"measure": {"floor": False}},                          # protected still, floor gone
    {"measure": {"floor": False, "protected": False}},
    {"ship": {"after": ["build"]}, "retro": {"after": ["review", "ship", "docs"]}},
    {"ship": None, "retro": {"after": ["review", "docs"]}},  # delete a descendant
    {"retro": {"after": ["docs"]}},                         # detach the final recheck
])
def test_a_running_task_cannot_loosen_a_gate(gated, repo, nodes):
    _make(repo, "build.ok", "measure.ok")
    _complete(gated, "build", "measure")
    conn = store.connect()
    before = store.get_task(conn, gated)["graph"]
    with pytest.raises(graph.WfError, match="protected gate"):
        graph.revise(conn, store.get_task(conn, gated), {"nodes": nodes}, "loosen")
    assert store.get_task(conn, gated)["graph"] == before


def test_a_revise_may_add_a_gate_and_keep_ordinary_nodes_editable(gated, repo):
    conn = store.connect()
    graph.revise(conn, store.get_task(conn, gated),
                 {"nodes": {"build": {"check": "test -f build2.ok"},
                            "ship": {"protected": True}}}, "stronger")
    t = graph.parse(store.get_task(conn, gated)["graph"])
    assert [n["check"] for n in t["nodes"] if n["id"] == "build"] == ["test -f build2.ok"]
    with pytest.raises(graph.WfError, match="protected gate"):
        graph.revise(conn, store.get_task(conn, gated),
                     {"nodes": {"ship": {"protected": False}}}, "weaker")


def test_a_finished_node_reopens_when_it_becomes_a_gate(gated, repo):
    _make(repo, "build.ok")
    _complete(gated, "build")
    conn = store.connect()
    graph.revise(conn, store.get_task(conn, gated), {"nodes": {"build": {"protected": True}}},
                 "its waiver rule changed")
    assert statuses(gated)["build"] == "pending"


def test_the_flags_round_trip_and_are_validated():
    nodes = [{"id": "m", "do": "x", "check": "test -f m", "on_fail": "retry", "floor": True},
             {"id": "r", "after": ["m"], "do": "x", "check": "test -f r", "on_fail": "retry",
              "protected": True, "carry": "code"}]
    t = graph.parse(graph.submitted(json.loads(json.dumps(nodes)), "new", "m"))
    by = {n["id"]: n for n in t["nodes"]}
    assert (by["m"].get("floor"), by["m"].get("protected")) == (True, True)
    assert (by["r"].get("floor"), by["r"].get("protected")) == (None, True)
    plain = graph.parse(graph.submitted([{**nodes[1], "protected": False, "after": []}],
                                            "new", "m"))
    assert "protected" not in plain["nodes"][0]  # explicit false is no gate, and no change
    assert graph._shape(plain["nodes"][0]) != graph._shape(by["r"])
    for bad in ({**nodes[0], "floor": "yes"}, {"id": "m", "do": "x", "kind": "human", "floor": True},
                {**nodes[0], "carry": "code"}):
        with pytest.raises(graph.WfError, match="floor|protected"):
            graph.submitted([bad], "new", "m", strict=False)
    with pytest.raises(graph.WfError, match="protected"):
        graph.submitted([nodes[0], {"id": "retro", "protected": True}], "new", "m")
    # A library update that drops a gate is a weakening, and the retro's reuse review sees it.
    loose = graph.parse(graph.submitted([nodes[0], {**nodes[1], "protected": False}], "new", "m"))
    assert graph._weakened(t, loose) == ["r"] and graph._protected_changes(t, loose) == ["r"]
