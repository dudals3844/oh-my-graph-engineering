"""The graph library — a local git repository the retro commits to — and what a
retro reads back out of the task history.

Packaged `workflows/` seed it: on first use all of them, later only a type the library
does not have yet — a library file is never overwritten, so after the seed the library
is the source and a retro's commit is in use from the next `wf_start` with no reinstall.
`WF_LIBRARY` overrides the path — tests use it.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import sqlite3
import statistics
import subprocess
from collections import Counter, defaultdict
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path

import yaml

from . import store
from .i18n import EN, t, variants
from .store import WfError

WORKFLOWS = Path(__file__).parent / "workflows"
#: `match` length in the brief listing every session starts with.
BRIEF_MATCH = 100
#: A check that holds a node to a comment naming the current HEAD: a review receipt.
HEAD_RECEIPT = re.compile(r"rev-parse HEAD[\s\S]*/comments")
IDENT = ["-c", "user.name=wf", "-c", "user.email=wf@localhost"]
#: `new` is refused when an existing type shares this much of its own wording with
#: the issue. ponytail: token overlap, not semantics — Korean particles glued to
#: words lower it; tune after the first retros.
SIMILAR = 0.5
SHARED_MIN = 2
#: A version comparison needs this many closed tasks on each side.
COMPARE_MIN = 3
WORSE_BY = 0.2
#: Task score in [0, 1] (plan §16): W_DONE for finishing, the rest for how cleanly —
#: each event below costs its weight, a run slower than the type's median up to W_SLOW.
#: Not `revise`: wf_revise reports a deviation, the retro's input — costing it rewards hiding.
W_DONE = 0.5
PENALTY = {"stall": 0.25, "waive": 0.1, "back": 0.1, "retry": 0.1}
W_SLOW = 0.2
#: Fitness or precision under this is a suggestion.
LOW_FIT = 0.8
#: A task still running is not scored, nor one dropped/abandoned — it never measured
#: the template.
UNSCORED = ("active", "waiting_human", "paused", "dropped", "abandoned")


def _git(*args: str, check: bool = True) -> str:
    r = subprocess.run(["git", "-C", str(root(seed=False)), *IDENT, *args],
                       capture_output=True, text=True)
    if check and r.returncode:
        raise WfError(t("library.git_failed", cmd=args[0], out=(r.stderr or r.stdout).strip()))
    return r.stdout


def _unseeded(path: Path) -> bool:
    # No HEAD, not merely no `.git`: a seed killed between init and commit retries.
    return not path.is_dir() or bool(subprocess.run(
        ["git", "-C", str(path), "rev-parse", "-q", "--verify", "HEAD"],
        capture_output=True).returncode)


def root(seed: bool = True) -> Path:
    path = Path(os.environ.get("WF_LIBRARY") or Path.home() / ".local/share/omge/library")
    if seed or path.is_dir():
        store.private_dir(path)
    if seed and (any(not (path / f.name).exists() for f in WORKFLOWS.glob("*.yaml"))
                 or _unseeded(path)):
        with _locked(path):
            fresh = _unseeded(path)
            new = sorted(f.name for f in WORKFLOWS.glob("*.yaml") if not (path / f.name).exists())
            for name in new:
                shutil.copy(WORKFLOWS / name, path)
            if fresh:
                _git("init", "-q")
                _git("add", "-A")
                _git("commit", "-q", "--allow-empty", "-m",
                     "wf library seed from packaged workflows")
            elif new:
                _git("add", *new)
                _git("commit", "-q", "-m", f"wf library seed: new types {', '.join(new)}"
                     " (packaged workflows, existing files untouched)")
    return path


@contextmanager
def _locked(path: Path):
    """An exclusive flock on the library: two retros committing at once would
    otherwise race on the index. Beside the repository, so `git add -A` never sees it."""
    with open(path.parent / f".{path.name}.lock", "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield


def types() -> dict[str, str]:
    return {p.stem: p.read_text(encoding="utf-8") for p in sorted(root().glob("*.yaml"))}


def body(type_: str, seed: bool = True) -> tuple[str, str] | None:
    """(graph body, version sha) of a library type, or None."""
    path = root(seed=seed) / f"{type_}.yaml"
    if not re.fullmatch(r"[a-z0-9-]+", type_) or not path.is_file():
        return None
    version = _git("log", "-1", "--format=%H", "--", path.name).strip()
    return path.read_text(encoding="utf-8"), version


def commit(type_: str, text: str, message: str) -> str | None:
    """Write and commit one type. None when the text is already there."""
    return _write({f"{type_}.yaml": text}, message)


def _write(changes: dict[str, str | None], message: str,  # noqa: C901
           expect: tuple[str, str] | None = None,
           before_write: Callable[[], None] | None = None,
           after_write: Callable[[str | None], None] | None = None) -> str | None:
    """Commit `changes` (library path -> text, None deletes) as one commit, or none —
    also none when `expect` (path, sha) no longer names that path's last commit."""
    lib = root()
    with _locked(lib):
        if before_write:
            before_write()
        if expect and _git("log", "-1", "--format=%H", "--", expect[0]).strip() != expect[1]:
            return None
        before = {p: (lib / p).read_text(encoding="utf-8") if (lib / p).exists() else None
                  for p in changes}
        touched = [p for p in changes if before[p] != changes[p]]
        if not touched:
            if after_write:
                try:
                    after_write(None)
                except Exception as error:
                    raise WfError(f"DB finalization failed: {error};"
                                  " no library commit created") from error
            return None
        try:
            for p in touched:
                if changes[p] is None:
                    (lib / p).unlink()
                else:
                    (lib / p).parent.mkdir(exist_ok=True)
                    (lib / p).write_text(changes[p], encoding="utf-8")
            _git("add", "-A", "--", *touched)
            _git("commit", "-q", "-m", message)
        except WfError:
            # Put the files back, so the library never holds an uncommitted type.
            for p in touched:
                if before[p] is None:
                    (lib / p).unlink(missing_ok=True)
                else:
                    (lib / p).write_text(before[p], encoding="utf-8")
            _git("reset", "-q", "--", *touched, check=False)
            raise
        sha = _git("rev-parse", "HEAD").strip()
        if after_write:
            try:
                after_write(sha)
            except Exception as error:
                try:
                    _git("revert", "--no-edit", sha)
                except Exception as compensation:
                    raise WfError(f"DB finalization failed: {error}; compensation failed:"
                                  f" {compensation}; partial state: library commit {sha}"
                                  " remains adopted; SQLite transaction rolled back") from error
                raise WfError(f"DB finalization failed: {error}; library commit {sha}"
                              " reverted; SQLite transaction rolled back") from error
        return sha


