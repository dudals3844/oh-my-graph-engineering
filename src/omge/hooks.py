"""Claude Code hook decisions, registered by the plugin as `omge hook <event>`.

Scope is decided here, per call: the session home is a git checkout. What a
session answers for is decided by ownership, never by path: a session owns the
tasks it named (`issue_url` or `task` id) to a `wf_*` tool, and the issueless
task its `wf_start` created.

Failure policy is asymmetric on purpose. Stop is the last line of defence and
fails CLOSED (any exception → exit 2). Every other event fails OPEN: a broken wf
must not stop every tool call in every session. The static matching in
PreToolUse is bypassable by design — Stop re-running the checks is what
actually enforces.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from . import checks, graph, i18n, store

#: Inside the hook's own `timeout: 600`, leaving room to report. Past it the
#: remaining ready checks count as failed — an unrun check never opens Stop.
STOP_BUDGET = 480
#: Command hooks default to a 60 s timeout; these only re-check `merge`-like
#: human nodes and fail open, so they get what fits under that.
CONTEXT_BUDGET = 50
STALL_AFTER = 3
#: Cap on the text Stop joins across blocking tasks — past it the tail is wf_status's.
STOP_TEXT_MAX = 4000
#: Outward writes since the user's prompt, with no open task, at which Stop wants
#: the work designed as a graph. Reads never count: a question may take many.
IDLE_CALLS = 1

WRITE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
#: A `wf_*` MCP tool under any server prefix.
WF_TOOL = re.compile(r"^mcp__.+__wf_\w+$")
#: The calls that make a session an owner — acting on a task, not reading it.
GRANTING = re.compile(r"__wf_(start|attach|complete|fail|revise|set|retro)$")
#: The calls after which the user must see the graph: freshly drawn, moved on by
#: `wf_complete`, or reopened by `wf_fail` — a stale `▶` reads as stuck .
SHOWN_TOOLS = re.compile(r"__wf_(start|attach|revise|complete|fail)$")
#: The id in a drawn graph's head line (`graph.render`): the first `task #<id>`.
TASK_ID = re.compile(r"task #(\d+)")
#: Matched in command position only, after `_code_only` has blanked quoted
#: strings and heredoc bodies: an issue body or commit message that *mentions*
#: `git commit` is not one (it blocked a real `gh api` call on 2026-10-09).
_AT_CMD = (r"(?:^|[;&|(\n`{!])\s*"
           r"(?:(?:then|do|else|sudo|env|time|command|exec|xargs|nohup)\s+(?:-\S+\s+)*"
           r"|\w+=\S*\s+)*")
GUARDED_BASH = re.compile(
    _AT_CMD + r"(?:git\s+(?:-[Cc]\s+\S+\s+|--\S+\s+)*(?:commit|push)\b|gh\s+pr\s+create\b)")
#: A shell command that changes something outside the checkout. Local edits
#: show in `git status`; reads change nothing. ponytail: matched at command position
#: only — a GraphQL mutation, `curl -d`, a wrapper (`timeout`, `bash -c`, `ssh`) or
#: flags before the subcommand slip through; widen if a real write is missed.
OUTWARD_BASH = re.compile(_AT_CMD + (
    r"(?:gh\s+\w+\s+(?:create|edit|comment|close|reopen|merge|delete|item-add|item-edit"
    r"|item-delete|review|ready|rerun|cancel|run|transfer|lock)\b"
    r"|(?:gh\s+api|curl)\b[^\n;&|]*(?:-X\s*|--method[\s=]+|--request[\s=]+)(?:POST|PUT|PATCH|DELETE)"
    r"|gh\s+api\s+(?!graphql\b)[^\n;&|]*\s(?:-f|-F|--field|--raw-field|--input)\b"
    r")"))
#: An MCP tool name part that writes somewhere; local plugin state tools are left out.
OUTWARD_MCP = {"write", "create", "add", "update", "delete", "submit",
               "publish", "send", "share", "trash", "copy",
               "respond", "edit", "post", "merge", "close", "upload", "batch", "remove",
               "register", "unregister", "finalize", "comment"}
HEREDOC = re.compile(r"(<<-?\s*(['\"]?)(\w+)\2)([^\n]*)\n.*?\n[ \t]*\3[ \t]*(?=\n|$)", re.S)
QUOTED = re.compile(r"'[^']*'|\"(?:\\.|[^\"\\])*\"")
LOCAL_BASH = re.compile(_AT_CMD + (
    r"(?:[\w./-]*/)?(?P<verb>cd|sed|perl|touch|mkdir|rm|cp|mv|install|tee|truncate"
    r"|python(?:\d+(?:\.\d+)?)?)"
    r"\b(?P<args>[^\n;&|<>]*)"))
REDIRECT = re.compile(r"(?<![<>\d])(?:\d*>>?|&>)(?![>&])\s*(?P<target>[^\s;&|]+)")
USER_COMMAND = re.compile(r"wf\s+(pause|waive|drop)\b\s*(.*)", re.DOTALL)
# ponytail: legacy CLI omits source; remove envelope fallback when source is always supplied.
INJECTED_PROMPT = re.compile(
    r"(?:Another Claude session sent a message(?: while you were working)?:[ \t]*\n"
    r"(?=<(?:cross-session-message|agent-message)[\s>])"
    r"|<(?:cross-session-message|agent-message|task-notification)(?=[\s>])"
    r"|\[SYSTEM NOTIFICATION - NOT USER INPUT\](?=\s|$))")
DROP = re.compile(r"#(\d+)\s+(\S.*)", re.DOTALL)
#: Writes here need no task: user config, and scratch (the session scratchpad
#: lives under /tmp).
EXEMPT_ROOTS = (Path.home().resolve() / ".claude", Path("/tmp").resolve())


def _git(cwd: str, *args: str) -> str | None:
    r = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def scope(cwd: str) -> str | None:
    """The checkout root, or None outside git."""
    return _git(cwd, "rev-parse", "--show-toplevel")


def signature(failed: dict[str, tuple[int, str]]) -> str:
    key = sorted((n, code, checks.normalise(checks.first_error_line(tail)))
                 for n, (code, tail) in failed.items())
    return hashlib.sha256(json.dumps(key).encode()).hexdigest()


def no_task_text(conn: sqlite3.Connection, session: str = "") -> str:  # noqa: ARG001
    """Automatic guidance stays short; types are an explicit wf_list_types read."""
    return i18n.t("hooks.no_task")


def _recheck_human(conn: sqlite3.Connection, task: sqlite3.Row, deadline: float) -> sqlite3.Row:
    """Re-run human nodes that have a check (e.g. merged); one passing ends `waiting_human`."""
    if checks.run_ready(conn, task, deadline, human_only=True)[1]:
        conn.execute("UPDATE task SET state = 'active' WHERE id = ? AND state = 'waiting_human'",
                     (task["id"],))
    return store.get_task(conn, task["id"])


def _name(task: sqlite3.Row) -> str:
    return task["issue_url"] if store.has_issue(task) else task["title"]


def _ready_line(conn: sqlite3.Connection, task: sqlite3.Row) -> str:
    ready = graph.ready(graph.parse(task["graph"]), store.node_rows(conn, task["id"]))
    return (f"omge:wf #{task['id']} {task['type']} state={task['state']} {_name(task)} ready: "
            + (", ".join(n["id"] for n in ready) or i18n.t("hooks.none")))


def session_start(conn: sqlite3.Connection, session: str) -> str:
    deadline = time.monotonic() + CONTEXT_BUDGET
    tasks = store.owned(conn, session) if session else []
    out = [graph.status_text(conn, _recheck_human(conn, t, deadline), listed=False)
           for t in tasks]
    if not tasks:
        out.append(no_task_text(conn, session))
    mine = {t["id"] for t in tasks}
    store.drop_abandoned(conn, except_ids=mine)
    if orphans := [t for t in store.ownerless(conn) if t["id"] not in mine]:
        # A count, not a list: every session paid for the whole list.
        out.append(i18n.t("hooks.orphans", count=len(orphans)))
    return "\n".join(out)


def _user_command(conn: sqlite3.Connection, task: sqlite3.Row, verb: str, rest: str) -> str:
    if verb == "pause":
        store.set_state(conn, task["id"], "paused")
        store.set_stop_streak(conn, f"task:{task['id']}", 0, "")
        return i18n.t("hooks.paused", id=task["id"])
    t = graph.parse(task["graph"])
    rows = store.node_rows(conn, task["id"])
    node_id, _, reason = rest.partition(" ")
    node_id, _, route = node_id.partition(">")
    if not node_id:
        return i18n.t("hooks.waive_usage", id=task["id"])
    node = next((n for n in t["nodes"] if n["id"] == node_id), None)
    if node is None or rows[node_id]["status"] != "pending":
        return i18n.t("hooks.not_pending", node=node_id)
    if not reason.strip():
        return i18n.t("hooks.reason_required")
    try:
        checks.close(conn, task, node_id, status="waived", done_by="user", evidence=reason.strip(),
                     route=route or None)
    except graph.WfError as exc:
        return f"omge:wf: {exc}"
    return i18n.t("hooks.waived_by_user", node=node_id, reason=reason.strip())


def _drop(conn: sqlite3.Connection, rest: str) -> str:
    """`wf drop #<id> <reason>`: give up any open task — an orphan included."""
    m = DROP.fullmatch(rest)
    task = m and store.get_task(conn, int(m[1]))
    if not task or task["state"] not in store.OPEN_STATES:
        return i18n.t("hooks.drop_usage")
    store.set_state(conn, task["id"], "dropped")
    store.event(conn, task["id"], "", "drop", m[2].strip())
    return f"omge:wf #{task['id']}: dropped — {m[2].strip()}"


def user_prompt(conn: sqlite3.Connection, session: str, prompt: str, *, human: bool = True) -> str:
    if not human:
        return ""
    m = USER_COMMAND.fullmatch(prompt.strip())
    if m and m[1] == "drop":
        return _drop(conn, m[2].strip())
    if session:
        store.set_stop_streak(conn, f"calls:{session}", 0, "")
        store.set_stop_streak(conn, f"idle:{session}", 0, "")
    tasks = store.owned(conn, session) if session else []
    if not tasks:
        return "" if m else no_task_text(conn, session)
    out = []
    if m:
        verb, rest = m[1], m[2].strip()
        if pick := re.match(r"#(\d+)\s*", rest):
            task = next((t for t in tasks if t["id"] == int(pick[1])), None)
            rest = rest[pick.end():]
        else:
            task = tasks[0] if len(tasks) == 1 else None
        out.append(_user_command(conn, task, verb, rest) if task else
                   i18n.t("hooks.many_tasks", ids=", ".join(f"#{t['id']}" for t in tasks), verb=verb))
    else:
        # A user who talks to a stalled session is the release: without this the
        # stall's write lock could only be lifted by `wf waive`, and a session
        # that cannot write cannot make the progress that would lift it either.
        for t in tasks:
            if t["state"] in ("paused", "stalled"):
                store.set_state(conn, t["id"], "active")
                store.set_stop_streak(conn, f"task:{t['id']}", 0, "")
    deadline = time.monotonic() + CONTEXT_BUDGET
    out += [_ready_line(conn, _recheck_human(conn, t, deadline))
            for t in store.owned(conn, session)]
    return "\n".join(out)


def _fill(text: str) -> str:
    return re.sub(r"[^\n]", "_", text)


def _code_only(cmd: str) -> str:
    """`cmd` with heredoc bodies and quoted strings overwritten, same length."""
    cmd = HEREDOC.sub(lambda m: m[1] + m[4] + _fill(m[0][len(m[1]) + len(m[4]):]), cmd)
    cmd = QUOTED.sub(lambda m: "_" * len(m[0]), cmd)
    return re.sub(r"(?m)(?<!\S)#.*$", lambda m: _fill(m[0]), cmd)


def _bash_writes(command: str, cwd: str) -> list[str | None]:  # noqa: C901
    """Known local mutations; None means a write whose target is not literal.

    ponytail: static command-position matching, not a shell sandbox. Wrappers,
    aliases and computed paths can escape; `cd` tracks simple command lists only.
    Python snippets/scripts are conservatively writes (even read-only ones),
    except the known pytest/ruff module runners; no interpreter side-effect inference.
    Single-line comparisons are reads; nested substitutions stay conservative.
    """
    code = _code_only(command)
    writes = []
    for comparison in re.finditer(r"\[\[[^\n]*?\]\]|\(\([^\n]*?\)\)", code):
        raw = command[comparison.start():comparison.end()]
        raw = QUOTED.sub(lambda m: _fill(m[0]) if m[0].startswith("'") else m[0], raw)
        if re.search(r"(?<!\\)(?:\$\(|`)", raw):
            writes.append(None)  # do not hide nested commands along with comparison operators
        code = code[:comparison.start()] + _fill(comparison[0]) + code[comparison.end():]
    operations = [(m.start(), m["verb"], command[m.start("args"):m.end("args")])
                  for m in LOCAL_BASH.finditer(code)]
    operations += [(m.start(), ">", command[m.start("target"):m.end("target")])
                   for m in REDIRECT.finditer(code)]
    for _, verb, raw in sorted(operations):
        try:
            args = shlex.split(raw, comments=True)
        except ValueError:
            writes.append(None)  # a restricted lane must not infer read-only from parse failure
            continue
        targets = []
        if verb == "cd":
            if args and not re.search(r"[$`*?]", args[0]):
                cwd = str(Path(cwd, os.path.expanduser(args[0])))
            continue
        if verb.startswith("python"):
            if args[:2] == ["-m", "pytest"] or (args[:3] == ["-m", "ruff", "check"]
                    and not any(a.startswith("--fix") for a in args)):
                continue
            targets = [None]
        elif verb in ("sed", "perl"):
            if any(re.match(r"-[A-Za-z]*i|--in-place(?:=|$)", a) for a in args):
                words, script, options = iter(args), False, True
                for arg in words:
                    if options and arg == "--":
                        options = False
                    elif options and (arg in ("-e", "-f", "--expression", "--file")
                                      or verb == "perl" and arg == "-E"):
                        script = True
                        next(words, None)
                    elif options and (arg.startswith(("-e", "-f", "--expression=", "--file="))
                                      or verb == "perl" and arg.startswith("-E")):
                        script = True
                    elif not (options and arg.startswith("-")):
                        if script:
                            targets.append(arg)
                        script = True
                targets = targets or [None]
        else:
            targets = [a for a in args if not a.startswith("-")]
            if verb in ("cp", "install"):
                for i, arg in enumerate(args):
                    if arg in ("-t", "--target-directory"):
                        targets = [args[i + 1] if i + 1 < len(args) else None]
                        break
                    if match := re.fullmatch(r"(?:-t|--target-directory=)(.+)", arg):
                        targets = [match[1]]
                        break
                else:
                    targets = targets[-1:]
            if verb != "tee":
                targets = targets or [None]
        for path in targets:
            if path in ("/dev/null", "/dev/stdout", "/dev/stderr"):
                continue
            writes.append(str(Path(cwd, os.path.expanduser(path)).resolve())
                          if path and not re.search(r"[$`*?]", path) else None)
    return writes


def _git_guard(conn: sqlite3.Connection, tasks: list[sqlite3.Row], command: str,  # noqa: C901
               cwd: str) -> str | None:
    """ponytail: direct literal shell commands only; wrappers/computed paths need a shell parser."""
    code = _code_only(command)
    commands = re.compile(_AT_CMD + r"(?P<verb>git|cd)\b(?P<args>[^\n;&|<>()`]*)")
    for match in commands.finditer(code):
        try:
            args = shlex.split(command[match.start("args"):match.end("args")], comments=True)
        except ValueError:
            continue
        if args and args[-1].isdigit() and command[match.end("args"):][:1] in ("<", ">") \
                and command[match.start("args"):match.end("args")].endswith(" " + args[-1]):
            args.pop()  # `2>&1`: the fd number belongs to the redirection, not to git
        if match["verb"] == "cd":
            while args and args[0] in ("--", "-L", "-P"):
                option = args.pop(0)
                if option == "--":
                    break
            if args and not re.search(r"[$`*?]", args[0]):
                cwd = str(Path(cwd, os.path.expanduser(args[0])).resolve())
            continue
        target = cwd
        i = 0
        while i < len(args) and args[i].startswith("-"):
            arg = args[i]
            if arg in ("-C", "-c", "--git-dir", "--work-tree"):
                if i + 1 == len(args):
                    break
                if arg == "-C":
                    target = str(Path(target, os.path.expanduser(args[i + 1])).resolve())
                elif arg in ("--git-dir", "--work-tree"):
                    target = ""  # alternate Git identities are not the task checkout
                i += 2
            else:
                if arg.startswith("-C"):
                    target = str(Path(target, os.path.expanduser(arg[2:])).resolve())
                elif arg.startswith(("--git-dir=", "--work-tree=")):
                    target = ""
                i += 1
        words = args[i:]
        if not words:
            continue
        verb, rest = words[0], words[1:]
        if verb == "stash" and (not rest or rest[0] == "pop" or (
                rest[0].startswith("-") and rest[0] not in ("--help", "-h"))):
            return i18n.t("hooks.stash")
        if (verb == "branch" and any(a in ("--delete", "-d", "-D") or
                                    re.fullmatch(r"-[drD]*[dD][drD]*", a) for a in rest)
                and any(a.startswith(("archive/", "refs/heads/archive/")) or "/archive/" in a
                        for a in rest if not a.startswith("-"))):
            return i18n.t("hooks.archive")
        positional, options, skip = [], [], False
        if verb == "push":
            for arg in rest:
                if skip:
                    skip = False
                elif arg in ("-o", "--push-option", "--repo", "--receive-pack", "--exec"):
                    options.append(arg)
                    skip = True
                elif arg.startswith("-"):
                    options.append(arg)
                else:
                    positional.append(arg)
            deleting = any(a in ("--delete", "-d") for a in options)
            if any((deleting and a.startswith(("archive/", "refs/heads/archive/"))) or
                   a.lstrip("+").startswith((":archive/", ":refs/heads/archive/")) for a in rest):
                return i18n.t("hooks.archive")
        if verb == "worktree" and rest[:1] == ["remove"]:
            for arg in rest[1:]:
                if arg.startswith("-") or re.search(r"[$`*?]", arg):
                    continue
                path = Path(target, os.path.expanduser(arg)).resolve()
                listed = subprocess.run(["git", "-C", target, "worktree", "list", "--porcelain"],
                                        capture_output=True, text=True)
                worktrees = [Path(line.removeprefix("worktree ")).resolve()
                             for line in listed.stdout.splitlines() if line.startswith("worktree ")]
                if path not in worktrees and not Path(arg).is_absolute():
                    suffix = Path(arg).parts
                    matches = [p for p in worktrees if p.parts[-len(suffix):] == suffix]
                    if len(matches) == 1:
                        path = matches[0]
                mine = {t["id"] for t in tasks}
                for task in conn.execute(
                        f"SELECT * FROM task WHERE state IN {store.OPEN_STATES!r}"):
                    if task["id"] not in mine and Path(task["dir"]).resolve().is_relative_to(path):
                        return i18n.t("hooks.worktree")
        if verb not in ("commit", "push"):
            continue
        active = [t for t in tasks if t["state"] != "paused"]
        if not active:
            if not tasks:
                return no_task_text(conn)
            continue
        actual = store.git_identity(target) if target else None
        for task in active:
            row = conn.execute("SELECT detail FROM event WHERE task = ? AND kind = 'git_identity'"
                               " ORDER BY rowid DESC LIMIT 1", (task["id"],)).fetchone()
            expected = json.loads(row[0]) if row else None
            if actual and expected and actual == expected:
                if verb == "push":
                    if any(a in ("--all", "--mirror", "--tags") for a in rest):
                        return i18n.t("hooks.push_one")
                    # The first positional word is the remote; the rest are refspecs.
                    branch = expected["branch"]
                    refs = positional[1:]
                    if not refs:
                        return i18n.t("hooks.push_refspec", branch=branch)
                    for ref in refs:
                        source, sep, destination = ref.lstrip("+").partition(":")
                        ref_delete = (deleting and not sep) or (sep and not source)
                        if ref_delete:
                            deleted = (destination or source).removeprefix("refs/heads/")
                            if deleted != branch:
                                return i18n.t("hooks.push_mismatch", branch=branch)
                        elif (source.removeprefix("refs/heads/") not in ("HEAD", branch)
                                or destination.removeprefix("refs/heads/") != branch):
                            return i18n.t("hooks.push_mismatch", branch=branch)
                break
        else:
            return i18n.t("hooks.identity")
    return None


def _exempt(path: str) -> bool:
    if not path:
        return False
    p = Path(path).resolve()
    if any(p.is_relative_to(root) for root in EXEMPT_ROOTS):
        return True
    # Git-ignored files (`.omc/` handoffs, local state) are not the work.
    d = p.parent
    while not d.exists() and d != d.parent:  # a Write may create the directory
        d = d.parent
    return subprocess.run(["git", "-C", str(d), "check-ignore", "-q", str(p)],
                          capture_output=True).returncode == 0


def pre_tool_use(conn: sqlite3.Connection, session: str, tool: str, tool_input: dict,  # noqa: C901
                 in_agent: bool = False, cwd: str | None = None) -> str | None:
    """Deny reason, or None to allow. Also where ownership is recorded: the MCP
    server never learns which session called it. `in_agent`: a subagent's call."""
    if WF_TOOL.match(tool):
        issue = tool_input.get("issue_url")
        if not issue and (task := store.find(conn, None, _id(tool_input.get("task")))):
            issue = task["issue_url"]  # the key, `task:<id>` for an issueless task
        # `wf_start` on an issue that already has a task is refused; it grants nothing.
        # An issueless `wf_start` has no key yet: PostToolUse grants from its result.
        if issue and session and GRANTING.search(tool) and not (
                tool.endswith("__wf_start") and store.open_task(conn, issue)):
            _own(conn, session, issue)
        return None
    cmd = _code_only(tool_input.get("command", "")) if tool == "Bash" else ""
    if tool == "Skill" and session and (
            msg := _early_skill(conn, session, tool_input.get("skill", ""))):
        return msg
    store.seen(conn, session)
    # Only outward writes count as work, a subagent's too: delegating is no way round.
    if session and _outward(tool, cmd) and not store.owned(conn, session):
        store.count_call(conn, session)
    tasks = store.owned(conn, session) if session else []
    if tool == "Bash" and (reason := _git_guard(
            conn, tasks, tool_input.get("command", ""), cwd or os.getcwd())):
        return reason
    if tool in WRITE_TOOLS:
        paths = [tool_input.get("file_path") or tool_input.get("notebook_path") or None]
    elif tool == "Bash":
        # No-task local edits still fall to Stop's dirty-tree check.
        paths = _bash_writes(tool_input.get("command", ""), cwd or os.getcwd()) if tasks else []
        if GUARDED_BASH.search(cmd):
            paths.append(None)
    else:
        return None
    paths = [p for p in paths if p is None or not _exempt(p)]
    if not paths:
        return None
    if tasks and all(t["state"] == "paused" for t in tasks):
        return None
    if any(t["state"] != "stalled" for t in tasks):
        return None if in_agent else _delegated(conn, tasks)
    if tasks:
        return i18n.t("hooks.stalled_write")
    return no_task_text(conn, session)


