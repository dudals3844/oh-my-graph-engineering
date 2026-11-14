---
name: setup
description: Use when the user runs /omge:setup or asks to set up omge — asks the message language, checks that uv is available and offers to install the optional ponytail plugin.
---

# omge setup

Do the steps in order and report each result in one line.

## 0. Language

Use AskUserQuestion with one question, written in both languages: which language should omge's
messages use? Options: `English` / `한국어`. From here on, write every reply and every
AskUserQuestion (question, options, descriptions) in the chosen language.

Set `env.OMGE_LANG` (`en` or `ko`) in `~/.claude/settings.json`, keeping every other key; create the
file if it is missing. omge's hooks and MCP server read it, so a new session shows their messages in
that language. Report the change in one line.

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
