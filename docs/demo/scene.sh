# The recorded scene. record.sh runs it inside a throwaway HOME and git repo.
wf-tool() { python3 "$DEMO_DIR/wf_tool.py" "$@"; }
hook() {  # what Claude Code runs on that event
  printf '{"session_id":"demo","cwd":"%s"}' "$PWD" | wf hook "$1"
  echo -e "\033[2m(exit $?)\033[0m"
}
printf '{"session_id":"demo","cwd":"%s","source":"startup"}' "$PWD" | wf hook session-start >/dev/null
say() { echo -e "\033[1;36m$*\033[0m"; sleep 0.8; }
run() {
  printf '$ '
  for ((i = 0; i < ${#1}; i++)); do printf '%s' "${1:i:1}"; sleep 0.012; done
  echo; eval "$1"; sleep "${2:-2.5}"
}
clear
say "# Every piece of work is a graph. Start one from a shipped template:"
run 'wf-tool wf_start type=code-implementation dir=. title="Add a --shout flag"' 5
clear
say "# The session tries to stop early: the Stop hook blocks it"
run 'hook stop' 3.5
clear
say "# Completing a node runs its check. No plan file yet, so it fails:"
run 'wf-tool wf_set key=plan value="$(dirname "$PWD")/plan.md" task=1' 1
run 'wf-tool wf_complete node=plan evidence="plan written" task=1' 3.5
say "# Write the plan; now the check passes and implement is ready"
run 'printf "# Plan\n- add a --shout flag to greet.py\n" > ../plan.md' 1
run 'wf-tool wf_complete node=plan evidence="plan written" task=1' 3
clear
say "# The graph moves on: plan is done, implement is ready"
run 'wf status --all' 5