def _outward(tool: str, cmd: str) -> bool:
    if tool.startswith("mcp__plugin_"):
        return False
    if tool.startswith("mcp__"):
        return bool(OUTWARD_MCP & set(re.split(r"[_-]", tool.rsplit("__", 1)[-1])))
    return bool(OUTWARD_BASH.search(cmd))


def _early_skill(conn: sqlite3.Connection, session: str, name: str) -> str | None:
    """A Skill call counts only once its node is ready: refuse one that would not."""
    early = []
    for t in store.owned(conn, session):
        if t["state"] == "paused":
            continue
        g, rows = graph.parse(t["graph"]), store.node_rows(conn, t["id"])
        ready = {n["id"] for n in graph.ready(g, rows)}
        for n in graph.pending(g, rows):
            if name in n.get("skill", ()):
                if n["id"] in ready:
                    return None
                early.append((t["id"], n, rows))
    if not early:
        return None
    tid, n, rows = early[0]
    waiting = ", ".join(a for a in n["after"] if rows[a]["status"] == "pending")
    return i18n.t("hooks.early_skill", name=name, tid=tid, node=n["id"], waiting=waiting)


def _delegated(conn: sqlite3.Connection, tasks: list[sqlite3.Row]) -> str | None:
    """A ready `agent_mode: require` node's work is a subagent's: the main session may not
    write while one is ready. A paused task enforces nothing."""
    need = [f"#{t['id']} `{n['id']}` → {' | '.join(n['agent'])}"
            for t in tasks if t["state"] not in ("paused", "stalled")
            for n in graph.ready(graph.parse(t["graph"]), store.node_rows(conn, t["id"]))
            if n.get("agent_mode") == "require"]
    return i18n.t("hooks.delegated", need=", ".join(need)) if need else None


