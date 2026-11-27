"""checks messages."""

EN: dict[str, str] = {
    "checks.unrun": "[omge:wf] check budget exhausted — not run",
    "checks.param_unset": "param `{name}` is unset — set it first with wf_set",
    "checks.param_mismatch": "param `{name}`={value} does not match {pattern}",
    "checks.no_dir": "[omge:wf] no dir: {cwd} — if the user moved it, wf_set(issue_url, 'dir', <path>)",
    "checks.review_blocking": ("the reviewer's latest verdict is blocking — fix those findings"
                               " and get reviewed again, or `wf_drop`"),
    "checks.protected_waive": ("`{node}` is a protected gate — a waiver needs an independent"
                               " reviewer's pass for the current HEAD (its reply ends"
                               " `Verdict: pass` then `Reviewed SHA: <HEAD>`)"),
    "checks.floor_waive": "`{node}` is a floor gate — it cannot be waived; pass its check or `wf_drop`",
    "checks.floor_stale": ("floor gate `{floor}` has not passed now (a failing one is reopened with"
                           " everything after it) — `{node}` cannot close until it passes"),
    "checks.no_when": "`{node}`: no `next` when matches — completion refused, staying on the node",
    "checks.branch_route": ("`{node}` is a branch node — waive it with a destination:"
                            " `wf_waive({node}>{to}, <reason>)` ({options})"),
    "checks.already_closed": "`{node}` was already closed meanwhile — wf_status",
    "checks.loop_max": ("`{node}` → `{to}`: the loop back used up its max {max} —"
                        " completion refused. Report it (`wf_waive`)."),
    "checks.e2e_drop": "e2e task cited as evidence by task #{tid} `{node}` — auto cleanup",
    "checks.retro_idle": ("retro skipped: no failure or revision signal"
                          " (clean task {count}/{every} since the last retro)"),
    "checks.skip_reason": "skipping the retro needs a reason",
    "checks.skip_again": ("the retro cannot be skipped — task #{tid} recorded the same"
                          " revise(+{nodes}) too. Fold a recurring revision into the type's"
                          " default graph with a structural review (update_type)"),
    "checks.skipped": "retro skipped (agent's call): {reason}",
    "checks.retro_not_ready": "retro is not ready — close the remaining nodes first",
    "checks.not_ready": ("`{node}` is not ready (preceding nodes unfinished, already done,"
                         " or no such node)"),
    "checks.no_on_fail": "`{node}` has no on_fail — there is nowhere to go back to",
    "checks.fail_reason": "wf_fail needs a reason (what failed)",
    "checks.check_passes": "`{node}`'s check passes — it is not a failure, wf_complete",
    "checks.retry_recorded": "`{node}` failure recorded (on_fail: retry) — redo it in place",
    "checks.fail_exhausted": ("`{node}` → `{to}`: the loop back used up its on_fail_max {limit} —"
                              " task #{tid} was recorded as stalled. Notify the user with"
                              " PushNotification and stop."),
    "checks.fail_back": "`{node}` failed → redo from `{to}` ({count}/{limit})",
    "checks.no_retro": ("[omge:wf] no wf_retro record — call wf_retro first"
                        " (if there is nothing to improve, wf_retro(verdict=\"skip\", reason=...))"),
    "checks.silent_human": ("[omge:wf] the check failed with no output (exit={code}; check: {check}) — this node waits"
                            " on a human decision (a merge, etc.). Do not repeat wf_complete;"
                            " ask the user and wait"),
    "checks.silent": "[omge:wf] the check failed with no output (exit={code}; check: {check}) — re-read this node's `do`",
    "checks.carried": "code unchanged — pass carried over from {sha}",
    "checks.no_skill": ("[omge:wf] no record of a call to skill `{skills}` — if you only applied its"
                        " criteria, report that; this node requires the skill to run and does"
                        " not close unless `wf_waive`d"),
    "checks.no_agent": ("[omge:wf] no record of a call to agent `{agents}` — this node must be handed"
                        " to that subagent with the Agent tool, and does not close unless"
                        " `wf_waive`d"),
    "checks.no_check": "`{node}` has no check — leave what you did as evidence",
    "checks.check_failed": "`{node}` check failed exit={code}:\n{tail}",
}

