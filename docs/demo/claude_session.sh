#!/usr/bin/env bash
# Records docs/demo.gif (default) or, with `compat`, docs/compat-demo.gif: omge running inside a real
# Claude Code TUI session, driven through tmux. `compat` also loads the public superpowers and ponytail
# plugins, cloned fresh into the temp dir, and runs a one-off graph whose nodes call their skills.
# Isolated: a throwaway HOME, CLAUDE_CONFIG_DIR and git repo; a copy of the plugin loads via --plugin-dir.
# Auth comes from the caller's environment (CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY, and
# ANTHROPIC_BASE_URL if set) and is never written to disk.
# Needs tmux, claude, uv, asciinema 2.4 (pip) and agg (asciinema/agg release binary).
# Usage: [CLAUDE=/path/to/claude] ASCIINEMA=/path/to/asciinema AGG=/path/to/agg bash docs/demo/claude_session.sh [compat]
set -euo pipefail
mode=${1:-demo}
repo=$(cd "$(dirname "$0")/../.." && pwd)
work=$(mktemp -d /tmp/omge-demo.XXXX)
sock=$(basename "$work")  # a tmux server per take, so concurrent takes never touch each other
# kill-server does not end asciinema's child tree (claude, its MCP servers), which keeps recreating
# $work. Every process of this take, reparented or not, inherits OMGE_DEMO_TAKE=$work from the env -i
# below: SIGKILL those (an exact environ entry, not an argv regex) until none is left, then rm.
cleanup() {
  tmux -L "$sock" kill-server 2>/dev/null || true
  while pids=$(grep -lzxF "OMGE_DEMO_TAKE=$work" /proc/[0-9]*/environ 2>/dev/null | cut -d/ -f3); [ -n "$pids" ]; do
    kill -KILL $pids 2>/dev/null || true
  done
  rm -rf "$work"
}
trap cleanup EXIT
mkdir -p "$work/home/.claude" "$work/greeter" "$work/omge"
# A copy, so the hook commands Claude Code echoes show no path of the real checkout.
tar -C "$repo" --exclude=.venv --exclude=.git -cf - . | tar -C "$work/omge" -xf -
cd "$work/greeter"
git init -q -b main && printf 'print("hello")\n' > greet.py
git -c user.name=demo -c user.email=demo@example.com add . &&
  git -c user.name=demo -c user.email=demo@example.com commit -qm init
git config user.name demo && git config user.email demo@example.com
cat > "$work/home/.claude/.claude.json" <<EOF
{"hasCompletedOnboarding": true, "theme": "dark", "projects": {"$PWD": {"hasTrustDialogAccepted": true}}}
EOF
echo '{"skipDangerousModePermissionPrompt": true, "spinnerTipsEnabled": false}' > "$work/home/.claude/settings.json"

cast="$work/demo.cast"
# --dangerously-skip-permissions is used ONLY because this session runs in the throwaway temp project and
# config this script just created under $work. Never use it in a real project.
claude_cmd="${CLAUDE:-claude} --model sonnet --plugin-dir $work/omge --dangerously-skip-permissions"
if [ "$mode" = compat ]; then
  for src in obra/superpowers DietrichGebert/ponytail; do
    git clone -q --depth 1 "https://github.com/$src" "$work/${src#*/}"
    claude_cmd+=" --plugin-dir $work/${src#*/}"
  done
  node=trim cut='`retro`\s+exit=' gif=compat-demo.gif
  prompt='Start a one-off omge graph (wf_start type="new", new_reason="demo") for: add greet(name) to greet.py. Nodes: `test` (skill: superpowers:test-driven-development; write test_greet.py and make it pass; check: python3 test_greet.py; on_fail: retry), then `trim` (after test; skill: ponytail:ponytail; cut greet.py to the minimum; same check and on_fail). Run each node by calling its skill. Stop once `trim` passes.'
else
  node=plan cut='streak\s+2/|`implement`\s+exit=' gif=demo.gif
  prompt='Start the omge code-implementation workflow here for: add a --shout flag to greet.py, and show me the graph. Then end your turn at once. When the stop hook blocks you, do only the plan node (a two-line plan) and stop.'
fi
env -i PATH="$(dirname "$(command -v uv)"):$(dirname "$(command -v claude)"):/usr/bin:/bin" \
  OMGE_DEMO_TAKE="$work" HOME="$work/home" CLAUDE_CONFIG_DIR="$work/home/.claude" TERM=xterm-256color LANG=C.UTF-8 PS1='$ ' DISABLE_AUTOUPDATER=1 \
  UV_CACHE_DIR="$(uv cache dir)" ${ANTHROPIC_BASE_URL:+ANTHROPIC_BASE_URL="$ANTHROPIC_BASE_URL"} \
  ${CLAUDE_CODE_OAUTH_TOKEN:+CLAUDE_CODE_OAUTH_TOKEN="$CLAUDE_CODE_OAUTH_TOKEN"} \
  ${ANTHROPIC_API_KEY:+ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY"} \
  tmux -L "$sock" new-session -d -s omge-demo -x 110 -y 32 \
  "${ASCIINEMA:-asciinema} rec -q --overwrite -i 2 -c '$claude_cmd' $cast"

pane() { tmux -L "$sock" capture-pane -p -t omge-demo; }
wait_for() {  # wait_for <regex> <seconds>
  for ((i = 0; i < $2; i++)); do pane | grep -qE "$1" && return 0; sleep 1; done
  echo "timed out waiting for: $1" >&2; pane >&2; exit 1
}
say() { tmux -L "$sock" send-keys -t omge-demo -l "$1"; sleep 1; tmux -L "$sock" send-keys -t omge-demo Enter; }
# The plugin's own interpreter: a different SQLite build reading the WAL DB breaks its writers (disk I/O error).
node_done() { uv run --locked --quiet --project "$work/omge" python -c 'import sqlite3, sys; sys.exit(sqlite3.connect(sys.argv[1]).execute("SELECT 1 FROM node_state WHERE node = ? AND status = '"'done'"'", (sys.argv[2],)).fetchone() is None)' "$work/home/.local/share/omge/wf.db" "$node" 2>/dev/null; }

wait_for 'for shortcuts|bypass permissions' 60; sleep 2
say "$prompt"
for ((i = 0; i < 300; i++)); do node_done && break; sleep 1; done
node_done || { echo "$node never passed" >&2; pane >&2; exit 1; }
sleep 8
tmux -L "$sock" send-keys -t omge-demo Escape; sleep 2
tmux -L "$sock" send-keys -t omge-demo C-c; sleep 1; tmux -L "$sock" send-keys -t omge-demo C-c
for ((i = 0; i < 30; i++)); do tmux -L "$sock" has-session -t omge-demo 2>/dev/null || break; sleep 1; done

# Blank the account usage notice, and end once $node passed: cut where the hook blocks on what follows
# (demo: streak 2/, or `implement`; compat: `retro`), which only leads on to the stall.
python3 -I - "$cast" "$work/trim.cast" "$cut" <<'PY'
import json, re, sys
src, dst = open(sys.argv[1]), open(sys.argv[2], "w")
dst.write(src.readline())
for line in src:
    t, kind, data = json.loads(line)
    if re.search(sys.argv[3], re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", " ", data)):
        break
    data = re.sub(r"You've used \d+% of your weekly limit[^\x1b]*", lambda m: " " * len(m.group()), data)
    dst.write(json.dumps([t, kind, data]) + "\n")
PY
"${AGG:-agg}" --font-size 16 --idle-time-limit 2 --speed 1.5 --last-frame-duration 0.5 "$work/trim.cast" "$repo/docs/$gif"