def _own(conn: sqlite3.Connection, session: str, key: str) -> None:
    """Own a task. Calls before it were the work of designing it, not work without one."""
    store.own(conn, session, key)
    store.set_stop_streak(conn, f"calls:{session}", 0, "")


def _id(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _scope_hint(conn: sqlite3.Connection, session: str, signal: str, called: str = ""
                ) -> dict | None:
    tasks = []
    for task in store.owned(conn, session):
        if task["state"] not in ("active", "waiting_human"):
            continue
        ready = graph.ready(graph.parse(task["graph"]), store.node_rows(conn, task["id"]))
        tasks.append((task, ready))
    if not tasks or (signal == "agent" and any(called in n.get("agent", ())
                                               for _, ready in tasks for n in ready)):
        return None
    changed = False
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        for task, ready in tasks:
            revision = conn.execute(
                "SELECT MAX(rowid) FROM event WHERE task = ? AND kind = 'revise'",
                (task["id"],)).fetchone()[0]
            stage = [task["graph"], revision, sorted(n["id"] for n in ready), signal]
            key = hashlib.sha256(json.dumps(stage).encode()).hexdigest()
            if not conn.execute("SELECT 1 FROM event WHERE task = ? AND kind = 'scope_hint'"
                                " AND detail = ?", (task["id"], key)).fetchone():
                store.event(conn, task["id"], "", "scope_hint", key)
                changed = True
    if changed:
        text = i18n.t("hooks.scope_path" if signal == "path" else "hooks.scope_agent")
        return {"hookSpecificOutput": {
            "hookEventName": "PreToolUse" if signal == "path" else "PostToolUse",
            "additionalContext": text}}
    return None


def _write_hint(conn: sqlite3.Connection, session: str, tool: str, tool_input: dict,
                cwd: str) -> dict | None:
    tasks = store.owned(conn, session) if session else []
    if not tasks:
        return None
    paths = ([tool_input.get("file_path") or tool_input.get("notebook_path")]
             if tool in WRITE_TOOLS else
             _bash_writes(tool_input.get("command", ""), cwd) if tool == "Bash" else [])
    if any(p and not _exempt(p) and not any(
            Path(p).resolve().is_relative_to(Path(t["dir"]).resolve()) for t in tasks)
            for p in paths):
        return _scope_hint(conn, session, "path")
    return None


def _payload(value: object) -> dict | None:
    """A tool call's response dict, unwrapped from JSON text or a content block."""
    if isinstance(value, str):
        try:
            return _payload(json.loads(value))
        except (ValueError, TypeError):
            return None
    if isinstance(value, list):
        return next((d for d in (_payload(v) for v in value) if d), None)
    if isinstance(value, dict):
        if "text" in value and not ({"success", "agentId"} & value.keys()):
            return _payload(value["text"])
        return value
    return None


def _resumed(conn: sqlite3.Connection, response: object) -> str | None:
    """The subagent_type of an agent a `SendMessage` resumed. Only a successful
    resume says so: `resumedAgentId` arrives in the response envelope, and the type was
    recorded when the Agent launched it. Echoed JSON in a message body is not a resume."""
    body = _payload(response)
    if not body or body.get("success") is not True:
        return None
    aid = body.get("resumedAgentId")
    return store.launch_type(conn, aid) if isinstance(aid, str) else None


def _texts(value: object):
    """Response text, walked like `accepted`: a str, a list, or a dict's text/content/result."""
    if isinstance(value, str):
        yield value
        try:
            yield from _texts(json.loads(value))
        except (ValueError, TypeError):
            return
    elif isinstance(value, list):
        for v in value:
            yield from _texts(v)
    elif isinstance(value, dict):
        for key in ("text", "content", "result"):
            if key in value:
                yield from _texts(value[key])


def post_tool_use(conn: sqlite3.Connection, session: str, tool: str, tool_input: dict,  # noqa: C901
                  response: object = None, in_agent: bool = False,
                  agent_id: str | None = None) -> dict | None:
    """The hook output: the drawn graph after start/attach/revise/complete/fail, for the
    user — not left to the model. A `Skill` call (a subagent's arrives with the parent's
    session) is recorded on the ready `skill:` nodes naming it, in the session's tasks;
    an `Agent` launch on the ready `agent:` nodes naming its subagent_type."""
    if (tool == "wf_retro" or tool.endswith("__wf_retro")) and (
            review := tool_input.get("structural_review")) is not None:
        task = store.find(conn, tool_input.get("issue_url"), _id(tool_input.get("task")))
        if not task or not in_agent or not agent_id or not session:
            return None
        try:
            ack = graph.structural_ack(conn, task, review)
        except graph.WfError:
            return None
        # MCP content may be plain text, a content list, or CallToolResult JSON.
        def accepted(value: object) -> bool:
            if isinstance(value, str):
                try:
                    return accepted(json.loads(value))
                except (ValueError, TypeError):
                    return False
            if isinstance(value, list):
                return any(accepted(v) for v in value)
            if isinstance(value, dict) and not (
                    value.get("isError") or value.get("is_error") or value.get("error")
                    or value.get("type") == "error"):
                return (value == ack or accepted(value.get("result"))
                        or accepted(value.get("content"))
                        or (value.get("type") == "text" and accepted(value.get("text"))))
            return False
        if accepted(response):
            store.event(conn, task["id"], "retro", "structural_review", json.dumps(
                {**ack, "review": review, "agent_id": agent_id, "session": session},
                ensure_ascii=False))
        return None
    if tool == "AskUserQuestion" and session and not in_agent:
        return _scope_hint(conn, session, "question")
    calls = {"Skill": ("skill", tool_input.get("skill", "")),
             "Agent": ("agent", tool_input.get("subagent_type") or "general-purpose")}
    if tool == "SendMessage" and (resumed := _resumed(conn, response)):
        calls["SendMessage"] = ("agent", resumed)
    if tool in calls and session:
        field, called = calls[tool]
        name = f"{called}{' (agent)' if in_agent else ''}"
        # Any subagent but a fork (which inherits the parent's context) is independent.
        reviewer = tool in ("Agent", "SendMessage") and called != "fork"
        verdict = (graph.verdict("\n".join(_texts(response)))
                   if tool == "Agent" and not in_agent else None)
        if tool == "Agent":
            aid = (_payload(response) or {}).get("agentId")
            if isinstance(aid, str):
                store.note_launch(conn, aid, called)
        for task in store.owned(conn, session):
            for n in graph.ready(graph.parse(task["graph"]), store.node_rows(conn, task["id"])):
                # Only a call the node names opens its gate. A reviewer's call is also
                # the receipt wf_waive checks, on every ready node.
                if reviewer:
                    store.event(conn, task["id"], n["id"], "reviewer", name)
                if called in n.get(field, ()):
                    store.event(conn, task["id"], n["id"], field, name)
                if verdict and (reviewer or called in n.get(field, ())):
                    store.event(conn, task["id"], n["id"], "review", json.dumps(
                        {"agent": called, "verdict": verdict[0], "sha": verdict[1],
                         "head": _git(task["dir"], "rev-parse", "HEAD")},
                        ensure_ascii=False))
        if tool == "Agent" and not in_agent:
            return _scope_hint(conn, session, "agent", called)
        return None
    if not SHOWN_TOOLS.search(tool) or not WF_TOOL.match(tool):
        return None
    task = store.find(conn, tool_input.get("issue_url"), _id(tool_input.get("task")))
    if task is None and tool.endswith("__wf_start") and not tool_input.get("issue_url"):
        # Only the result knows an issueless task's id. Only a task created just now,
        # with no issue, is granted — a stray `task #<id>` in a title is not a claim.
        m = TASK_ID.search(json.dumps(response, ensure_ascii=False))
        if m is None:
            return {"hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": i18n.t("hooks.no_task_id")}}
        task = store.find(conn, None, int(m[1]))
        if not task or store.has_issue(task) or task["created"] < time.time() - store.GRANT_SLACK:
            return None
    if task is None:
        return None
    # Granted again after success: the PreToolUse grant can lose a race with
    # another session's `wf_start` on the same issue, or predate the row too much.
    if session and not tool.endswith("__wf_revise"):
        _own(conn, session, task["issue_url"])
    if tool.endswith("__wf_revise"):
        text = graph.revise_text(conn, task)
    else:
        text = graph.bounded(task, graph.parse(task["graph"]), store.node_rows(conn, task["id"]))
    return {"systemMessage": text}


