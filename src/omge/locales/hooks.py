"""hooks messages."""

EN: dict[str, str] = {
    "hooks.stop_text_tail": "… (the rest is in wf_status)",
    "hooks.grace": "wf was updated — this session is not enforced until it is restarted (or resumed).",
    "hooks.ask_hint": "If you need to ask the user, use AskUserQuestion (do not ask by ending the turn).",
    "hooks.no_task": ("wf: this session has no open task — unless you are answering a question,"
                      " first design a graph with wf_start (title if there is no issue; types via"
                      " wf_list_types). Someone else's task: wf_attach(issue_url or task)."),
    "hooks.none": "none",
    "hooks.orphans": ("wf: {count} open task(s) with no owner session for over an hour"
                      " — list them with `wf status`, take one over with wf_attach(issue_url or"
                      " task=<id>), discard one with `wf_drop(task, reason)`"),
    "hooks.paused": "wf #{id}: paused — it returns to active on the next ordinary prompt.",
    "hooks.waive_usage": ("wf: `wf waive <node> <reason>` — for a task whose type does not fit,"
                          " to close all remaining nodes: `wf drop #{id} <reason>`."),
    "hooks.not_pending": "wf: `{node}` is not a remaining node.",
    "hooks.reason_required": "wf: a reason is required — `wf waive <node> <reason>`.",
    "hooks.waived_by_user": "wf: `{node}` waived (user): {reason}",
    "hooks.drop_usage": "wf: `wf drop #<id> <reason>` — needs an open task's number and a reason.",
    "hooks.many_tasks": "wf: this session has several tasks ({ids}) — `wf {verb} #<id> …`",
    "hooks.stash": ("wf: a bare git stash / stash pop changes the shared stash"
                    " — use a tagged push and apply <sha>."),
    "hooks.archive": "wf: archive/* branches are never deleted.",
    "hooks.worktree": "wf: another open task's working directory is never removed.",
    "hooks.push_one": "wf: git push must target the task branch only.",
    "hooks.push_refspec": "wf: name the push refspec — git push origin HEAD:{branch}",
    "hooks.push_mismatch": ("wf: the git push refspec differs from the task branch"
                            " — git push origin HEAD:{branch}"),
    "hooks.identity": ("wf: the checkout/branch of this git commit/push differs from the open"
                       " task's identity, or none is recorded — check the task dir/branch and"
                       " wf_set(dir)."),
    "hooks.stalled_write": ("wf: this session's task is stalled — writes are blocked."
                            " `wf_waive(node, reason)`, or it lifts once a check makes progress."),
    "hooks.early_skill": ("wf: the skill `{name}` call does not count yet for #{tid} `{node}` —"
                          " the node is not ready. Finish the preceding nodes ({waiting}) first,"
                          " then call it again."),
    "hooks.delegated": ("wf: a node for a subagent is ready — the main session cannot write."
                        " Hand it off with the Agent tool: {need}"),
    "hooks.scope_path": ("wf: the write path is outside the task dir — re-check the current check"
                         " scope and, if needed, wf_revise or a separate wf_start."),
    "hooks.scope_agent": ("wf: re-check the user's reply/delegation against the current ready"
                          " scope and checks — if the scope changed, wf_revise or start a"
                          " separate wf_start before going on."),
    "hooks.no_task_id": ("wf: could not read the task id from the result, so no owner was"
                         " recorded — call wf_attach(task=<id>)."),
    "hooks.dirty_release": ("wf: ending with changes left and no task (Stop blocked {count} times"
                            " in the same state)."),
    "hooks.idle_block": ("wf: with no task, {calls} outward write(s) since this prompt —"
                         " work that is not answering a question is designed and run as a graph.\n"),
    "hooks.idle_release": ("wf: ending after working with no task (Stop blocked {count} times in"
                           " the same state)."),
    "hooks.concurrent": "[wf] concurrent change — retry",
    "hooks.stop_head": "wf: cannot stop, nodes remain (task #{id} {name}).",
    "hooks.streak": ", streak {streak}/{max}).",
    "hooks.on_fail": "  to redo from an earlier step: wf_fail({node}, reason) → from `{to}`",
    "hooks.see_status": "The to-do and pitfalls are in wf_status.",
    "hooks.retro_only": ("wf: task #{id} has only the retro left — not blocking again for the same"
                         " reason."),
    "hooks.ci_release": ("wf: the remaining checks of task #{id} wait on external CI — not"
                         " blocking again for the same reason."),
    "hooks.ci_wait": ("The remaining checks are not failures but waits on external CI. Start"
                      " `gh run watch <run-id> --exit-status` with run_in_background and end this"
                      " turn — when its completion notification wakes the session, the next Stop"
                      " decides."),
    "hooks.stalled": ("wf: the same failure repeated {count} times, so task #{id} was recorded as"
                      " stalled. Notify the user with PushNotification and stop."),
    "hooks.pause_hint": " To stop, call `wf_pause` or type `wf pause` as the prompt.",
    "hooks.scope_error": "wf hook could not decide its scope ({event}): {exc}",
    "hooks.hook_error": "wf hook error ({event}): {exc}",
}

