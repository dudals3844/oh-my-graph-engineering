"""server messages."""

EN: dict[str, str] = {
    "server.skills_nudge": "\nNext: call omge:improving-skills with the Skill tool to fold this"
                           " task's skill signals (procedure corrections, reusable techniques)"
                           " back in.",
    "server.one_of": "give exactly one of issue_url and task (id)",
    "server.no_open_task": "no open task — wf_start first",
    "server.closed": "task #{id} is already closed as {state} — wf tools handle only open tasks"
                     " and never reopen a closed task or record its retro. retro: {retro}",
    "server.no_node": "no node",
    "server.coverage": "Request check: before the first node, compare the steps the user asked"
                       " for (deploy, runtime/e2e tests, ...) against this graph's checks. Add a"
                       " node (with a check) via wf_revise for any step no check verifies — text"
                       " in another node's do is not a check.",
    "server.retro_skipped": "retro skipped (task #{id}): {reason}",
    "server.evidence_full": "\n…(full text: {path})",
    "server.review_prompt":
        "\n\nIndependent structural review input: {path} (task {id}, snapshot {snapshot})"
        "\nPass the file path above to an independent Agent reviewer — launch a new agent,"
        " not a fork (a fork inherits the whole parent context). Judge order, duplication,"
        " missing and rework, all on evidence. Tell approval/CI waits apart from defect"
        " rework. Do not judge by node names alone."
        " A failure or revision suggestion (S) repeated in 2 or more tasks goes into the graph"
        " structure (check, param, order, an earlier node), not into do/pitfall text, or into"
        " dismissed with a structural reason — 'it is already in do/pitfall' is not a reason"
        " to reject."
        " Judge each node in prior_changes on this run's evidence as helped/hurt/no observed"
        " effect/confounded; if this single task cannot tell, or several nodes changed"
        " together and cannot be separated, it is confounded; adoption (adopted) is not"
        " evidence of help; for hurt, raise a finding that reverts it."
        " The reviewer submits wf_retro(task=the task above, structural_review={{task,"
        " snapshot, axes: {{order: evidence, duplication: evidence, missing: evidence, rework:"
        " evidence}}, findings: [{{id, axis, evidence, proposal}}],"
        " prior_changes: [{{node, verdict, evidence}}],"
        " reuse_graph: the chosen graph,"
        " adopted: finding IDs to adopt}}) directly."
        " With no safe improvement, give reuse_graph \"default\" (do not rewrite the default"
        " graph), adopted=[] and the uncertainty/rejection grounds. Do not invent a fake edit."
        " To adopt an improvement, send only what changes, not the whole graph:"
        " reuse_graph={{base: \"default\", nodes: {{node id: fields to change (null deletes the"
        " field) | null (deletes the node)}}, params: {{name: regex | null}}}} — a new node goes"
        " before retro, so if retro must wait for it, fix retro's after too."
        " After the successful hook receipt the parent records the verdict with review_id"
        " (an omitted graph uses the receipt's); a start or Skill trace is not evidence.",
    "server.waive_reason": "wf_waive needs a reason",
    "server.not_pending": "`{node}` is not a pending node",
    "server.no_reviewer": "no independent reviewer (a subagent, not a fork) was called"
                          " — call wf_waive after that Agent call",
    "server.drop_reason": "wf_drop needs a reason",
    "server.not_open": "#{task} is not an open task",
    "server.paused": "wf #{id}: paused — the next ordinary prompt makes it active again.",
}