def _bump(conn: sqlite3.Connection, key: str, sig: str) -> int:
    streak, prev = store.stop_streak(conn, key)
    streak = streak + 1 if sig == prev else 1
    store.set_stop_streak(conn, key, streak, sig)
    return streak


def _no_task_stop(conn: sqlite3.Connection, home: str, session: str) -> tuple[int, str]:
    # Submodule gitlink drift is a superproject checkout's resting state.
    status = _git(home, "status", "--porcelain", "--ignore-submodules=all")
    # A session that finished a task did its work under a graph; a tree it leaves dirty
    # (or found dirty — a `uv run` lockfile) is not untracked work.
    if status and session and conn.execute(
            "SELECT 1 FROM owner o JOIN task t ON t.issue_url = o.issue_url"
            " WHERE o.session_id = ? AND t.state = 'done'", (session,)).fetchone():
        status = ""
    if not status:
        store.set_stop_streak(conn, home, 0, "")
        return _idle_stop(conn, session)
    # The same dirty tree three Stops running: the agent is not going to start a
    # task; let it go once with a note rather than loop.
    streak = _bump(conn, home, hashlib.sha256(status.encode()).hexdigest())
    if streak < STALL_AFTER:
        return 2, no_task_text(conn, session)
    return 0, (i18n.t("hooks.dirty_release", count=STALL_AFTER)
               if streak == STALL_AFTER else "")


