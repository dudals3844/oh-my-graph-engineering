"""The box drawing: the mode setting, five situations, and the limits."""

from __future__ import annotations

import textwrap
from collections import Counter
from pathlib import Path

import pytest

from omge import graph

from .test_cycle import CYCLE, _big


@pytest.mark.parametrize("env,file,want", [
    (None, None, "box"), (None, "chain", "chain"), ("box", "chain", "box"),
    ("chain", None, "chain"), ("bogus", "chain", "chain"), (None, "bogus\n", "box"),
])
def test_render_mode_precedence(tmp_path, monkeypatch, env, file, want):
    monkeypatch.setattr(graph, "RENDER_FILE", tmp_path / "render")
    if file:
        (tmp_path / "render").write_text(file)
    if env:
        monkeypatch.setenv("WF_RENDER", env)
    else:
        monkeypatch.delenv("WF_RENDER", raising=False)
    assert graph.render_mode() == want


SHIPPED = Path(graph.__file__).parent / "workflows"
TASK = {"id": 1, "version": "", "issue_url": "https://github.com/example/demo/issues/1"}

QUEUE = textwrap.dedent(
    """
    type: queue
    match: queue test
    nodes:
      - {id: intake, check: 'test -d .'}
      - {id: queue, after: [intake], check: 'test -d .'}
      - id: deliver
        after: [queue]
        check: 'test -d .'
        next: [{to: queue, label: more, max: 5}, {to: report, label: empty}]
      - {id: report, check: 'test -d .'}
      - {id: retro, after: [report]}
    """
)
LINEAR = textwrap.dedent(
    """
    type: demo
    match: short test
    nodes:
      - {id: a, check: 'test -d .'}
      - {id: b, after: [a], check: 'test -d .'}
      - {id: retro, after: [b]}
    """
)


@pytest.fixture(autouse=True)
def box(monkeypatch):
    monkeypatch.setenv("WF_RENDER", "box")


def _rows(t: dict, done: tuple = (), **cols) -> dict:
    """Node rows as the DB has them: `done` closed, then any column set per node."""
    rows = {n["id"]: {"status": "done" if n["id"] in done else "pending", "loops": 0,
                      "route": None, "last_output_tail": None, "last_exit": None}
            for n in t["nodes"]}
    for col, values in cols.items():
        for nid, value in values.items():
            rows[nid][col] = value
    return rows


def _shown(body: str, done: tuple = (), state: str = "active", **cols) -> tuple[dict, dict, dict]:
    t = graph.parse(body)
    return {**TASK, "type": t["type"], "state": state}, t, _rows(t, done, **cols)


def _issue(**cols):
    return _shown((SHIPPED / "issue-resolution.yaml").read_text(),
                  ("claim", "spec", "scope_review", "plan"),
                  loops={"implement": 1}, route={"scope_review": "plan"}, **cols)


def test_box_issue_resolution_detour_and_two_rails():
    assert graph.render(*_issue()) == SNAP_ISSUE


def test_box_queue_cycle():
    shown = _shown(QUEUE, ("intake", "queue"), loops={"queue": 2})
    assert graph.render(*shown) == SNAP_QUEUE


def test_box_stalled():
    shown = _shown(CYCLE, ("implement",), state="stalled", loops={"implement": 2},
                   last_output_tail={"test": "collected 3 items\nFAILED test_x\n"})
    assert graph.render(*shown) == SNAP_STALL


def test_box_short_linear_is_boxes_too():
    assert graph.render(*_shown(LINEAR, ("a",))) == SNAP_LINEAR


def test_box_human_ready_node_waits():
    task, t, rows = _issue()
    for nid in ("implement", "test", "review", "pr", "ci"):
        rows[nid]["status"] = "done"
    assert "┃ ⏸ merge" in graph.render(task, t, rows)


def test_box_folds_straight_runs_only_past_the_limit(monkeypatch):
    task, t, rows = _issue()
    assert graph.bounded(task, t, rows) == graph.render(task, t, rows)  # it fits: nothing folded
    monkeypatch.setattr(graph, "MAX_LINES", 35)
    text = graph.bounded(task, t, rows)
    assert graph._fits(text.splitlines())
    assert "┆ · pr → ci" in text  # review is an agent node: drawn, not folded
    # The ready node's neighbour, a skill node and a human node stay boxes.
    assert {"test", "merge"} <= set(graph.box_line_re().findall(text))
    assert graph.drawn_ids(text) == Counter(n["id"] for n in t["nodes"])


def test_box_long_graph_is_drawn_around_the_current_node():
    t = graph.parse(_big())
    ids = [n["id"] for n in t["nodes"]]
    task = {**TASK, "type": "big", "state": "active"}
    text = graph.bounded(task, t, _rows(t, tuple(ids[:20])))
    lines = text.splitlines()
    assert len(lines) <= graph.MAX_LINES and len(text) <= graph.MAX_CHARS
    assert "┃ ▶ step_au" in text and "[▶ step_au]" not in text
    assert "… 28 more nodes after (full view in wf_status)" in lines


