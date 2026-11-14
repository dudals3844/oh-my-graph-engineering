---
name: improving-skills
description: Use right after an omge:wf retro verdict (or when a loaded skill turned out wrong, the user corrected a procedure, or a reusable technique was worked out) to patch or create skills in ~/.claude/skills from that task's evidence.
---

# Improving skills

A retro fixes the graph; this skill fixes the procedures (skills). One goal: the next similar task starts
with a better skill. Answer in the user's language.

## 1. Change only on a signal

Look for these signals in this task. With none, finish with one line, "no change — no signal". Do not
invent one.

- The user corrected a procedure (a correction, "don't do it that way", a revert).
- A loaded skill was wrong, or missing a step you had to work around.
- A verified new technique came up and will clearly be reused (a command, an order, a pitfall).
- The same mistake happened twice in this task.
- **The same procedure repeats.** This task and earlier ones rebuilt the same command set or order by
  hand. Twice or more, lift it into a skill. Find it through the type evidence from `wf_retro()`,
  `wf_status`, issue comments, and `git -C ~/.claude/skills log --grep`.

Do not record:

- transient environment failures (network, quota, a single timeout);
- negative claims like "tool X does not work" — they outlive the fix as a refusal;
- incident narratives, or content that only means something with a PR or issue number;
- an unsolved failure written up as if it were a procedure.

Facts and preferences go to memory, project rules to CLAUDE.md; only repeatable procedures are skills.
Do not copy what CLAUDE.md or memory already holds.

## 2. Where to change, in this order

1. The skill loaded in this task.
2. An existing skill that already covers the topic. Check `ls ~/.claude/skills` and each description first.
3. Move growing detail into that skill's `references/<topic>.md`.
4. Create a new skill only when nothing fits. Name it for the class of work (`writing-a-plan`), not for
   today's incident (`fix-pr-123`).

Do not edit skills managed elsewhere: synced directories, symlinks, plugin skills. Send the fix to their
source repository as an issue or PR.

## 3. Graph and skill together

Look at both in one retro, without mixing their roles.

- **The graph (omge:wf library)** owns what is checked and when. Fix repeated failures with a check, order,
  param or earlier node (`wf_retro`).
- **The skill** owns how. It holds repeated procedures, commands and pitfalls.
- **Link them.** When a skill is created or changed, name it in the `do` of the node that uses the
  procedure so the next task starts with it, e.g. "per the writing-a-plan skill". If the graph changes too,
  put it in the retro's reuse_graph.
- **Add a `skill:` gate with care.** `skill:` on a node forces the call. Gate only on skills kept in git
  that will not disappear. Before archiving or renaming one, remove its references first:
  `grep -l <name> ~/.local/share/omge/library/*.yaml` (or `$WF_LIBRARY`). A gate on an uninstalled skill
  blocks its node forever.
- A repeated failure from not knowing the procedure: fix the skill. From a missing check: fix the graph.
  Both: fix both.

## 4. How to change

- **Read first.** Open the file with Read now. Do not edit from memory or a quote left in the conversation.
- **Change little.** Edit only the lines concerned. Rewrite the whole file only when its structure is
  wrong, and give the reason in the commit message; a full rewrite loses detail.
- **One lesson is one rule.** Strengthen the existing line instead of adding a similar one. Fix a wrong
  sentence in place; do not append "UPDATE: actually…".
- **Mind the size.** The description is one sentence and starts with the trigger. The body stays under 150
  lines; move overflow to `references/`. Keep strong words (MUST, never) for real prohibitions such as
  approval boundaries.
- **Do not delete.** Move a skill no longer needed to `~/.claude/skills/_archive/`. If another skill
  absorbed it, name that skill in the commit message.

## 5. Verify

- For every change, give a reviewer other than the author (e.g. `omge:reviewer`) the diff and the signal
  behind it. The reviewer judges by section 1's signals, section 2's location and section 4's size.
- For a change in behavior (a new skill, a new prohibition, changed trigger wording), run one or two real
  requests on the old and the new version and compare. Get the old one with `git show HEAD:<path>`. If it
  is not better, drop it. A check that passes on either version is not evidence.
- Never weaken skill wording that guards an approval boundary, a deploy or another irreversible action
  without asking the user first.

## 6. Record and revert

`~/.claude/skills` is a git repository (if not, start with `git -C ~/.claude/skills init -q`). One change,
one commit:

```bash
git -C ~/.claude/skills add -A <changed paths> && git -C ~/.claude/skills commit -q -F - <<'MSG'
skill(<name>): <what changed>

Signal: <task #id and signal — user correction / loaded skill was wrong / verified technique>
Skill-Effect: <skill loaded in this task>=helped|hurt|none
MSG
```

- Even with nothing changed, if this task loaded a skill, record only `Skill-Effect` with `--allow-empty`.
  That is the helped/hurt counter.
- Revert with `git -C ~/.claude/skills revert <sha>`.
- Add the skill commit sha, or "no change — reason", as a line in the retro issue comment.

## 7. Prune (monthly, or past 20 skills)

```bash
git -C ~/.claude/skills log --since=60.days --format=%B | grep '^Skill-Effect:' | sort | uniq -c
```

- A skill with `hurt` at or above `helped`: read those commits, then fix or archive it.
- A skill not loaded in 60 days is an archive candidate, unless it is younger than 60 days. Unused is not
  evidence of harm.
- Merge two skills whose descriptions overlap: archive the absorbed one and name it in the commit message.
