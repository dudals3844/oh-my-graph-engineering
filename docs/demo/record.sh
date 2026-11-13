#!/usr/bin/env bash
# Records docs/demo.gif from scene.sh with asciinema 2.4 (pip) and agg (asciinema/agg release binary).
# Usage: ASCIINEMA=/path/to/asciinema AGG=/path/to/agg bash docs/demo/record.sh
set -euo pipefail
repo=$(cd "$(dirname "$0")/../.." && pwd)
work=$(mktemp -d /tmp/omge-demo.XXXX)
mkdir -p "$work/home" "$work/greeter"
cd "$work/greeter"
git init -q -b main && printf 'print("hello")\n' > greet.py
git -c user.name=demo -c user.email=demo@example.com add . && \
  git -c user.name=demo -c user.email=demo@example.com commit -qm init
cast="$work/demo.cast"
env -i PATH="$repo/.venv/bin:/usr/bin:/bin" HOME="$work/home" TERM=xterm-256color \
  LANG=C.UTF-8 DEMO_DIR="$repo/docs/demo" PS1='$ ' \
  "${ASCIINEMA:-asciinema}" rec -q --overwrite --cols 100 --rows 32 \
  -c 'bash --noprofile --norc "$DEMO_DIR/scene.sh"' "$cast"
"${AGG:-agg}" --font-size 16 --idle-time-limit 5 --last-frame-duration 3 "$cast" "$repo/docs/demo.gif"
[ -n "${KEEP:-}" ] && cp "$cast" "$KEEP"; rm -rf "$work"