KO: dict[str, str] = {
    "checks.unrun": "[omge:wf] 검사 예산 소진 — 미실행",
    "checks.param_unset": "param `{name}` 미설정 — wf_set 으로 먼저 설정하라",
    "checks.param_mismatch": "param `{name}`={value} 가 {pattern} 에 맞지 않는다",
    "checks.no_dir": "[omge:wf] dir 없음: {cwd} — 사용자가 옮겼다면 wf_set(issue_url, 'dir', <경로>)",
    "checks.review_blocking": "리뷰어의 최신 판정이 blocking 이다 — 그 지적을 고치고 다시 리뷰받거나 `wf_drop`",
    "checks.protected_waive": ("`{node}` 는 보호 게이트다 — 면제하려면 현재 HEAD 에 대한 독립 리뷰어의 pass"
                               " 가 필요하다 (응답 끝 `Verdict: pass`, `Reviewed SHA: <HEAD>`)"),
    "checks.floor_waive": "`{node}` 는 floor 게이트다 — 면제할 수 없다. check 를 통과하거나 `wf_drop`",
    "checks.floor_stale": ("floor 게이트 `{floor}` 가 지금 통과한 상태가 아니다 (실패하면 그 뒤까지 다시"
                           " 연다) — 통과하기 전에는 `{node}` 를 닫을 수 없다"),
    "checks.no_when": "`{node}`: 어느 `next` 의 when 도 맞지 않는다 — 완료 거부, 노드에 머문다",
    "checks.branch_route": ("`{node}` 는 갈래 노드다 — 갈 곳을 정해 면제하라:"
                            " `wf_waive({node}>{to}, <사유>)` ({options})"),
    "checks.already_closed": "`{node}` 가 그사이 이미 닫혔다 — wf_status",
    "checks.loop_max": "`{node}` → `{to}` 되돌아가기가 max {max}회를 다 썼다 — 완료 거부. 알려라 (`wf_waive`).",
    "checks.e2e_drop": "task #{tid} `{node}` 가 증거로 쓴 e2e task — 자동 정리",
    "checks.retro_idle": "retro 생략: 실패·수정 신호 없음 (마지막 retro 이후 깨끗한 task {count}/{every})",
    "checks.skip_reason": "retro 를 건너뛰려면 reason 이 필요하다",
    "checks.skip_again": ("retro 를 건너뛸 수 없다 — task #{tid} 도 같은 revise(+{nodes}) 를 기록했다."
                          " 되풀이하는 수정은 구조검토로 유형 기본 graph 에 반영하라 (update_type)"),
    "checks.skipped": "retro 건너뜀 (에이전트 판단): {reason}",
    "checks.retro_not_ready": "retro 가 ready 가 아니다 — 남은 노드를 먼저 닫는다",
    "checks.not_ready": "`{node}` 는 ready 가 아니다 (선행 미완료, 이미 완료, 또는 없는 노드)",
    "checks.no_on_fail": "`{node}` 에는 on_fail 이 없다 — 되돌아갈 곳이 없다",
    "checks.fail_reason": "wf_fail 에는 reason(무엇이 실패했나)이 필요하다",
    "checks.check_passes": "`{node}` 의 check 가 통과한다 — 실패가 아니다, wf_complete",
    "checks.retry_recorded": "`{node}` 실패 기록 (on_fail: retry) — 제자리에서 다시 한다",
    "checks.fail_exhausted": ("`{node}` → `{to}` 되돌아가기가 on_fail_max {limit}회를 다 썼다 —"
                              " task #{tid} 를 stalled 로 기록했다. PushNotification 으로 사용자에게"
                              " 알리고 종료하라."),
    "checks.fail_back": "`{node}` 실패 → `{to}` 부터 다시 ({count}/{limit}회)",
    "checks.no_retro": ("[omge:wf] wf_retro 기록이 없다 — wf_retro 를 먼저 호출하라"
                        " (개선할 것이 없으면 wf_retro(verdict=\"skip\", reason=...))"),
    "checks.silent_human": ("[omge:wf] check 가 출력 없이 실패했다 (exit={code}; check: {check}) — 이 노드는 사람의 결정(머지 등)을"
                            " 기다린다. wf_complete 를 반복하지 말고 사용자에게 묻고 기다려라"),
    "checks.silent": "[omge:wf] check 가 출력 없이 실패했다 (exit={code}; check: {check}) — 이 노드의 `do` 를 다시 읽어라",
    "checks.carried": "코드 동일 — {sha} 통과 이월",
    "checks.no_skill": ("[omge:wf] 스킬 `{skills}` 호출 기록이 없다 — 기준만 적용했으면 그렇게 보고하고,"
                        " 스킬 실행이 필수인 노드라 `wf_waive` 하지 않는 한 닫히지 않는다"),
    "checks.no_agent": ("[omge:wf] 에이전트 `{agents}` 호출 기록이 없다 — 이 노드는 Agent 도구로 그 subagent"
                        " 에게 맡겨야 하고, `wf_waive` 하지 않는 한 닫히지 않는다"),
    "checks.no_check": "`{node}` 는 check 가 없다 — 무엇을 했는지 evidence 로 남겨라",
    "checks.check_failed": "`{node}` check 실패 exit={code}:\n{tail}",
}
