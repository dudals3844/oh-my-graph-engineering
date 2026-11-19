"""SQLite state for `wf` — stdlib only, because the hooks import it on every tool call.

One file, WAL mode: the Stop hook, the PreToolUse hook and the MCP server are
separate processes reading and writing the same rows concurrently.

The DB lives on local disk (`~/.local/share/omge/wf.db`), never NFS: SQLite's
locking is unreliable there and a hook that hangs on a lock hangs the session.
`WF_DB` overrides the path — tests use it so they never touch the real file.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import time
from pathlib import Path
from threading import Lock

from .i18n import t

#: An issue has at most one task in these states. `done`/`dropped` stay as history.
OPEN_STATES = ("active", "waiting_human", "stalled", "paused")
#: An owner silent this long no longer holds its task (SessionStart lists it).
OWNER_IDLE = 3600
#: An owner silent this long is gone: SessionStart drops the task.
OWNER_DROP = 7 * 24 * 3600
#: A grant counts for a task created up to this long after it: `wf_start`'s
#: PreToolUse grants just before the row exists. An older grant on the same issue
#: does not make a past session co-own a later task.
GRANT_SLACK = 60
#: A task with no issue is keyed `task:<id>` in `issue_url`, so ownership, the
#: one-open-task index and every lookup work unchanged.
NO_ISSUE = "task:"
SCHEMA_VERSION = 3
_DB_CREATE_LOCK = Lock()

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS task (
    id INTEGER PRIMARY KEY,
    type TEXT NOT NULL,
    dir TEXT NOT NULL,
    issue_url TEXT NOT NULL,
    version TEXT NOT NULL,
    graph TEXT NOT NULL,
    params TEXT NOT NULL,
    state TEXT NOT NULL,
    created REAL NOT NULL,
    updated REAL NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    new_reason TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS task_open_issue ON task(issue_url)
    WHERE state IN {OPEN_STATES!r};
CREATE TABLE IF NOT EXISTS node_state (
    task INTEGER NOT NULL REFERENCES task(id),
    node TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    last_exit INTEGER,
    last_output_tail TEXT,
    evidence TEXT,
    done_by TEXT,
    route TEXT,
    loops INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (task, node)
);
-- Keyed by issue (or `task:<id>`), not task id: the PreToolUse of `wf_start`
-- records the owner before the task row exists.
CREATE TABLE IF NOT EXISTS owner (
    session_id TEXT NOT NULL,
    issue_url TEXT NOT NULL,
    last_seen REAL NOT NULL,
    granted_at REAL NOT NULL,
    PRIMARY KEY (session_id, issue_url)
);
-- Sessions started under this wf. One missing began before it was installed and
-- still talks to the old wf-mcp process: it is not enforced until it restarts.
CREATE TABLE IF NOT EXISTS session (
    session_id TEXT PRIMARY KEY,
    registered INTEGER NOT NULL,
    noticed INTEGER NOT NULL DEFAULT 0,
    listed INTEGER NOT NULL DEFAULT 0
);
-- `task:<id>`, the session home for a dirty tree with no task, `calls:<session>`
-- (outward writes since the prompt) and `idle:<session>` (Stop on those).
CREATE TABLE IF NOT EXISTS stop_block (
    key TEXT PRIMARY KEY,
    streak INTEGER NOT NULL,
    signature TEXT NOT NULL
);
-- Every attempt, failure, waiver, skip, route and revision: what a retro reads.
CREATE TABLE IF NOT EXISTS event (
    task INTEGER NOT NULL REFERENCES task(id),
    ts REAL NOT NULL,
    node TEXT NOT NULL,
    kind TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    seconds REAL
);
-- An `Agent` launch's id, so a later SendMessage that resumes it can name its
-- subagent_type: the resume response carries the id, never the type.
CREATE TABLE IF NOT EXISTS agent_launch (
    agent_id TEXT PRIMARY KEY,
    subagent_type TEXT NOT NULL,
    ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS retro (
    task INTEGER PRIMARY KEY REFERENCES task(id),
    deviations TEXT NOT NULL,
    verdict TEXT NOT NULL,
    link TEXT
);
"""