KO: dict[str, str] = {
    "hooks.stop_text_tail": "… (나머지는 wf_status)",
    "hooks.grace": "wf 가 업데이트됐다 — 이 세션은 재시작(또는 resume) 전까지 강제되지 않는다.",
    "hooks.ask_hint": "사용자에게 물어야 하면 AskUserQuestion 을 쓰라(턴 종료로 묻지 말 것).",
    "hooks.no_task": ("wf: 이 세션에 열린 task 가 없다 — 질문 답변이 아닌 일이면 먼저 wf_start 로"
                      " 그래프를 설계하라 (이슈가 없으면 title, 유형은 wf_list_types)."
                      " 남의 task 는 wf_attach(issue_url 또는 task)."),
    "hooks.none": "없음",
    "hooks.orphans": ("wf: 주인 세션이 1시간 넘게 없는 열린 task {count}개"
                      " — 목록은 `wf status`, 이어받으려면 wf_attach(issue_url 또는 task=<id>),"
                      " 버리려면 `wf_drop(task, reason)`"),
    "hooks.paused": "wf #{id}: paused — 다음 일반 프롬프트에서 active 로 돌아온다.",
    "hooks.waive_usage": ("wf: `wf waive <node> <사유>` — type 이 맞지 않아 남은 노드를 모두 닫을"
                          " task 는 `wf drop #{id} <사유>`."),
    "hooks.not_pending": "wf: `{node}` 는 남은 노드가 아니다.",
    "hooks.reason_required": "wf: 사유가 필요하다 — `wf waive <node> <사유>`.",
    "hooks.waived_by_user": "wf: `{node}` waived (사용자): {reason}",
    "hooks.drop_usage": "wf: `wf drop #<id> <사유>` — 열린 task 의 번호와 사유가 필요하다.",
    "hooks.many_tasks": "wf: 이 세션의 task 가 여럿이다 ({ids}) — `wf {verb} #<id> …`",
    "hooks.stash": "wf: bare git stash / stash pop 은 공유 stash 를 바꾼다 — tagged push 와 apply <sha>.",
    "hooks.archive": "wf: archive/* 브랜치는 삭제하지 않는다.",
    "hooks.worktree": "wf: 다른 열린 task 의 작업 디렉터리는 제거하지 않는다.",
    "hooks.push_one": "wf: git push 는 task branch 하나만 대상으로 해야 한다.",
    "hooks.push_refspec": "wf: push refspec 을 명시하라 — git push origin HEAD:{branch}",
    "hooks.push_mismatch": "wf: git push refspec 이 task branch 와 다르다 — git push origin HEAD:{branch}",
    "hooks.identity": ("wf: git commit/push 대상 checkout·branch 가 열린 task identity 와 다르거나"
                       " 기록이 없다 — task dir/branch 와 wf_set(dir) 를 확인하라."),
    "hooks.stalled_write": ("wf: 이 세션의 task 가 stalled 다 — 쓰기가 막혔다."
                            " `wf_waive(node, reason)` 하거나 check 가 진전되면 풀린다."),
    "hooks.early_skill": ("wf: 스킬 `{name}` 호출은 #{tid} `{node}` 에 아직 세지 않는다 —"
                          " 노드가 ready 가 아니다. 선행 노드({waiting})를 먼저 끝내고 다시 호출하라."),
    "hooks.delegated": ("wf: subagent 가 할 노드가 ready 다 — 메인 세션은 쓰지 못한다."
                        " Agent 도구로 맡겨라: {need}"),
    "hooks.scope_path": ("wf: 쓰기 경로가 task dir 밖이다 — 현재 check 범위를 재대조하고 필요하면"
                         " wf_revise 또는 별도 wf_start."),
    "hooks.scope_agent": ("wf: 사용자 응답/위임을 현재 ready 범위·check 와 재대조하라 — 범위가"
                          " 달라졌으면 wf_revise 또는 별도 wf_start 후 진행하라."),
    "hooks.no_task_id": ("wf: 결과에서 task id 를 읽지 못해 주인을 기록하지 못했다"
                         " — wf_attach(task=<id>) 를 호출하라."),
    "hooks.dirty_release": "wf: task 없이 변경이 남은 채 종료한다 (같은 상태로 종료 차단 {count}회).",
    "hooks.idle_block": ("wf: task 없이 이번 프롬프트 이후 외부 쓰기를 {calls}회 했다 —"
                         " 질문 답변이 아닌 일은 그래프로 설계해 실행한다.\n"),
    "hooks.idle_release": "wf: task 없이 일한 채 종료한다 (같은 상태로 종료 차단 {count}회).",
    "hooks.concurrent": "[wf] 동시 변경 — 재시도",
    "hooks.stop_head": "wf: 남은 노드가 있어 종료할 수 없다 (task #{id} {name}).",
    "hooks.streak": ", 연속 {streak}/{max}).",
    "hooks.on_fail": "  앞 단계부터 다시 해야 하면 wf_fail({node}, 사유) → `{to}` 부터",
    "hooks.see_status": "할 일·pitfalls 는 wf_status 로 본다.",
    "hooks.retro_only": "wf: task #{id} 는 retro 만 남았다 — 같은 이유로 다시 막지 않는다.",
    "hooks.ci_release": "wf: task #{id} 의 남은 검사는 외부(CI) 대기다 — 같은 이유로 다시 막지 않는다.",
    "hooks.ci_wait": ("남은 검사는 실패가 아니라 외부(CI) 대기다. `gh run watch <run-id>"
                      " --exit-status` 를 run_in_background 로 걸고 이번 턴을 끝내라 —"
                      " 완료 알림이 세션을 깨우면 다음 Stop 이 판정한다."),
    "hooks.stalled": ("wf: 같은 실패가 {count}회 반복돼 task #{id} 를 stalled 로"
                      " 기록했다. PushNotification 으로 사용자에게 알리고 종료하라."),
    "hooks.pause_hint": " 멈추려면 `wf_pause` 를 호출하거나 프롬프트에 `wf pause` 를 입력한다.",
    "hooks.scope_error": "wf 훅 범위 판정 실패 ({event}): {exc}",
    "hooks.hook_error": "wf 훅 오류 ({event}): {exc}",
}
