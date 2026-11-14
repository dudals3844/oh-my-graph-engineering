"""`omge hook <event>` (what the plugin hooks run) and `omge status` (every open task)."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from . import graph, hooks, store
from .i18n import t

EVENTS = ("session-start", "user-prompt-submit", "pre-tool-use", "post-tool-use", "stop")



def _worktree(path: Path) -> Path | None:
    top = subprocess.run(["git", "-C", str(path), "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True)
    return Path(top.stdout.strip()).resolve() if top.returncode == 0 else None


def _in_task(cwd: Path, task_dir: Path) -> bool:
    """True when `cwd` is this task's dir or inside it, in the same git worktree.
    A nested worktree has its own root. A non-git dir matches by path."""
    if cwd != task_dir and not cwd.is_relative_to(task_dir):
        return False
    return _worktree(cwd) == _worktree(task_dir)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="omge", formatter_class=argparse.RawDescriptionHelpFormatter,
        description=t("cli.description"), epilog=t("cli.epilog"))
    sub = parser.add_subparsers(dest="command", required=True)
    hook = sub.add_parser(
        "hook", help=t("cli.hook_help"), description=t("cli.hook_description"))
    hook.add_argument("event", choices=EVENTS, help=t("cli.event_help"))
    status = sub.add_parser(
        "status", help=t("cli.status_help"), description=t("cli.status_description"))
    status.add_argument("--all", action="store_true",
                        help=t("cli.all_help"))
    args = parser.parse_args(argv)
    if args.command == "hook":
        return hooks.main(args.event, sys.stdin.read())
    conn = store.connect()
    tasks = conn.execute(f"SELECT * FROM task WHERE state IN {store.OPEN_STATES!r}").fetchall()
    if not tasks:
        print(t("cli.no_open"))
        return 0
    if args.all:
        print("\n\n".join(graph.status_text(conn, t) for t in tasks))
        return 0
    here = Path.cwd().resolve()
    mine = [t for t in tasks if _in_task(here, Path(t["dir"]).resolve())]
    other = len(tasks) - len(mine)
    parts = [graph.status_text(conn, t, brief=True) for t in mine]
    parts.append(t("cli.other_dirs", n=other) if other else t("cli.no_other"))
    summary = t("cli.none_here", n=other)
    print("\n\n".join(parts) if mine else summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
