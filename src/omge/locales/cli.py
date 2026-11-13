"""cli messages."""

EN: dict[str, str] = {
    "cli.description": "Tracks work as a graph and confirms completion through each step's"
                       " check, evidence and retro.\n"
                       "The CLI is for status and the automatic hooks. Work is managed by"
                       " calling the wf-mcp tools.",
    "cli.epilog": """Quick start (CLI)
  wf status                 ready nodes of the current worktree and a summary of open tasks
  wf status --all           graph, current node and failure reasons of every open task
  wf status --help          status help
  wf hook --help            the automatic Claude Code hooks (not for starting work)

Agent workflow (wf-mcp tool calls, not shell commands)
  1. Find the right type with wf_list_types() and start the work with wf_start.
     wf_start(type="issue-resolution", dir="/worktree", issue_url="<GitHub issue URL>")
     Without an issue, give title instead of issue_url. The returned task ID names later calls.
  2. Read the ready nodes' do and pitfalls with wf_status(task=123) (123 is the returned ID).
     Set the params you need, e.g. wf_set(task=123, key="complexity", value="simple").
  3. Do a ready node, then call wf_complete(task=123, node="test", evidence="command, result").
     It closes only when its check really passes. Start, revise and state changes show the graph.
  4. A real failure goes back with wf_fail(task=123, node="test", reason="why it failed").
     When the path or the user's request changes, wf_revise(graph={{nodes: {{changed_id: fields}}}},
     reason=..., task=123) — do not resend the whole graph. Give title too if the summary changed.
     An ownerless task: wf_attach(task=123).
     CI queued/running or a wait for outside approval is not a failure. Wait until it ends.
  5. Finally read the evidence with wf_retro(task=123) and record verdict, deviations and any
     reason needed. With an issue, leave the wf retro comment, then complete.
     wf_complete(task=123, node="retro", evidence="retro evidence")

Review, plan and approval rules
  A skill node needs a real Skill tool call. Reading the criteria or applying them in words is not a call.
  A review's first line is ponytail <HEAD> or review <HEAD> and must equal the current SHA (7/40 chars).
  A SHA mentioned in the body is not evidence. A required skill receipt is left after the real call.
  After the plan artifact and internal review, leave the final receipt with the first line PLAN done.
  A PLAN progress comment or draft link alone does not complete it.
  If there are findings, go back to implementation.
  A node's skill called before the node is ready is refused.
  Pick a planning skill that fits the complexity, but add no Claude built-in Plan Mode or duplicate approval waits.
  A finished graph or a passed review grants no merge or deploy authority. Keep the user approvals required.

Status and troubleshooting
  ✔ done  ▶ ready  · waiting  ⊘ skipped  ⏸ external wait
  Remaining nodes or a pending retro block the session from ending. Do not skip without evidence; check wf_status.
  Updating the install leaves a running wf-mcp process as it is. Reconnect MCP or start a new session.
""",
    "cli.hook_help": "the hook Claude Code calls automatically with JSON stdin",
    "cli.hook_description": "For the Claude Code hooks. It takes an event and JSON stdin;"
                            " it is not a command for starting work.",
    "cli.event_help": "the configured hook event",
    "cli.status_help": "ready nodes of the current worktree and a summary of open tasks",
    "cli.status_description": "By default, the ready nodes of the current directory's task and"
                              " the number of other open tasks. --all shows the graph and"
                              " failure reasons of every open task. It never starts or"
                              " completes a task.",
    "cli.all_help": "graph, ready nodes and failure reasons of every open task",
    "cli.no_open": "wf: no open tasks",
    "cli.other_dirs": "wf: open tasks in other directories: {n}",
    "cli.no_other": "wf: no other open tasks",
    "cli.none_here": "wf: no open task in this directory · other directories: {n}",
}

