"""Task score, template conformance, and candidate versions promoted on score."""

from __future__ import annotations

import pytest
import yaml

from omge import graph, hooks, library, store

from .conftest import ISSUE, close, start
from .test_library import GRAPH, _retro, _task


def _row(tid: int):
    conn = store.connect()
    return conn, store.get_task(conn, tid)


# --- score ---------------------------------------------------------------------------

def test_score_formula():
    conn = store.connect()
    clean = _task(conn, "v1", hours=1)
    assert library.score(conn, store.get_task(conn, clean), 3600) == 1.0
    messy = _task(conn, "v1", hours=1)
    for kind, detail in [("stall", ""), ("waive", ""), ("back", "a"), ("wf_fail", "a: red"),
                         ("wf_fail", "retry: red"), ("revise", "changed")]:
        store.event(conn, messy, "b", kind, detail)
    # 0.25 + 0.1 (waive) + 0.1 (back; its wf_fail is not counted twice) + 0.1 (retry) = 0.55;
    # a wf_revise reports a deviation and costs nothing
    assert library.score(conn, store.get_task(conn, messy), 3600) == pytest.approx(0.725)
    slow = store.get_task(conn, _task(conn, "v1", hours=3))  # 3× the median: slowness capped at 1
    assert library.score(conn, slow, 3600) == pytest.approx(0.5 + 0.5 * 0.8)
    dropped = store.get_task(conn, _task(conn, "v1", state="dropped"))
    assert library.score(conn, dropped, 3600) == 0.5
    store.event(conn, dropped["id"], "", "stall")
    for _ in range(10):
        store.event(conn, dropped["id"], "a", "waive")
    assert library.score(conn, dropped, 3600) == 0.0  # never below 0


def test_versions_head_with_score(workflows):
    conn = store.connect()
    sha = library.body("demo")[1]
    for _ in range(2):
        _task(conn, sha)
    text = library.evidence(conn, "demo")
    assert f"current {sha[:7]}: score 1.00 ± 0.00 (n=2)" in text
    assert "    started 2 · done 2 · stall 0" in text


# --- conformance ---------------------------------------------------------------------

def test_conformance_on_a_followed_path(workflows, repo):
    tid = start(repo)
    close(tid, "a", "b", "h", "m")
    conn, row = _row(tid)
    assert library.conformance(conn, row) == (1.0, 1.0, set())


def test_conformance_counts_order_extra_nodes_edges_and_unused(workflows, repo):
    tid = start(repo)
    conn = store.connect()
    for node, kind, detail in [("b", "done", ""), ("a", "done", ""), ("lint", "done", ""),
                               ("b", "back", "a"), ("b", "done", "")]:
        store.event(conn, tid, node, kind, detail)
    # b before its predecessor a ✗, a ✓, lint not in the template ✗, b → a is no template
    # edge (b's on_fail is retry) ✗, b after a ✓ — 2 of 5. h, m never ran — 2 of 4 used.
    assert library.conformance(conn, store.get_task(conn, tid)) == (0.4, 0.5, {"h", "m"})
    text = library.evidence(conn, "demo", store.get_task(conn, tid))
    assert text.startswith("This template explains 40% of the actual path (precision 50%)"
                           " — mean over 1 tasks\n"
                           "  This task: explains 40% of the actual path (precision 50%)")
    assert "The template explains only 40% of the actual path" in text
    # Open, h and m are not yet run: only a finished task names a node unneeded.
    assert "nodes unused in most tasks" not in text
    store.update_task(conn, tid, state="done")
    text = library.evidence(conn, "demo", store.get_task(conn, tid))
    assert ("nodes unused in most tasks ['h', 'm']: candidates to remove or split into branches"
            in text)


def test_conformance_does_not_count_a_skipped_branch_as_unused(workflows, repo):
    tid = start(repo)
    close(tid, "a", "b", "m")
    conn = store.connect()
    store.event(conn, tid, "h", "skip")  # the route took m, not h
    assert library.conformance(conn, store.get_task(conn, tid)) == (1.0, 1.0, set())


def test_a_submitted_graph_is_its_template_less_revisions(workflows, repo):
    tid = start(repo, type_="new", graph=GRAPH)
    conn, row = _row(tid)
    graph.revise(conn, row, GRAPH + "  - {id: lint, check: 'test -d .', on_fail: retry,"
                 " after: [fix]}\n", "lint was needed")
    close(tid, "reproduce", "fix", "lint")
    fit, prec, unused = library.conformance(conn, store.get_task(conn, tid))
    assert (fit, prec, unused) == (pytest.approx(2 / 3), 1.0, set())


def test_a_new_task_aggregates_only_itself(workflows, repo):
    a = start(repo, type_="new", graph=GRAPH)
    b = start(repo, type_="new", issue=ISSUE.replace("/1", "/2"), graph=GRAPH,
              new_reason="unrelated")
    store.event(store.connect(), b, "fix", "waive")
    conn, row = _row(a)
    text = library.evidence(conn, "new", row)
    assert "Type new: 1 past tasks" in text and "waived 1" not in text
    # Folded into a library type by update_type, the task counts there from now on.
    close(a, "reproduce", "fix")
    merged = yaml.safe_load(library.body("demo")[0])
    merged["nodes"] = (yaml.safe_load(GRAPH)["nodes"] +
                       [n for n in merged["nodes"] if n["id"] != "retro"])
    _retro(a, "update_type", name="demo", graph=merged)
    assert store.get_task(store.connect(), a)["type"] == "demo"


# --- candidates ----------------------------------------------------------------------

def _candidate(text: str = "") -> str:
    body = library.body("demo")[0]
    return library._write({"candidates/demo.yaml": body + (text or "# candidate\n")}, "c")