KO: dict[str, str] = {
    "server.skills_nudge": "\n다음: Skill 도구로 omge:improving-skills 를 호출해 이번 task 의 스킬 신호"
                           "(절차 교정·재사용 기법)를 반영한다.",
    "server.one_of": "issue_url 과 task(id) 중 하나만 준다",
    "server.no_open_task": "열린 task 가 없다 — wf_start 먼저",
    "server.closed": "task #{id} 는 이미 {state} 로 닫혔다 — wf 도구는 열린 task 만"
                     " 다루며 닫힌 task 를 다시 열거나 retro 를 기록하지 않는다. retro: {retro}",
    "server.no_node": "노드 없음",
    "server.coverage": "요청 대조: 첫 노드 전에 사용자가 요청한 단계(배포·runtime/e2e 테스트 등)를 이"
                       " graph 의 check 와 대조하라. 어느 check 로도 확인되지 않는 단계는 wf_revise 로"
                       " 노드(check 포함)를 추가한다 — 다른 노드의 do 문구에 적힌 것은 check 가 아니다.",
    "server.retro_skipped": "retro 건너뜀 (task #{id}): {reason}",
    "server.evidence_full": "\n…(전체: {path})",
    "server.review_prompt":
        "\n\n독립 구조검토 입력: {path} (task {id}, snapshot {snapshot})"
        "\n독립 Agent reviewer 에게 위 파일 경로를 전달하라 — fork 가 아닌 새"
        " agent 로 띄운다(fork 는 부모 컨텍스트 전체를 물려받는다). 순서(order),"
        " 중복(duplication), 누락(missing), 재작업(rework)을 모두 근거로 판단하라."
        " 승인/CI 대기는 결함 재작업과 구분하라. 노드명만으로 판단하지 말라."
        " 2 task 이상 반복된 실패·수정 제안(S)은 do·pitfall 문구가 아니라 graph 구조"
        "(check·param·순서·앞 노드)로 반영하거나 dismissed 에 구조적 이유를 적어라 —"
        " 'do/pitfall 에 이미 있다'는 거절 사유가 아니다."
        " prior_changes 의 각 노드를 이번 실행 근거로 helped/hurt/no observed effect/confounded"
        " 로 판정한다; 이번 task 한 번만으로 갈리거나 여러 노드가 함께 바뀌어 분리할 수"
        " 없으면 confounded; 채택(adopted)은 도움의 증거가 아니다;"
        " hurt 면 되돌리는 finding 을 낸다."
        " reviewer 는 wf_retro(task=위 task, structural_review={{task, snapshot,"
        " axes: {{order: 근거, duplication: 근거, missing: 근거, rework: 근거}},"
        " findings: [{{id, axis, evidence, proposal}}],"
        " prior_changes: [{{node, verdict, evidence}}],"
        " reuse_graph: 선택한 graph,"
        " adopted: 채택할 finding IDs}}) 를 직접 제출하라."
        " 안전한 개선이 없으면 reuse_graph 에 \"default\" 를 내고(기본 graph 를 다시"
        " 쓰지 않는다) adopted=[] 및 불확실/거절 근거를 내라. 가짜 edit를 만들지 말라."
        " 개선을 채택하면 전체 graph 대신 바뀐 것만 낸다: reuse_graph={{base: \"default\","
        " nodes: {{노드 id: 바꿀 필드(null 은 필드 삭제) | null(노드 삭제)}}, params: {{이름:"
        " regex | null}}}} — 새 노드는 retro 앞에 들어가니 retro 가 기다려야 하면 retro 의"
        " after 도 고친다."
        " 성공 hook receipt 이후 parent 가 review_id 로 판정한다(graph 는 생략하면"
        " 영수증의 것을 쓴다); 시작/Skill 흔적은 증거가 아니다.",
    "server.waive_reason": "wf_waive 에는 reason 이 필요하다",
    "server.not_pending": "`{node}` 는 남은 노드가 아니다",
    "server.no_reviewer": "독립 리뷰어(fork 가 아닌 subagent) 호출 기록이 없다"
                          " — 그 Agent 호출 뒤에 wf_waive",
    "server.drop_reason": "wf_drop 에는 reason 이 필요하다",
    "server.not_open": "#{task} 는 열린 task 가 아니다",
    "server.paused": "wf #{id}: paused — 다음 일반 프롬프트에서 active 로 돌아온다.",
}