def test_a_finished_graph_past_the_limit_summarises_instead_of_crashing():
    t = graph.parse(_big())
    ids = [n["id"] for n in t["nodes"]]
    task = {**TASK, "type": "big", "state": "done"}
    text = graph.bounded(task, t, _rows(t, tuple(ids)))
    lines = text.splitlines()
    assert len(lines) <= graph.MAX_LINES and len(text) <= graph.MAX_CHARS
    assert lines[1].startswith(f"✔ {len(ids)} done:")
    assert "current: no nodes left" in lines
    assert "┃" not in text

SNAP_ISSUE = """\
issue-resolution · demo#1 · task #1 active
        ┌──────────────────────────────┐
        │ ✔ claim                      │
        └───────────────┬──────────────┘
        ┌───────────────▼──────────────┐
 ┌─────►│ ✔ spec                       │
 │      └───────────────┬──────────────┘
 │      ┌───────────────▼──────────────┐
 ├───✗──│ ✔ scope_review 🤖recommended │── complex → plan ──┐
 │      └───────────────┬──────────────┘                    │
 │                      │                       ┌───────────▼──────────┐
 │                      │ simple(⊘)             │ ✔ plan 🤖recommended │
 │                      │                       └───────────┬──────────┘
 │      ┏━━━━━━━━━━━━━━━▼━━━━━━━━━━━━━━┓                    │
 │ ┌───►┃ ▶ implement 🤖recommended ↺1 ┃◄───────────────────┘  ◄ now
 └─┼─✗──┗━━━━━━━━━━━━━━━┯━━━━━━━━━━━━━━┛
   │    ┌───────────────▼──────────────┐
   ├─✗──│ · test                       │
   │    └───────────────┬──────────────┘
   │    ┌───────────────▼──────────────┐
   ├─✗──│ · review 🤖recommended       │
   │    └───────────────┬──────────────┘
   │    ┌───────────────▼──────────────┐
   │    │ · pr                         │
   │    └───────────────┬──────────────┘
   │    ┌───────────────▼──────────────┐
   ├─✗──│ · ci                         │
   │    └───────────────┬──────────────┘
   │    ┌───────────────▼──────────────┐
   ├─✗──│ · merge                      │
   │    └───────────────┬──────────────┘
   │    ┌───────────────▼──────────────┐   ┌─────────┐
   └─✗──│ · cleanup                    │──►│ · retro │
        └──────────────────────────────┘   └─────────┘
[plan] ─✗─► ↺ spec (max 3)
current: implement
✔ done ▶ ready · pending ⊘ skipped ⏸ waiting (external) ↺ back ✗ on fail ⛔ stall 🔧 skill ┆=folded"""


SNAP_QUEUE = """\
queue · demo#1 · task #1 active
      ┌────────────┐
      │ ✔ intake   │
      └──────┬─────┘
      ┌──────▼─────┐
 ┌───►│ ✔ queue ↺2 │
 │    └──────┬─────┘
 │    ┏━━━━━━▼━━━━━┓
 └─↺──┃ ▶ deliver  ┃  ◄ now ↺ more (max 5)
      ┗━━━━━━┯━━━━━┛ empty
      ┌──────▼─────┐   ┌─────────┐
      │ · report   │──►│ · retro │
      └────────────┘   └─────────┘
current: deliver
✔ done ▶ ready · pending ⊘ skipped ⏸ waiting (external) ↺ back ✗ on fail ⛔ stall 🔧 skill ┆=folded"""

SNAP_STALL = """\
cy · demo#1 · task #1 stalled
      ┌────────────────┐
 ┌───►│ ✔ implement ↺2 │
 │    └────────┬───────┘
 │    ┏━━━━━━━━▼━━━━━━━┓
 ├─✗──┃ ⛔ test        ┃  ◄ now
 │    ┗━━━━━━━━┯━━━━━━━┛
 │             │ └ FAILED test_x
 │    ┌────────▼───────┐   ┌─────────┐
 └─✗──│ · pr           │──►│ · retro │
      └────────────────┘   └─────────┘
current: test
✔ done ▶ ready · pending ⊘ skipped ⏸ waiting (external) ↺ back ✗ on fail ⛔ stall 🔧 skill ┆=folded"""

SNAP_LINEAR = """\
demo · demo#1 · task #1 active
┌─────┐
│ ✔ a │
└──┬──┘
┏━━▼━━┓   ┌─────────┐
┃ ▶ b ┃──►│ · retro │  ◄ now
┗━━━━━┛   └─────────┘
current: b
✔ done ▶ ready · pending ⊘ skipped ⏸ waiting (external) ↺ back ✗ on fail ⛔ stall 🔧 skill ┆=folded"""