#: v0 (worktree-keyed `instance`) → v1. Renames keep every row and its history.
_FROM_V0 = (
    "ALTER TABLE instance RENAME TO task",
    "ALTER TABLE task RENAME COLUMN worktree TO dir",
    "ALTER TABLE task RENAME COLUMN template_body TO graph",
    "ALTER TABLE task RENAME COLUMN template_sha256 TO version",
    "ALTER TABLE task ADD COLUMN title TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE task ADD COLUMN new_reason TEXT",
    "DROP INDEX IF EXISTS instance_open_worktree",
    "ALTER TABLE node_state RENAME COLUMN instance TO task",
    "ALTER TABLE node_state ADD COLUMN route TEXT",
    "ALTER TABLE node_state ADD COLUMN loops INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE retro RENAME COLUMN instance TO task",
    "ALTER TABLE stop_block RENAME COLUMN worktree TO key",
    "DELETE FROM stop_block",
    "DROP TABLE IF EXISTS session_touch",
    # A `new` that only ever owed its retro was the unlock-by-new misuse,
    # not unfinished work: off the ownerless list, not counted as done either.
    f"UPDATE task SET state = 'abandoned' WHERE type = 'new' AND state IN {OPEN_STATES!r} AND"
    " (SELECT GROUP_CONCAT(node) FROM node_state n WHERE n.task = task.id"
    " AND n.status = 'pending') = 'retro'",
    f"UPDATE task SET state = 'done' WHERE state IN {OPEN_STATES!r} AND NOT EXISTS"
    " (SELECT 1 FROM node_state n WHERE n.task = task.id AND n.status = 'pending')",
    f"UPDATE task SET state = 'dropped' WHERE state IN {OPEN_STATES!r} AND id < (SELECT MAX(id)"
    f" FROM task t WHERE t.issue_url = task.issue_url AND t.state IN {OPEN_STATES!r})",
)


class WfError(ValueError):
    """A refusal whose message is meant for the agent."""


def _migrate(conn: sqlite3.Connection) -> None:  # noqa: C901
    if conn.execute("PRAGMA user_version").fetchone()[0] >= SCHEMA_VERSION:
        return
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        # Re-read under the lock: another hook process may have just migrated.
        if conn.execute("PRAGMA user_version").fetchone()[0] >= SCHEMA_VERSION:
            return
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
        if "instance" in names and "task" not in names:
            for stmt in _FROM_V0:
                conn.execute(stmt)
        elif "instance" in names:
            # A wf-mcp started before v1 still runs the v0 schema on every connect
            # and re-creates these empty; renaming them over `task` jammed every
            # Stop. Empty ones are dropped; a non-empty one is left alone.
            for table in ("instance", "session_touch"):
                if table in names and not conn.execute(f"SELECT 1 FROM {table}").fetchone():
                    conn.execute(f"DROP TABLE {table}")
        for stmt in _SCHEMA.split(";"):
            if stmt.strip():
                conn.execute(stmt)
        # v1 → v2: whether the session was already shown the library listing.
        if "listed" not in {r[1] for r in conn.execute("PRAGMA table_info(session)")}:
            conn.execute("ALTER TABLE session ADD COLUMN listed INTEGER NOT NULL DEFAULT 0")
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def private_dir(path: Path) -> Path:
    """Plugin state holds task text and evidence: owner-only, whatever the umask."""
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)
    return path


def write_private(path: Path, text: str) -> None:
    private_dir(path.parent)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with open(fd, "w", encoding="utf-8") as f:
        f.write(text)