KO: dict[str, str] = {
    "cli.description": "작업을 그래프로 추적하고, 각 단계의 check·증거·회고로 완료를 확인합니다.\n"
                       "CLI는 상태 조회와 자동 hook용입니다. 작업 관리는 wf-mcp 도구를 호출합니다.",
    "cli.epilog": """빠른 시작 (CLI)
  wf status                 현재 worktree의 ready 노드와 열린 task 요약
  wf status --all           열린 task 전체의 그래프·현재 노드·실패 사유
  wf status --help          상태 조회 도움말
  wf hook --help            Claude Code 자동 hook 안내 (일반 작업 시작용이 아님)

에이전트 작업 흐름 (wf-mcp 도구 호출이며 셸 명령이 아님)
  1. wf_list_types()로 맞는 유형을 찾고 wf_start로 작업을 시작합니다.
     wf_start(type="issue-resolution", dir="/worktree", issue_url="<GitHub 이슈 URL>")
     이슈가 없으면 issue_url 대신 title을 줍니다. 반환된 task ID로 이후 호출을 식별합니다.
  2. wf_status(task=123)로 ready 노드의 do·pitfalls를 읽습니다 (123은 반환된 ID).
     wf_set(task=123, key="complexity", value="simple")처럼 필요한 param을 설정합니다.
  3. ready 노드를 수행한 뒤 wf_complete(task=123, node="test", evidence="명령·결과")를 호출합니다.
     check가 실제로 통과해야 닫힙니다. 시작·수정·상태 변경 그래프를 그대로 보여줍니다.
  4. 실제 실패는 wf_fail(task=123, node="test", reason="실패 근거")로 복귀합니다.
     경로나 사용자 요청이 바뀌면 wf_revise(graph={{nodes: {{바뀐id: 필드}}}}, reason=..., task=123)
     — 그래프 전체를 다시 보내지 않는다. 요약이 바뀌면 title 도 준다.
     주인 없는 작업은 wf_attach(task=123).
     CI queued/running이나 외부 승인 대기는 실패가 아닙니다. 끝날 때까지 기다립니다.
  5. 마지막에는 wf_retro(task=123)로 근거를 읽고 verdict·deviations·필요한 reason을 기록합니다.
     이슈가 있으면 wf retro 코멘트를 남긴 뒤 완료합니다.
     wf_complete(task=123, node="retro", evidence="회고 근거")

리뷰·계획·승인 규칙
  skill 노드는 Skill 도구의 실제 호출이 필요합니다. 기준을 읽거나 말로 적용한 것은 호출이 아닙니다.
  리뷰 첫 줄은 ponytail <HEAD> 또는 리뷰/review <HEAD>이며 현재 SHA 7자/40자와 정확히 같아야 합니다.
  본문 SHA 언급은 증거가 아닙니다. 필수 스킬 영수증은 실제 호출 뒤에 남깁니다.
  계획 산출물·내부 검토를 끝낸 뒤 첫 줄 PLAN 완료로 최종 영수증을 남깁니다.
  PLAN 진행 댓글이나 초안 링크만으로 완료하지 않습니다.
  지적이 있으면 구현으로 복귀합니다.
  노드의 skill은 노드가 ready 되기 전에 호출하면 거부됩니다.
  복잡도에 맞는 계획 스킬을 고르되 Claude 내장 Plan Mode와 중복 실행 승인 대기는 추가하지 않습니다.
  그래프 완료나 리뷰 통과는 머지·배포 권한을 주지 않습니다. 필요한 사용자 승인을 지킵니다.

상태·문제 해결
  ✔ 완료  ▶ 실행 가능  · 대기  ⊘ 건너뜀  ⏸ 외부 대기
  남은 노드·회고가 있으면 세션 종료가 차단됩니다. 증거 없이 건너뛰지 말고 wf_status로 확인합니다.
  설치를 갱신해도 기존 wf-mcp 프로세스는 그대로입니다. MCP를 재연결하거나 새 세션을 시작합니다.
""",
    "cli.hook_help": "Claude Code가 JSON stdin으로 자동 호출하는 hook",
    "cli.hook_description": "Claude Code hook용입니다. event와 JSON stdin을 받아 실행하며 "
                            "일반 작업 시작 명령이 아닙니다.",
    "cli.event_help": "설정된 hook 이벤트",
    "cli.status_help": "현재 worktree의 ready 노드와 열린 task 요약",
    "cli.status_description": "기본은 현재 디렉터리 task의 ready 노드와 그 밖의 열린 task 수입니다. "
                              "--all 은 열린 task 전체의 그래프와 실패 사유를 보여줍니다. "
                              "task를 시작하거나 완료하지 않습니다.",
    "cli.all_help": "열린 task 전체의 그래프·ready 노드·실패 사유",
    "cli.no_open": "wf: 열린 task 없음",
    "cli.other_dirs": "wf: 다른 디렉터리의 열린 task {n}개",
    "cli.no_other": "wf: 다른 열린 task 없음",
    "cli.none_here": "wf: 이 디렉터리의 열린 task 없음 · 다른 디렉터리 {n}개",
}
