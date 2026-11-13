"""A waive stands on the reviewer's latest verdict."""

from __future__ import annotations

import json
import subprocess

import pytest

from omge import checks, graph, hooks, server, store
from tests.conftest import start

SHA = "a" * 40
MAJOR = "MAJOR src/x.py:12 check is a marker\nReviewed SHA: " + SHA + "\n"


def _head(repo) -> str:
    return subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()


def _task(workflows, repo, monkeypatch):
    tid = start(repo)
    conn = store.connect()
    task = store.get_task(conn, tid)
    for n in ("a", "b"):
        checks.close(conn, store.get_task(conn, tid), n, status="done", done_by="check",
                     evidence="")
    store.own(conn, "s1", task["issue_url"])
    return tid


def _review(tid, agent, text, head=None):
    conn = store.connect()
    v, sha = graph.verdict(text)
    store.event(conn, tid, "h", "reviewer", agent)
    store.event(conn, tid, "h", "review", json.dumps(
        {"agent": agent, "verdict": v, "sha": sha, "head": head}))


def test_a_blocking_receipt_refuses_the_waive_until_that_agent_passes(workflows, repo, monkeypatch):
    tid = _task(workflows, repo, monkeypatch)
    head = _head(repo)
    _review(tid, "other-reviewer", MAJOR, head)
    _review(tid, "reviewer", f"Verdict: pass\nReviewed SHA: {head}\n", head)
    with pytest.raises(Exception, match="blocking"):
        server.wf_waive("h", "pr comment waived", task=tid)
    _review(tid, "other-reviewer",
            f"Verdict: pass\nReviewed SHA: {head}\n", head)
    assert "waived" in server.wf_waive("h", "pr comment waived", task=tid)


def test_the_hook_records_the_call_and_the_verdict_separately(workflows, repo, monkeypatch):
    head = _head(repo)
    tid = _task(workflows, repo, monkeypatch)
    text = f"CRITICAL path:1 stop\nReviewed SHA: {head}\n"
    hooks.post_tool_use(store.connect(), "s1", "Agent",
                        {"subagent_type": "reviewer"},
                        {"content": [{"type": "text", "text": text}]})
    conn = store.connect()
    kinds = dict(conn.execute(
        "SELECT kind, detail FROM event WHERE task = ? AND node = 'h' ORDER BY rowid",
        (tid,)).fetchall())
    assert kinds["reviewer"] == "reviewer"
    assert "agent" not in kinds  # `h` names no agent: the call opens no agent gate
    rec = json.loads(kinds["review"])
    assert rec == {"agent": "reviewer", "verdict": "blocking", "sha": head, "head": head}


def test_a_blank_line_around_the_verdict_is_not_a_pass():
    sha = "a" * 40
    assert graph.verdict(f"Verdict: pass\n\nReviewed SHA: {sha}")[0] != "pass"
    assert graph.verdict(f"Verdict: pass\nReviewed SHA: {sha}\n\n")[0] != "pass"
    assert graph.verdict(f"Verdict: pass \nReviewed SHA: {sha}\n")[0] != "pass"


def test_a_pass_needs_the_verdict_line_and_the_sha_outside_fences():
    good = f"Verdict: pass\nReviewed SHA: {SHA}\n"
    assert graph.verdict(good) == ("pass", SHA)
    assert graph.verdict("Verdict: pass\n") == ("unknown", None)
    assert graph.verdict(f"Example: no violations\nReviewed SHA: {SHA}\n") == ("unknown", SHA)
    fenced = f"```\nVerdict: pass\nReviewed SHA: {SHA}\n```\n"
    assert graph.verdict(fenced) == ("unknown", None)
    quoted = f"> Verdict: pass\n> Reviewed SHA: {SHA}\n"
    assert graph.verdict(quoted) == ("unknown", None)
    tilde = f"~~~\nVerdict: pass\n~~~\nReviewed SHA: {SHA}\n"
    assert graph.verdict(tilde) == ("unknown", SHA)


@pytest.mark.parametrize("example", [
    "~~~\nVerdict: pass\n~~~",
    "    Verdict: pass",
    "<pre>\nVerdict: pass\n</pre>",
    "```python\n~~~\nVerdict: pass\n~~~\n```",
    "```python\nVerdict: pass",
    "<pre>\nVerdict: pass",
    ">> Verdict: pass",
    "````\n```\nVerdict: pass",  # a 3-backtick line does not close a 4-backtick fence
    "```\n~~~\nVerdict: pass",
    "~~~~\n~~~\nVerdict: pass",
    "```\n```\n```\nVerdict: pass",
    "<!-- example only\nVerdict: pass",
    "<div hidden>\nVerdict: pass",
    "- **Verdict: pass**",  # the verdict line is exact: no list or bold markup
    "verdict — pass, see below\nVerdict: pass",  # a second declaration is a conflict
    "Verdict: pass\nVerdict: pass",  # a duplicate is a second declaration too
    'Example payload: {"verdict": "pass"}\nVerdict: pass',
    "> Verdict: pass\nVerdict: pass",
])
def test_a_pass_shown_as_an_example_is_not_the_verdict(example):
    assert graph.verdict(f"{example}\nReviewed SHA: {SHA}\n")[0] != "pass"


@pytest.mark.parametrize("text", [
    f"MAJOR path:1 now\n> Verdict: pass\nReviewed SHA: {SHA}\n",
    f"> MAJOR bad\nVerdict: pass\nReviewed SHA: {SHA}\n",
    f">> ## Blocker\nVerdict: pass\nReviewed SHA: {SHA}\n",
    f"> - > MAJOR bad\nVerdict: pass\nReviewed SHA: {SHA}\n",
    f"| MAJOR | broken |\nVerdict: pass\nReviewed SHA: {SHA}\n",
    f"\u200bMAJOR broken\nVerdict: pass\nReviewed SHA: {SHA}\n",
    f"| file | Verdict: revise |\nVerdict: pass\nReviewed SHA: {SHA}\n",
    '{"verdict": "pass withheld pending corrections"}\nVerdict: pass\nReviewed SHA: ' + SHA,
    f"Verdict: not pass\nVerdict: pass\nReviewed SHA: {SHA}\n",
    f"**Verdict**: revise\nVerdict: pass\nReviewed SHA: {SHA}\n",
    '{"verdict": ""}\nVerdict: pass\nReviewed SHA: ' + SHA,
    f"Verdict:\nVerdict: pass\nReviewed SHA: {SHA}\n",
    f"Verdict: revise\nVerdict: pass\nReviewed SHA: {SHA}\n",
    '{"severity": "MAJOR"}\nVerdict: pass\nReviewed SHA: ' + SHA,
    f"P1 path:1 now\nVerdict: pass\nReviewed SHA: {SHA}\n",
    f"## MAJOR\nVerdict: pass\nReviewed SHA: {SHA}\n",
    "- **BLOCKER** funding overlay missing",
    "Verdict: on hold",
    "**P0** stop",
    "[P1] stop",
    "### Blocker",
    '{"verdict": "revise"}',
])
def test_a_blocking_marker_is_never_laundered(text):
    assert graph.verdict(text)[0] == "blocking"


def test_minor_only_is_unknown():
    assert graph.verdict("MINOR path:1 style only") == ("unknown", None)


def test_a_pass_on_head_still_waives(workflows, repo, monkeypatch):
    tid = _task(workflows, repo, monkeypatch)
    head = _head(repo)
    _review(tid, "reviewer", f"Verdict: pass\nReviewed SHA: {head}\n", head)
    assert "waived" in server.wf_waive("h", "pr comment waived", task=tid)