def _median(conn: sqlite3.Connection, type_: str) -> float:
    took = [r[0] for r in conn.execute(
        "SELECT updated - created FROM task WHERE type = ? AND state = 'done'", (type_,))]
    return statistics.median(took) if took else 0.0


def score(conn: sqlite3.Connection, task: sqlite3.Row, median: float) -> float:
    """Plan §16: W_DONE·done + (1 − W_DONE)·max(0, 1 − Σ weight·count − W_SLOW·slow).
    A retry `wf_fail` counts as `retry`; one to an earlier node already logs a `back`."""
    kinds = Counter("retry" if k == "wf_fail" and d.startswith("retry:") else k
                    for k, d in conn.execute("SELECT kind, detail FROM event WHERE task = ?",
                                             (task["id"],)))
    took = task["updated"] - task["created"]
    slow = min(1.0, max(0.0, took / median - 1)) if median else 0.0
    penalty = sum(w * kinds[k] for k, w in PENALTY.items()) + W_SLOW * slow
    return W_DONE * (task["state"] == "done") + (1 - W_DONE) * max(0.0, 1 - penalty)


def _stat(xs: list[float]) -> str:
    return f"{statistics.fmean(xs):.2f} ± {statistics.pstdev(xs):.2f} (n={len(xs)})" if xs \
        else t("library.stat_none")