def _idle_stop(conn: sqlite3.Connection, session: str) -> tuple[int, str]:
    """Work done with no graph: IDLE_CALLS outward writes since the prompt and no task.
    Same three-in-a-row release as the dirty tree; a new call is a new state."""
    calls = store.stop_streak(conn, f"calls:{session}")[0] if session else 0
    if calls < IDLE_CALLS:
        store.set_stop_streak(conn, f"idle:{session}", 0, "")
        return 0, ""
    streak = _bump(conn, f"idle:{session}", str(calls))
    if streak < STALL_AFTER:
        return 2, i18n.t("hooks.idle_block", calls=calls) + no_task_text(conn, session)
    return 0, (i18n.t("hooks.idle_release", count=STALL_AFTER)
               if streak == STALL_AFTER else "")


def stop(  # noqa: C901
    conn: sqlite3.Connection, task: sqlite3.Row, deadline: float, wide: bool = False
) -> tuple[int, str]:
    """(exit code, message) for one owned task: 2 blocks with the message, 0 allows.

    `wide`: another owned task may block in the same Stop, so node tails shorten
    to their first line — the full ones stay in wf_status."""
    key = f"task:{task['id']}"
    if task["state"] == "paused":
        return 0, ""
    failed, _ = checks.run_ready(conn, task, deadline)
    task = store.get_task(conn, task["id"])
    t = graph.parse(task["graph"])
    rows = store.node_rows(conn, task["id"])
    if not graph.pending(t, rows):
        store.set_state(conn, task["id"], "done")
        store.set_stop_streak(conn, key, 0, "")
        return 0, ""
    blocking = [n for n in graph.ready(t, rows) if n.get("kind") != "human"]
    if not blocking:
        store.set_state(conn, task["id"], "waiting_human")
        store.set_stop_streak(conn, key, 0, "")
        return 0, ""
    # Every non-human ready node has a check and was just attempted, unless a
    # concurrent writer closed/reopened something in between.
    fails = {n["id"]: failed.get(n["id"], (1, i18n.t("hooks.concurrent"))) for n in blocking}
    waiting = {nid for nid, (code, _) in fails.items() if code == checks.WAIT_EXIT}
    # A check waiting on external CI is not stuck, and a check the budget never
    # reached says nothing either: both block but stay out of the stall signature.
    sig = signature({k: v for k, v in fails.items()
                     if v[1] != i18n.t("checks.unrun") and k not in waiting})
    if task["state"] == "stalled" and store.stop_streak(conn, key)[1] in (sig, checks.EXHAUSTED):
        return 0, ""  # already announced; a changed signature is progress and resumes below
    parts = [i18n.t("hooks.stop_head", id=task["id"], name=_name(task))]
    for n in blocking:
        code, tail = fails[n["id"]]
        parts.append(f"- `{n['id']}` exit={code}")
        # Waiting nodes, several nodes or several tasks: one line each — the full
        # tail stays in wf_status.
        parts.append(checks.first_error_line(tail)
                     if n["id"] in waiting or wide or len(blocking) > 1 else tail)
        if n["id"] in waiting:
            continue  # a wait takes no on_fail advice and no unset note
        if note := graph.unset_note(t, n, json.loads(task["params"])):
            parts.append(note)
        if n.get("on_fail", graph.RETRY) != graph.RETRY:
            parts.append(i18n.t("hooks.on_fail", node=n["id"], to=n["on_fail"]))
    # The `do` and pitfalls stay out: the user sees this text too.
    parts.append(i18n.t("hooks.see_status"))
    streak = _bump(conn, key, sig)
    # Only the retro left and already asked for once: the agent is waiting on the user
    # (a waive) or chose to stop; blocking again only re-reads the context.
    if [n["id"] for n in blocking] == ["retro"] and streak >= 2:
        return 0, (i18n.t("hooks.retro_only", id=task["id"])
                   if streak == 2 else "")
    if len(waiting) == len(blocking):
        # Every blocking check waits on something external (CI): a wait never stalls.
        # Block once to ask for a background watcher, then let its completion
        # notification wake the session — blocking again only re-reads the context.
        if streak >= 2:
            return 0, i18n.t("hooks.ci_release", id=task["id"]) if streak == 2 else ""
        store.set_state(conn, task["id"], "active")
        parts.append(i18n.t("hooks.ci_wait"))
        return 2, "\n".join(parts)
    if streak >= STALL_AFTER:
        store.set_state(conn, task["id"], "stalled")
        store.event(conn, task["id"], "", "stall", sig)
        return 2, i18n.t("hooks.stalled", count=STALL_AFTER, id=task["id"])
    store.set_state(conn, task["id"], "active")
    parts[0] = parts[0][:-2] + i18n.t("hooks.streak", streak=streak, max=STALL_AFTER)
    parts.append(i18n.t("hooks.ask_hint") + i18n.t("hooks.pause_hint"))
    return 2, "\n".join(parts)


