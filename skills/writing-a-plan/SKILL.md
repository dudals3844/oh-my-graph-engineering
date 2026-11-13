---
name: writing-a-plan
description: Use when writing an implementation plan (PLAN) for a multi-step or complex change before touching code, e.g. the issue-resolution or code-implementation plan node, or when a plan/<name>.md document is needed.
---

# Writing a plan

A plan collects the decisions the implementer cannot make alone. It does not write the code for them.
Answer in the user's language.

**Never:** use the built-in Plan Mode (EnterPlanMode/ExitPlanMode) or `--interactive`, or stop to ask the
user to review the plan or pick an execution mode. If the scope is already approved, finish the plan and go
straight to the next wf node. Keep every approval boundary the user's instructions set (deploys, merges,
anything irreversible).

## 0. Is a plan needed

- One or two files with an obvious approach: no plan. Leave complexity=simple and say so.
- Write one only for design choices, several files or services, a new module, schema or external contract,
  or a change that is hard to undo.

## 1. Dig first

- Actually read the code that will change, its callers, the existing helpers and patterns, and the test
  command. Knowing a file name is not understanding it.
- Hand a broad sweep to an explorer subagent (e.g. `omge:explorer`) and take back only locations, rules and
  entry points.
- Settle questions a run can answer with a small experiment first. Ask the user only about taste or
  product decisions an experiment cannot settle, as options.

## 2. Pick the shape

- With design choices, put at least two structurally different candidates side by side. A tweak inside one
  candidate is not a candidate.
- Screen each candidate for these warning signs:
  - a shallow module whose callers must chain several methods;
  - an internal decision leaking into several places;
  - two places writing the same state;
  - two ways to do the same thing;
  - duplicated lists that must be kept in sync by hand;
  - a method that only passes things through and hides nothing.
- Prefer the option that hides more complexity behind a smaller public surface. Check YAGNI, reuse and the
  standard library first.
- Leave each losing candidate and why it lost as one line under "Rejected alternatives".

## 3. Document format

Write `plan/<name>.md` (or where the node's `do` says) in this order:

1. **Goal** in one sentence, **approach** in two or three, **SPEC link**. The plan rests on the SPEC and is
   read with it.
2. **Global constraints**: conditions on every task — versions, dependencies, names, approval boundaries.
   Copy the SPEC's exact values.
3. **File map**: files to create, files to change (`path:line`) and files to delete, each with its
   responsibility. Follow the repository's existing structure.
4. **PR order**: one PR is one change with its own evidence. Give dependencies and the files each PR touches.
5. **Tasks** (at least one per PR). A task is the smallest unit with its own test cycle. Fold setup and doc
   steps into the task that needs them.
   - **Files**: exact paths.
   - **Interfaces**: what it uses from earlier tasks and what later tasks rely on — exact names,
     arguments, return types.
   - **Steps**: `- [ ]` checkboxes, one checkable result per step. Failing test (name and assertion, the
     SPEC's values) → command that shows it fails and the expected output → signature and file to
     implement → command that shows it passes → commit.
   - **Verification**: the unit test command plus a real run (the command that runs the service, a log
     line, a DB row, an end-to-end task) and its pass condition. Tests alone are not verification. If
     performance is the goal, measure the baseline first and write the failing threshold.
6. **Review focus**: up to five inputs or failure conditions the SPEC implies but no test touches, most
   likely to break first. Add each line's test to the task that owns that code.
7. **Rejected alternatives**, **risks** (what to watch in which PR).

## 4. What a step holds

- A step is done when the implementer can write only one reasonable thing for it. Unambiguous, not
  exhaustive.
- Use code blocks only for signatures, algorithms the tests cannot pin down, and exact wording the SPEC fixes.
- No lines that decide nothing ("TBD", "handle edge cases", "add proper validation", "test the above"). No
  references to types or functions no task defines.

## 5. Self-check (do it yourself, no subagent)

1. Does every done condition in the SPEC have a task that implements it? If not, add one.
2. Does every step allow only one reasonable implementation? Fix lines that decide nothing and function
   bodies copied in.
3. Do later tasks' names and signatures match the earlier tasks' definitions?
4. Does every review-focus line have a test?
5. If the plan is several times longer than the SPEC or mostly code blocks, replace the body with
   signatures, test names and assertions.

Fix what you find in place and move on.

## 6. Hand off

Commit the plan document if it lives in the repository. If the node asks for a receipt (issue-resolution
wants an issue comment whose first line is exactly `PLAN done`, posted after the last passing `scope`
comment), post it with the document path, the PR order and the remaining risks. Then go to implement
without waiting.
If implementation shows the plan is wrong, fix the plan instead of patching around it; the same workaround
repeating is the sign. If the SPEC is wrong, go back with wf_fail(plan, reason).