def show(sha: str) -> str:
    return _git("show", "--format=%H%n%s", sha)[:4000]


def titles(conn: sqlite3.Connection, type_: str, exclude: int | None = None) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT title FROM task WHERE type = ? AND title != '' AND id IS NOT ?"
        " ORDER BY id DESC LIMIT 5", (type_, exclude))]


def listing(conn: sqlite3.Connection, brief: bool = False, query: str = "") -> str:
    """Every type and its `match`; `brief` (every session start) cuts the match and
    skips the past titles. A `query` keeps only the types whose name or `match` mentions
    it, with the full line — few matches, full detail."""
    q = query.casefold()
    lines = []
    for name, text in types().items():
        match = yaml.safe_load(text).get("match", "")
        if q and q not in f"{name}: {match}".casefold():
            continue
        if brief and len(match) > BRIEF_MATCH:
            match = match[:BRIEF_MATCH] + "…"
        lines.append(f"- {name}: {match}")
        if not brief and (past := titles(conn, name)):
            lines.append(t("library.past",
                           title=past[0][:60] + ("…" if len(past[0]) > 60 else "")))
    if not lines:
        return t("library.no_match", query=query) if query else t("library.empty")
    return "\n".join(lines)


def issue_text(issue_url: str) -> tuple[str, str]:
    """(title, title + body) over REST. Fails open to empty: matching is advice,
    and a `gh` outage must not stop a task from starting."""
    try:
        out = subprocess.run(
            ["gh", "api", "repos/" + issue_url.removeprefix("https://github.com/"),
             "--jq", '.title + "\\n" + (.body // "")'],
            capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return "", ""
    if out.returncode:
        return "", ""
    return out.stdout.partition("\n")[0], out.stdout


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"\w+", text.lower()) if len(w) > 1}


def similar(conn: sqlite3.Connection, text: str, exclude: int | None = None) -> list[str]:
    """Types whose `match` or a past task title is mostly contained in `text`. `exclude`
    leaves out the task under retro, whose own title would always match its type."""
    words = _tokens(text)
    hits = []
    for name, graph in types().items():
        g = yaml.safe_load(graph)
        for cand in [g.get("match", ""), *titles(conn, name, exclude)]:
            c = _tokens(cand)
            if len(words & c) >= SHARED_MIN and len(words & c) / len(c) >= SIMILAR:
                hits.append(name)
                break
    return hits


def _versions(conn: sqlite3.Connection, type_: str) -> list[str]:
    path = root() / f"{type_}.yaml"
    if not path.is_file():
        return []
    # A promotion commit names the candidate whose tasks ran the same text.
    log = _git("log", "--format=%H %(trailers:key=Candidate,valueonly,separator=%x20)",
               "--", path.name).splitlines()
    median = _median(conn, type_)
    lines, scored = [], []
    for label, line in zip((t("library.current"), t("library.previous")), log[:2], strict=False):
        shas = line.split()
        rows = conn.execute(f"SELECT * FROM task WHERE type = ? AND version IN"
                            f" ({','.join('?' * len(shas))})", (type_, *shas)).fetchall()
        ids = [r["id"] for r in rows]
        kinds = Counter(r[0] for r in conn.execute(
            f"SELECT kind FROM event WHERE task IN ({','.join('?' * len(ids))})", ids))
        stalled = conn.execute(
            f"SELECT COUNT(DISTINCT task) FROM event WHERE kind = 'stall' AND task IN"
            f" ({','.join('?' * len(ids))})", ids).fetchone()[0]
        done = [r for r in rows if r["state"] == "done"]
        hours = sum(r["updated"] - r["created"] for r in done) / len(done) / 3600 if done else 0
        scored.append([score(conn, r, median) for r in rows
                       if r["state"] not in UNSCORED])
        lines.append(t("library.version_score", label=label, sha=shas[0][:7],
                       stat=_stat(scored[-1])))
        lines.append(t(
            "library.version_stats", started=len(rows), done=len(done), stalled=stalled,
            waived=kinds['waive'],
            dev=(kinds['revise_add'] + kinds['revise_change']) / len(rows) if rows else 0,
            hours=hours))
    if len(scored) == 2 and min(map(len, scored)) >= COMPARE_MIN and (
            statistics.fmean(scored[0]) <= statistics.fmean(scored[1]) - WORSE_BY):
        sha = log[0].split()[0][:7]
        # Reverting a promotion would bring its candidate back too: drop it in the same commit.
        fix = (f"git -C {root()} revert --no-commit {sha} && git -C {root()} rm -q"
               f" candidates/{type_}.yaml && git -C {root()} commit -m 'revert {sha}'"
               if len(log[0].split()) > 1 else f"git -C {root()} revert {sha}")
        lines.append(t("library.worse", by=WORSE_BY, fix=fix))
    return lines