def _stop_event(conn: sqlite3.Connection, home: str, session: str, waiting: bool) -> int:
    # Waiting on its own background agents/commands is not finishing: no checks, no
    # block, nothing shown; the completion notification wakes the
    # session and that Stop judges.
    if waiting:
        return 0
    tasks = store.owned(conn, session) if session else []
    if tasks:
        deadline = time.monotonic() + STOP_BUDGET
        results = [stop(conn, t, deadline, wide=len(tasks) > 1) for t in tasks]
    else:
        results = [_no_task_stop(conn, home, session)]
    blocks = [text for code, text in results if code]
    notes = [text for code, text in results if not code and text]
    if blocks:
        text = "\n\n".join(blocks)
        if len(text) > STOP_TEXT_MAX:
            tail = i18n.t("hooks.stop_text_tail")
            text = text[:STOP_TEXT_MAX - len(tail)] + tail
        print(text, file=sys.stderr)
        return 2
    if notes:
        print(json.dumps({"systemMessage": "\n".join(notes)}, ensure_ascii=False))
    return 0


def _grace(conn: sqlite3.Connection, event: str, session: str) -> int:
    """A session from before this wf: its MCP server is the old process, so nothing
    is enforced; it is told once."""
    if event == "user-prompt-submit" and store.notice_once(conn, session):
        print(i18n.t("hooks.grace"))
    return 0