def connect() -> sqlite3.Connection:
    path = Path(os.environ.get("WF_DB") or Path.home() / ".local/share/omge/wf.db")
    private_dir(path.parent)
    # SQLite inherits the mode for -wal/-shm. Never open an existing DB here:
    # closing any fd for it releases this process's SQLite POSIX locks.
    # Serialize the first open/close so a peer thread cannot connect in between.
    with _DB_CREATE_LOCK:
        try:
            os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600))
        except FileExistsError:
            pass
    path.chmod(0o600)
    # Autocommit: every write here is a single statement or an explicit
    # `with conn:` block, and an implicit open transaction would hold the write
    # lock across a multi-minute check run.
    conn = sqlite3.connect(path, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        _migrate(conn)
    except BaseException:
        conn.close()
        raise
    return conn


def open_task(conn: sqlite3.Connection, issue_url: str) -> sqlite3.Row | None:
    return conn.execute(
        f"SELECT * FROM task WHERE issue_url = ? AND state IN {OPEN_STATES!r}", (issue_url,)
    ).fetchone()


def find(conn: sqlite3.Connection, issue_url: str | None, task: int | None
         ) -> sqlite3.Row | None:
    """The open task named by its issue or by its id."""
    row = open_task(conn, issue_url) if issue_url else task and get_task(conn, task)
    return row if row and row["state"] in OPEN_STATES else None


def has_issue(task: sqlite3.Row) -> bool:
    return not task["issue_url"].startswith(NO_ISSUE)


def git_identity(directory: str) -> dict[str, str] | None:
    """Worktree root (not common Git dir) and symbolic branch, never the live HEAD SHA."""
    # symbolic-ref, not rev-parse HEAD: it names an unborn branch too, so a fresh repo's
    # first commit matches the identity recorded before it; detached HEAD fails it.
    top = subprocess.run(["git", "-C", directory, "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True)
    branch = subprocess.run(["git", "-C", directory, "symbolic-ref", "--short", "-q", "HEAD"],
                            capture_output=True, text=True)
    if top.returncode or branch.returncode:
        return None
    return {"dir": str(Path(top.stdout.strip()).resolve()), "branch": branch.stdout.strip()}


def record_git_identity(conn: sqlite3.Connection, tid: int, directory: str) -> None:
    event(conn, tid, "@git", "git_identity", json.dumps(git_identity(directory)))


def create_task(conn: sqlite3.Connection, *, nodes: list[str], **fields) -> int:
    """`issue_url=None` keys the task by its own id."""
    now = time.time()
    fields = {**fields, "state": "active", "created": now, "updated": now}
    keyless = fields["issue_url"] is None
    fields["issue_url"] = fields["issue_url"] or ""
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        tid = conn.execute(
            f"INSERT INTO task ({', '.join(fields)}) VALUES ({', '.join('?' * len(fields))})",
            list(fields.values()),
        ).lastrowid
        if keyless:
            conn.execute("UPDATE task SET issue_url = ? WHERE id = ?", (f"{NO_ISSUE}{tid}", tid))
        record_git_identity(conn, tid, fields["dir"])
        conn.executemany("INSERT INTO node_state (task, node) VALUES (?, ?)",
                         [(tid, n) for n in nodes])
    return tid


def get_task(conn: sqlite3.Connection, tid: int) -> sqlite3.Row:
    return conn.execute("SELECT * FROM task WHERE id = ?", (tid,)).fetchone()


def set_state(conn: sqlite3.Connection, tid: int, state: str) -> None:
    conn.execute("UPDATE task SET state = ?, updated = ? WHERE id = ?", (state, time.time(), tid))


def update_task(conn: sqlite3.Connection, tid: int, **fields) -> None:
    if "dir" in fields:
        record_git_identity(conn, tid, fields["dir"])
    sets = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE task SET {sets}, updated = ? WHERE id = ?",
                 [*(json.dumps(v) if isinstance(v, dict) else v for v in fields.values()),
                  time.time(), tid])


def node_rows(conn: sqlite3.Connection, tid: int) -> dict[str, sqlite3.Row]:
    rows = conn.execute("SELECT * FROM node_state WHERE task = ?", (tid,)).fetchall()
    return {r["node"]: r for r in rows}


def set_node(conn: sqlite3.Connection, tid: int, node: str, **fields) -> None:
    sets = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE node_state SET {sets} WHERE task = ? AND node = ?",
                 [*fields.values(), tid, node])


def event(conn: sqlite3.Connection, tid: int, node: str, kind: str, detail: str = "",
          seconds: float | None = None) -> None:
    conn.execute("INSERT INTO event VALUES (?, ?, ?, ?, ?, ?)",
                 (tid, time.time(), node, kind, detail, seconds))


def note_launch(conn: sqlite3.Connection, agent_id: str, subagent_type: str) -> None:
    conn.execute("INSERT OR REPLACE INTO agent_launch VALUES (?, ?, ?)",
                 (agent_id, subagent_type, time.time()))


def launch_type(conn: sqlite3.Connection, agent_id: str) -> str | None:
    r = conn.execute("SELECT subagent_type FROM agent_launch WHERE agent_id = ?",
                     (agent_id,)).fetchone()
    return r[0] if r else None


def last_event(conn: sqlite3.Connection, tid: int, node: str) -> tuple[str, str] | None:
    r = conn.execute("SELECT kind, detail FROM event WHERE task = ? AND node = ?"
                     " ORDER BY rowid DESC LIMIT 1", (tid, node)).fetchone()
    return (r[0], r[1]) if r else None


def loops(conn: sqlite3.Connection, tid: int, src: str, to: str) -> int:
    """Times the edge `src` → `to` was taken back. Per edge: two nodes failing back to one
    target each have their own `max`. `node_state.loops` is the per-target total, drawn."""
    return conn.execute("SELECT COUNT(*) FROM event WHERE task = ? AND node = ? AND kind = 'back'"
                        " AND detail = ?", (tid, src, to)).fetchone()[0]


def retries(conn: sqlite3.Connection, tid: int, node: str) -> int:
    """Times `node` failed in place (`wf_fail` detail `retry:`). Same lifetime count as `loops`."""
    return conn.execute(
        "SELECT COUNT(*) FROM event WHERE task = ? AND node = ?"
        " AND kind = 'wf_fail' AND detail LIKE 'retry:%'", (tid, node)).fetchone()[0]


def record_attempt(conn: sqlite3.Connection, tid: int, node: str, exit_code: int, tail: str
                   ) -> None:
    conn.execute(
        "UPDATE node_state SET attempts = attempts + 1, last_exit = ?, last_output_tail = ?"
        " WHERE task = ? AND node = ?",
        (exit_code, tail, tid, node),
    )


def progress(conn: sqlite3.Connection, tid: int) -> None:
    """Closing a node is progress: clear the Stop streak and lift `stalled` — the
    PreToolUse write lock would otherwise outlive the event meant to release it."""
    set_stop_streak(conn, f"task:{tid}", 0, "")
    conn.execute("UPDATE task SET state = 'active', updated = ? WHERE id = ? AND state = 'stalled'",
                 (time.time(), tid))


def stop_streak(conn: sqlite3.Connection, key: str) -> tuple[int, str]:
    row = conn.execute("SELECT * FROM stop_block WHERE key = ?", (key,)).fetchone()
    return (row["streak"], row["signature"]) if row else (0, "")


def set_stop_streak(conn: sqlite3.Connection, key: str, streak: int, signature: str) -> None:
    if streak == 0:
        conn.execute("DELETE FROM stop_block WHERE key = ?", (key,))
    else:
        conn.execute("INSERT OR REPLACE INTO stop_block VALUES (?, ?, ?)", (key, streak, signature))


def own(conn: sqlite3.Connection, session_id: str, issue_url: str) -> None:
    now = time.time()
    conn.execute("INSERT OR REPLACE INTO owner VALUES (?, ?, ?, ?)",
                 (session_id, issue_url, now, now))


def register(conn: sqlite3.Connection, session_id: str) -> None:
    conn.execute("INSERT INTO session (session_id, registered) VALUES (?, 1)"
                 " ON CONFLICT(session_id) DO UPDATE SET registered = 1", (session_id,))


def registered(conn: sqlite3.Connection, session_id: str) -> bool:
    row = conn.execute("SELECT registered FROM session WHERE session_id = ?",
                       (session_id,)).fetchone()
    return bool(row and row[0])


def notice_once(conn: sqlite3.Connection, session_id: str) -> bool:
    """True the first time an unregistered session is asked about."""
    return conn.execute("INSERT INTO session (session_id, registered, noticed) VALUES (?, 0, 1)"
                        " ON CONFLICT(session_id)"
                        " DO NOTHING", (session_id,)).rowcount == 1


def count_call(conn: sqlite3.Connection, session_id: str) -> None:
    conn.execute("INSERT INTO stop_block VALUES (?, 1, '') ON CONFLICT(key)"
                 " DO UPDATE SET streak = streak + 1", (f"calls:{session_id}",))


def seen(conn: sqlite3.Connection, session_id: str) -> None:
    conn.execute("UPDATE owner SET last_seen = ? WHERE session_id = ?", (time.time(), session_id))


def owned(conn: sqlite3.Connection, session_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT t.* FROM task t JOIN owner o ON o.issue_url = t.issue_url"
        f" WHERE o.session_id = ? AND t.state IN {OPEN_STATES!r} AND o.granted_at >= t.created - ?"
        " ORDER BY t.id", (session_id, GRANT_SLACK)
    ).fetchall()


def ownerless(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        f"SELECT * FROM task WHERE state IN {OPEN_STATES!r} AND NOT EXISTS (SELECT 1 FROM owner o"
        " WHERE o.issue_url = task.issue_url AND o.last_seen > ?"
        " AND o.granted_at >= task.created - ?) ORDER BY id",
        (time.time() - OWNER_IDLE, GRANT_SLACK),
    ).fetchall()


def drop_abandoned(conn: sqlite3.Connection, *, except_ids: set[int]) -> None:
    """Drop tasks abandoned for `OWNER_DROP`. The `UPDATE` re-checks under the write
    lock, so an owner granted between the caller's read and here keeps its task."""
    held = (" AND EXISTS (SELECT 1 FROM owner o WHERE o.issue_url = task.issue_url"
            " AND o.granted_at >= task.created - :slack)")
    skip = "".join(f" AND id != :keep{i}" for i in range(len(except_ids)))
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        now = time.time()
        stale = conn.execute(
            f"UPDATE task SET state = 'dropped', updated = :now WHERE state IN {OPEN_STATES!r}"
            " AND NOT EXISTS (SELECT 1 FROM owner o WHERE o.issue_url = task.issue_url"
            " AND o.last_seen > :since AND o.granted_at >= task.created - :slack)"
            f"{held}{skip} RETURNING id",
            {"now": now, "since": now - OWNER_DROP, "slack": GRANT_SLACK,
             **{f"keep{i}": tid for i, tid in enumerate(except_ids)}},
        ).fetchall()
        for (tid,) in stale:
            event(conn, tid, "", "drop", t("store.orphan_drop"))


def get_retro(conn: sqlite3.Connection, tid: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM retro WHERE task = ?", (tid,)).fetchone()


def set_retro(conn: sqlite3.Connection, tid: int, deviations: str, verdict: str, link: str | None
              ) -> None:
    conn.execute("INSERT OR REPLACE INTO retro VALUES (?, ?, ?, ?)",
                 (tid, deviations, verdict, link))
