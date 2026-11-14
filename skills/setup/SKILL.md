---
name: setup
description: Use when the user runs /omge:setup or asks to set up omge — checks that uv is available and offers to install the optional ponytail plugin.
---

# omge setup

Answer in the user's language. Do the steps in order and report each result in one line.

## 1. uv

Run `uv --version`.

- It prints a version: report it, and tell the user that on first use uv resolves omge's Python
  dependencies from PyPI, pinned by the committed `uv.lock`, and downloads a Python 3.12 or newer if none is
  installed; nothing else is downloaded, and omge sends no telemetry. Go on.
- It fails: tell the user omge's hooks and MCP server run through uv, and point them to
  https://pypi.org/project/uv/ for installation. Do not install uv yourself. Stop here until they have it.

## 2. ponytail (optional)

Use AskUserQuestion with one question: install the ponytail plugin? Explain in one line that it is a
"lazy senior developer" skill that pushes for the smallest code that works. Options: yes / no.

- Yes: run, in order,
  ```bash
  claude plugin marketplace add DietrichGebert/ponytail
  claude plugin install ponytail@ponytail
  ```
  and report the output. A new session picks it up.
- No: do nothing.

## 3. Duplicate hooks

Tell the user: the omge plugin registers its own hooks (SessionStart, UserPromptSubmit, Stop,
PreToolUse, PostToolUse). If they previously wired `omge hook ...` commands into their own `settings.json`
(`~/.claude/settings.json` or a project's `.claude/settings.json`), they should remove those entries, or
every hook runs twice. Do not edit their settings yourself unless they ask.