def _template_path(task: sqlite3.Row) -> str:
    own = f"{task['type']}.yaml"
    wrote = _git("diff-tree", "--no-commit-id", "--name-only", "-r", "--root", task["version"],
                 check=False).split()
    candidate = f"candidates/{own}"
    if own not in wrote and _git(
            "ls-tree", "--name-only", task["version"], "--", candidate, check=False):
        return candidate
    return own


def _template(conn: sqlite3.Connection, task: sqlite3.Row) -> list[dict]:
    """The nodes of the graph `task` started from. A submitted graph (no version) is its
    graph less what `wf_revise` added. A version commit that wrote `<type>.yaml` is the
    default's — even a revert whose tree also holds a candidate — else the candidate's
    if present."""
    if not task["version"]:
        added = {r[0] for r in conn.execute(
            "SELECT node FROM event WHERE task = ? AND kind = 'revise_add'", (task["id"],))}
        nodes = (yaml.safe_load(task["graph"]) or {}).get("nodes") or []
        return [n for n in nodes if n.get("id") not in added]
    text = _git("show", f"{task['version']}:{_template_path(task)}", check=False)
    return (yaml.safe_load(text).get("nodes") or []) if text else []


def prior_changes(conn: sqlite3.Connection, task: sqlite3.Row) -> list[str]:
    """Nodes changed by the library version this task started from."""
    if task["type"] == "new" or not task["version"]:
        return []
    path = _template_path(task)
    versions = _git("log", "-2", "--format=%H", task["version"], "--", path).split()
    if len(versions) < 2:
        return []
    previous = yaml.safe_load(_git("show", f"{versions[1]}:{path}", check=False)) or {}
    before = {n["id"]: n for n in previous.get("nodes") or []}
    after = {n["id"]: n for n in _template(conn, task)}
    return sorted(n for n in before.keys() | after.keys() if before.get(n) != after.get(n))


def conformance(conn: sqlite3.Connection, task: sqlite3.Row
                ) -> tuple[float, float, set[str]] | None:
    """(fitness, precision, unused template nodes), or None before any step. The path is
    the task's `done`/`route`/`back` events in order (retro aside). A `done` is explained
    when its node is in the template and its template predecessors that ever ran ran
    before it; a `route`/`back` when the template has that edge (`next`, or `on_fail`).
    A node a route skipped is the branch not taken, not an unused one."""
    tmpl = {n.get("id"): n for n in _template(conn, task) if n.get("id") != "retro"}
    steps = conn.execute("SELECT node, kind, detail FROM event WHERE task = ? AND node != 'retro'"
                         " AND kind IN ('done', 'route', 'back') ORDER BY rowid",
                         (task["id"],)).fetchall()
    if not steps or not tmpl:
        return None
    ran = {node for node, kind, _ in steps if kind == "done"}
    seen: set[str] = set()
    fit = 0
    for node, kind, to in steps:
        n = tmpl.get(node)
        if kind == "done":
            fit += n is not None and all(p in seen or p not in ran for p in n.get("after") or [])
            seen.add(node)
        else:
            fit += n is not None and (to in {e.get("to") for e in n.get("next") or []}
                                      or (kind == "back" and n.get("on_fail") == to))
    skipped = {node for (node,) in conn.execute(
        "SELECT node FROM event WHERE task = ? AND kind = 'skip'", (task["id"],))}
    unused = set(tmpl) - ran - skipped
    return fit / len(steps), 1 - len(unused) / len(tmpl), unused