def main(event: str, stdin: str) -> int:  # noqa: C901
    # Scope first, and fail OPEN if it cannot be decided (bad stdin, no git): a
    # wf that cannot tell whether a session is in scope must not trap
    # sessions that are not. Only in-scope failures fail closed.
    try:
        data = json.loads(stdin)
        base = data.get("cwd") or os.getcwd()
        session = data.get("session_id") or ""
        tool, tool_input = data.get("tool_name", ""), dict(data.get("tool_input") or {})
        for key in ("file_path", "notebook_path"):
            if tool_input.get(key):
                tool_input[key] = str(Path(base, os.path.expanduser(tool_input[key])))
        home = scope(os.environ.get("CLAUDE_PROJECT_DIR") or base)
    except Exception as exc:
        print(i18n.t("hooks.scope_error", event=event, exc=repr(exc)), file=sys.stderr)
        return 0
    if home is None:
        return 0
    try:
        conn = store.connect()
        if event == "session-start" and session:
            store.register(conn, session)
        elif session and not store.registered(conn, session):
            return _grace(conn, event, session)
        if event == "stop":
            return _stop_event(conn, home, session, bool(data.get("background_tasks")))
        if event == "pre-tool-use":
            reason = pre_tool_use(conn, session, tool, tool_input, bool(data.get("agent_id")), base)
            if reason:
                print(reason, file=sys.stderr)
                return 2
            if out := _write_hint(conn, session, tool, tool_input, base):
                print(json.dumps(out, ensure_ascii=False))
            return 0
        if event == "post-tool-use":
            if out := post_tool_use(conn, session, tool, tool_input, data.get("tool_response"),
                                    bool(data.get("agent_id")), data.get("agent_id")):
                print(json.dumps(out, ensure_ascii=False))
            return 0
        prompt = data.get("prompt", "")
        human = (data["source"] == "user" if "source" in data
                 else not INJECTED_PROMPT.match(prompt.lstrip()))
        text = (session_start(conn, session) if event == "session-start"
                else user_prompt(conn, session, prompt, human=human))
        if text:
            print(text)
        return 0
    except Exception as exc:  # the failure policy below IS the handling
        print(i18n.t("hooks.hook_error", event=event, exc=repr(exc)), file=sys.stderr)
        return 2 if event == "stop" else 0