def _has_candidate() -> bool:
    return (library.root() / "candidates" / "demo.yaml").exists()


def test_update_type_replaces_default_and_retires_candidate(workflows, repo):
    default = library.body("demo")
    tid = start(repo)
    close(tid, "a", "b", "h", "m")
    _candidate()
    _retro(tid, "update_type",
           graph=default[0].replace("remember a", "remember a, reviewed v2"))
    first = library.body("demo")
    assert "reviewed v2" in first[0] and first[1] != default[1]
    assert not _has_candidate()
    conn = store.connect()
    for i in range(3):
        next_tid = start(repo, issue=None, title=f"matching {i}")
        assert store.get_task(conn, next_tid)["version"] == first[1]
    close(next_tid, "a", "b", "h", "m")
    _retro(next_tid, "update_type",
           graph=first[0].replace("reviewed v2", "reviewed v3"))
    assert "reviewed v3" in library.body("demo")[0]
    assert not _has_candidate()


def test_a_leftover_candidate_never_runs(workflows, repo):
    # Only the reviewed default is reused; a legacy candidate (built on an
    # older default, unreviewed) must not run on any task.
    _candidate()
    default = library.body("demo")[1]
    conn = store.connect()
    for i in range(4):
        tid = start(repo, issue=None, title=f"task {i}")
        assert store.get_task(conn, tid)["version"] == default
        assert "# candidate" not in store.get_task(conn, tid)["graph"]
        store.set_state(conn, tid, "dropped")
    assert "candidate" not in library.evidence(conn, "demo")


def test_reverting_a_promotion_keeps_default_tasks_on_the_default_template(workflows):
    # An older promotion named its candidate in a `Candidate:` trailer.
    default = library.body("demo")
    text = default[0].replace("make a.ok", "make a.ok CAND")
    cand = library._write({"candidates/demo.yaml": text}, "c")
    library._write({"demo.yaml": text, "candidates/demo.yaml": None},
                   f"wf candidate promote demo\n\nCandidate: {cand}")
    conn = store.connect()
    for _ in range(3):
        _task(conn, default[1], hours=0)
        _task(conn, cand, hours=0)
    for _ in range(6):
        store.event(conn, _task(conn, library.body("demo")[1], "stalled", hours=0), "b", "stall")
    advice = library.evidence(conn, "demo").split("consider reverting: ")[1].split("\n")[0]
    assert "revert --no-commit" in advice and "rm -q candidates/demo.yaml" in advice
    # A plain revert brings the candidate back in the default's commit: still the default.
    library._git("revert", "--no-edit", library.body("demo")[1])
    assert _has_candidate()
    row = store.get_task(conn, _task(conn, library.body("demo")[1]))
    assert library._template(conn, row) == yaml.safe_load(default[0])["nodes"]


# --- a param's contract is in a `do`; an unset one is named  -------------------

AUDIT = """
params: {audit: '^\\S+$'}
nodes:
  - {id: inventory, check: 'test -f {audit}', on_fail: retry, do: '%s'}
"""


def test_a_param_read_by_a_check_must_be_named_in_a_do(workflows, repo):
    with pytest.raises(graph.WfError, match=r"param \['audit'\] .* say in `do` what to wf_set"):
        start(repo, type_="new", graph=AUDIT % "make the inventory", new_reason="x")
    tid = start(repo, type_="new", graph=AUDIT % "write manifest.json, wf_set audit=<that path>",
                new_reason="x")
    conn, row = _row(tid)
    with pytest.raises(graph.WfError, match="audit"):
        graph.revise(conn, row, AUDIT % "make the inventory", "shortened the do")
    # An earlier node's `do` is enough.
    earlier = ("params: {audit: '^\\S+$'}\nnodes:\n"
               "  - {id: plan, kind: human, do: 'decide the audit path'}\n"
               "  - {id: inventory, after: [plan], check: 'test -f {audit}', on_fail: retry}\n")
    graph.submitted(earlier, "new", "m")


def test_an_unset_param_is_named_without_repeating_the_do(workflows, repo):
    do = "write manifest.json, wf_set audit=<that path>"
    tid = start(repo, type_="new", graph=AUDIT % do, new_reason="x")
    conn, row = _row(tid)
    note = ("param `audit` unset — the check cannot pass without it."
            " Set it first with wf_complete(params=…) or wf_set.")
    status = graph.status_text(conn, row)
    assert note in status and status.count(do) == 1
    code, text = hooks.stop(conn, row, 1e18)
    assert code == 2 and note in text and do not in text
    graph.set_param(conn, row, "audit", "manifest.json")
    assert note not in graph.status_text(conn, store.get_task(conn, tid))


def test_post_tool_use_redraws_after_wf_complete(workflows, repo):
    start(repo)
    close(1, "a")
    shown = hooks.post_tool_use(store.connect(), "s1", "mcp__wf__wf_complete",
                                {"issue_url": ISSUE})["systemMessage"]
    assert "[✔ a] ──► [▶ b]" in shown


def test_a_new_task_cannot_disappear_behind_an_unrelated_candidate(workflows, repo):
    from .test_library import GRAPH
    _candidate()
    tid = start(repo, type_="new", graph=GRAPH)
    close(tid, "reproduce", "fix")
    with pytest.raises(graph.WfError, match="must leave its graph in the library"):
        _retro(tid, "no_change", deviations="none")
    with pytest.raises(graph.WfError, match="reason required"):  # it would replace demo's gates
        _retro(tid, "update_type", name="demo")
    with pytest.raises(graph.WfError, match="leave its graph in the library"):
        _retro(tid, "no_change", name="demo", deviations="none")
    assert _has_candidate()