def _detail(e: sqlite3.Row) -> str:
    """A `fail`'s detail with an unset-param error read the same in every language."""
    if e["kind"] == "fail":
        for v in variants("checks.param_unset"):
            pattern = re.escape(f"[omge:wf] {v}").replace(r"\{name\}", "(.+)")
            if m := re.fullmatch(pattern, e["detail"]):
                return "[omge:wf] " + EN["checks.param_unset"].format(name=m[1])
    return e["detail"]


def _tally(events: list) -> tuple[dict, Counter, Counter, dict]:
    """(kind, node, detail) -> task ids; (kind, node) counts; per-detail counts; seconds."""
    by = defaultdict(set)
    count, times, secs = Counter(), Counter(), defaultdict(list)
    for e in events:
        detail = _detail(e)
        by[e["kind"], e["node"], detail].add(e["task"])
        count[e["kind"], e["node"]] += 1
        times[e["kind"], e["node"], detail] += 1
        if e["seconds"] is not None:
            secs[e["node"]].append(e["seconds"])
    return by, count, times, secs


def evidence(conn: sqlite3.Connection, type_: str, task: sqlite3.Row | None = None) -> str:  # noqa: C901
    """Conformance first, then everything every past task of `type_` recorded, the
    rule-derived suggestions, and the template versions by score. A `new` task is its
    own type: other `new` tasks are unrelated work."""
    rows = ([task] if task else []) if type_ == "new" else conn.execute(
        "SELECT * FROM task WHERE type = ?", (type_,)).fetchall()
    tasks = [r["id"] for r in rows]
    fits = {r["id"]: c for r in rows if (c := conformance(conn, r))}
    lines = []
    if fits:
        fit, prec = (statistics.fmean(c[i] for c in fits.values()) for i in (0, 1))
        lines.append(t("library.fit", fit=fit, prec=prec, n=len(fits)))
    if task and (mine := conformance(conn, task)):
        lines.append(t("library.fit_mine", fit=mine[0], prec=mine[1],
                       score=score(conn, task, _median(conn, type_))))
    lines.append(t("library.type_header", type=type_, n=len(tasks)))
    events = conn.execute(f"SELECT * FROM event WHERE task IN ({','.join('?' * len(tasks))})"
                          " AND kind != 'git_identity'", tasks).fetchall()
    for e in events:
        if e["kind"] == "structural_review":
            verdicts = json.loads(e["detail"])["review"].get("prior_changes", [])
            if verdicts:
                lines.append(f"  task {e['task']} prior_changes: " + "; ".join(
                    f"{v['node']}: {v['verdict']} — {v['evidence']}" for v in verdicts))
    found = body(type_)
    # Suggestions judge the template as it is now: a failure a later version fixed is not
    # a defect of this one. The per-node lines keep the whole history.
    # The task being retro'd counts even if the template moved on while it ran.
    cur = {r["id"] for r in rows if not found or r["version"] == found[1]
           or (task and r["id"] == task["id"])}
    by, count, _, secs = _tally(events)

    def tasks_with(kind: str, node: str, by=by) -> set[int]:
        return set().union(*(ids for (k, n, _), ids in by.items() if k == kind and n == node))

    nodes = (yaml.safe_load(found[0]).get("nodes") or []) if found else []
    # A wait is checks.WAIT_EXIT on node_state, one row per node: it does not say which
    # attempt/fail events were the wait, so they stay in the tally.
    cby, ccount, ctimes, _ = _tally([e for e in events if e["task"] in cur])
    # A checked node without `on_fail` predates on_fail was required and reads as retry (graph.parse).
    retry = {n.get("id") for n in nodes
             if n.get("check") and n.get("id") != "retro" and n.get("on_fail", "retry") == "retry"}
    suggestions = []
    for node in sorted({n for _, n in count if n}):
        tried, failed = count["attempt", node], count["fail", node]
        waived, skipped = tasks_with("waive", node), tasks_with("skip", node)
        avg = t("library.node_avg", secs=sum(secs[node]) / len(secs[node])) if secs[node] else ""
        lines.append(t("library.node_line", node=node, failed=failed, tried=tried, avg=avg,
                       waived=len(waived), skipped=len(skipped),
                       back=len(tasks_with('back', node)),
                       revised=len(tasks_with('revise_change', node)),
                       wf_fail=count['wf_fail', node]))
        if node in retry and ccount["fail", node] >= 2:
            suggestions.append(t("library.s_retry", node=node, n=ccount['fail', node]))
        # Waivers only: a skip is a branch not taken, which is what branches are for.
        cwaived = tasks_with("waive", node, cby)
        if len(cwaived) >= 2 and len(cwaived) > len(cur) / 2:
            suggestions.append(t("library.s_waived", node=node, n=len(cwaived), total=len(cur)))
        if len(changed := tasks_with("revise_change", node, cby)) >= 2:
            suggestions.append(t("library.s_revised", node=node, n=len(changed)))
    for (kind, node, detail), ids in sorted(cby.items()):
        if kind == "back" and (len(ids) >= 2 or ctimes[kind, node, detail] >= 3):
            suggestions.append(t("library.s_back", node=node, detail=detail,
                                 times=ctimes[kind, node, detail], n=len(ids)))
        if len(ids) < 2:
            continue
        if kind == "fail":
            suggestions.append(t("library.s_fail", node=node, n=len(ids), detail=detail))
        elif kind == "revise_add":
            suggestions.append(t("library.s_promote", node=node, n=len(ids)))
    # A review receipt pinned to HEAD is bought again by any later commit.
    pinned = [n for n in nodes if HEAD_RECEIPT.search(str(n.get("check") or ""))]
    if bare := [n["id"] for n in pinned if not n.get("carry")]:
        suggestions.append(t("library.s_carry", nodes=bare))
    ids = {n["id"] for n in pinned}
    if chained := [n["id"] for n in pinned if ids & set(n.get("after") or [])]:
        suggestions.append(t("library.s_chain", nodes=chained))
    if cfits := [c for t, c in fits.items() if t in cur]:
        fit = statistics.fmean(c[0] for c in cfits)
    if cfits and fit < LOW_FIT:
        suggestions.append(t("library.s_fit", fit=fit))
    # A node an open or dropped task never reached is not yet run, not unneeded.
    states = {r["id"]: r["state"] for r in rows}
    if dfits := [c for t, c in fits.items() if t in cur and states[t] == "done"]:
        prec = statistics.fmean(c[1] for c in dfits)
    if dfits and prec < LOW_FIT:
        idle = Counter(n for c in dfits for n in c[2])
        rare = sorted(n for n, k in idle.items() if k > len(dfits) / 2)
        suggestions.append(t("library.s_idle", prec=prec, rare=rare or t("library.none")))
    lines.append(t("library.suggestions") if suggestions else t("library.no_suggestions"))
    lines += [f"  S{i}. {s}" for i, s in enumerate(suggestions, 1)]
    lines.append(t("library.versions"))
    lines += _versions(conn, type_) or [t("library.not_in_library")]
    return "\n".join(lines)
