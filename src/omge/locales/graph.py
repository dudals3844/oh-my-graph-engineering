"""graph messages."""

EN: dict[str, str] = {
    "graph.new_match": "agent-submitted graph — the retro saves it to the library as new_type",
    "graph.retro_do": ("Call wf_retro() for the type's accumulated evidence and proposals, then record"
                       " wf_retro(verdict=no_change|update_type|new_type|skip). Nothing to improve:"
                       " wf_retro(verdict=\"skip\", reason=<why>). If there is an issue, leave a"
                       " comment starting with `wf retro` (with the library diff)."),
    "graph.legend": "✔ done ▶ ready · pending ⊘ skipped ⏸ waiting (external) ↺ back ✗ on fail",
    "graph.legend_box": " ⛔ stall 🔧 skill ┆=folded",
    "graph.on_fail_help": ("where to go when the check fails — an earlier (ancestor) node id to redo,"
                           " or `retry` to stay. e.g. {{id: test, check: ..., on_fail: implement}}."
                           " Optional: on_fail_max (default 3)"),
    "graph.check_trivial": "node {nid}: check {check} cannot fail — check something",
    "graph.on_fail_required": "node {nid}: a node with a check needs `on_fail` — {help}",
    "graph.on_fail_max": "node {nid}: on_fail_max {limit} must be an integer of at least 1",
    "graph.skill_field": ("node {nid}: `skill` is a skill name string or a list of them"
                          " (not allowed on retro)"),
    "graph.agent_field": ("node {nid}: `agent` is a subagent_type string or a list of them, and"
                          " `agent_mode` is require|recommend (not allowed on retro)"),
    "graph.carry_field": "node {nid}: `carry` takes only `code`, and only on a node with a check",
    "graph.gate_field": ("node {nid}: `protected` and `floor` are booleans, not on `retro`;"
                         " `floor` needs a check and no `carry`"),
    "graph.param_unsaid": ("node {nid}: check/when reads param {params} but no `do` of this node or"
                           " an earlier one names it — say in `do` what to wf_set (the value's"
                           " format, the file or key to create)"),
    "graph.when_trivial": "node {nid}: `when` {when} cannot split the branches — omit it for else",
    "graph.on_fail_not_ancestor": ("node {nid}: on_fail {to} is not an earlier (ancestor) node of"
                                   " this node — {help}"),
    "graph.fields_type": "node {nid}: fields must be an object or null (delete)",
    "graph.retro_after_needed": ("write retro.after in this patch — the older graph has no"
                                 " auto-recompute mark"),
    "graph.yaml_unreadable": "could not read graph YAML: {exc}",
    "graph.yaml_comment": ("YAML cut everything after `#` as a comment — quote that value or send"
                           " the graph as a JSON object"),
    "graph.not_object": "graph must be an object {{nodes: [...], params: {{...}}}}",
    "graph.nodes_needed": ("graph needs a `nodes` list (do/check/after/next) — retro alone will"
                           " not do"),
    "graph.check_needed": ("graph needs at least one node with a check — a graph with nothing to"
                           " check is just an unlock"),
    "graph.unset_note": ("  param {params} unset — the check cannot pass without it. Set it first"
                         " with wf_complete(params=…) or wf_set."),
    "graph.not_param": "`{name}` is not a param of this graph: {params}",
    "graph.param_mismatch": "param `{name}`={value} does not match {pattern}",
    "graph.task_open": "this issue already has open task #{id} ({type}) — wf_attach",
    "graph.issue_url": "issue_url must be a GitHub issue URL: {url}",
    "graph.title_needed": "without an issue, a title (a summary of the user's request) is required",
    "graph.dir_missing": "dir {dir} does not exist — the directory the checks run in",
    "graph.dir_absent": "dir {dir} does not exist",
    "graph.new_needs_graph": "type=new needs a submitted graph (YAML: nodes and optional params)",
    "graph.similar_type": ("a similar type is in the library: {hits} — start with that type."
                           " If it really is a new type, give the reason in new_reason"
                           " (it is recorded)."),
    "graph.graph_only_new": "graph is only for type=new — change a running task with wf_revise",
    "graph.issue_required": "this type needs an issue — give issue_url, or use another type or new",
    "graph.task_race": "another task was just opened on this issue — wf_status",
    "graph.revise_reason": "wf_revise needs a reason — it is recorded as a deviation",
    "graph.revise_held": ("cannot delete pending node(s) {ids} that still have a check, skill or"
                          " required agent — `wf_waive`"),
    "graph.revise_bare": ("cannot remove the check of pending node(s) {ids} — replace it with"
                          " another check or `wf_waive`"),
    "graph.revise_skill": "cannot change or remove the skill of pending node(s) {ids} — `wf_waive`",
    "graph.revise_agent": "cannot change or remove the agent of pending node(s) {ids} — `wf_waive`",
    "graph.revise_protected": ("`{nid}` is a protected gate — a running task cannot delete it, change"
                               " it, or detach what follows it; change the type and start a new task"),
    "graph.revise_summary": "changed: {parts} — {reason}",
    "graph.none": "none",
    "graph.skill": "skill",
    "graph.required": "required",
    "graph.recommended": "recommended",
    "graph.max": "(max {n})",
    "graph.why_exhausted": "on_fail limit exhausted",
    "graph.now": "  ◄ now",
    "graph.current": "current: {ids}",
    "graph.no_nodes_left": "no nodes left",
    "graph.done_prefix": "✔ {n} done: {path}",
    "graph.more_after": "… {n} more nodes after (full view in wf_status)",
    "graph.nodes_header": "nodes:",
    "graph.more_nodes": "… {n} more nodes",
    "graph.called": "called",
    "graph.not_called": "not called",
    "graph.status_skill": "  skill: {names} ({called})",
    "graph.status_agent": "  agent: {names} ({need}, {called})",
    "graph.last_failure": "  last failure exit={code}:\n{tail}",
    "graph.reuse_default_only": "reuse_graph \"default\" only works for a type in the library",
    "graph.reuse_patch_shape": ("reuse_graph patch is {{base: \"default\", nodes: {{id: fields|null}},"
                                " params: {{name: regex|null}}}}"),
    "graph.reuse_patch_node": ("reuse_graph patch node {nid}: a fields object, or null to delete an"
                               " existing node"),
    "graph.reuse_patch_param": ("reuse_graph patch param {name}: cannot delete a param that does"
                                " not exist"),
    "graph.reuse_patch_after": "reuse_graph patch node {nid}: after is a list of node ids",
    "graph.reuse_patch_cycle": "reuse_graph patch after/next form a cycle: {ids}",
    "graph.verdict_choice": "verdict is {choices}",
    "graph.deviations_needed": ("write in deviations how the actual path differed from the graph"
                                " ('none' if it did not)"),
    "graph.retro_open": ("node(s) {ids} not closed yet — the structural review is judged after the"
                         " actual path"),
    "graph.no_change_reason": "no_change needs structural grounds in reason even with deviations=none",
    "graph.adopted_no_change": "adopted improvements must be saved — no_change is not allowed",
    "graph.dev_review": "\nstructural review: {id}",
    "graph.dev_keep": "\nreason to keep the structure: {reason}",
    "graph.dev_dismissed": "\nrejection reasons: {dismissed}",
    "graph.new_must_save": ("a type=new task must leave its graph in the library — new_type or"
                            " update_type"),
    "graph.new_type_fields": "new_type needs a kebab-case name and a match sentence",
    "graph.type_exists": "type {name} already exists — update_type",
    "graph.similar_exists": ("a similar type already exists: {hits} — instead of a new type, fix"
                             " that type with update_type(name={first})"),
    "graph.type_missing": ("type {name} is not in the library — a type=new task must give name or"
                           " use new_type"),
    "graph.semantic_noop": ("reuse_graph is a semantic noop — with no improvement, give no_change"
                            " and its grounds"),
    "graph.weakened_reason": ("removes or changes a check, branch, predecessor, timeout or param"
                              " {ids} — reason required (it goes in the commit)"),
    "graph.msg_saved": ("\n\nSaved the reviewed reusable graph as the default type right away;"
                        " cleared the old candidate"),
    "graph.msg_check_change": "\n\ncheck change {ids} reason: {reason}",
    "graph.hardcoded": ("node(s) {ids} hardcode this task's issue number, branch or dir — use a"
                        " param or $WF_ISSUE_NUMBER/$WF_BRANCH/$WF_DIR"),
    "graph.msg_deviations": "\n\ndeviations: {deviations}",
    "graph.msg_applied": "\napplied proposals: {applied}",
    "graph.type_stale": "type {name} changed after the structural review — stale, review again",
    "graph.protected": ("protected-condition change {ids} — gates/params this task added or"
                        " changed; have an independent review finding name them and list that"
                        " finding in dismissed with a reason, or keep them in the default type"
                        " with update_type"),
    "graph.no_library_change": "(no library change)",
    "graph.retro_no_issue": ("retro recorded ({verdict}). There is no issue, so no comment:"
                             " wf_complete(retro).\n{diff}"),
    "graph.retro_issue": ("retro recorded ({verdict}). Leave a comment on the issue starting with"
                          " `wf retro` with the deviations, verdict and the diff below, then"
                          " wf_complete(retro).\n{diff}"),
}

KO: dict[str, str] = {
    "graph.new_match": "에이전트가 제출한 그래프 — 회고에서 new_type 으로 라이브러리에 남긴다",
    "graph.retro_do": ("wf_retro() 로 유형 누적 근거·제안을 받고,"
                       " wf_retro(verdict=no_change|update_type|new_type|skip) 로 판정을 기록한다."
                       " 개선할 것이 없으면 wf_retro(verdict=\"skip\", reason=사유) 로 건너뛴다."
                       " 이슈가 있으면 `wf retro` 로 시작하는 코멘트(라이브러리 diff 포함)를 남긴다."),
    "graph.legend": "✔ done ▶ ready · pending ⊘ skipped ⏸ waiting (external) ↺ back ✗ 실패 시",
    "graph.legend_box": " ⛔ stall 🔧 skill ┆=접힘",
    "graph.on_fail_help": ("check 가 실패하면 갈 곳 — 다시 할 앞(조상) 노드 id, 또는 제자리면 `retry`."
                           " 예: {{id: test, check: ..., on_fail: implement}}. 선택: on_fail_max(기본 3)"),
    "graph.check_trivial": "node {nid}: check {check} 는 실패할 수 없다 — 무언가를 확인하라",
    "graph.on_fail_required": "node {nid}: check 가 있는 노드는 `on_fail` 이 필요하다 — {help}",
    "graph.on_fail_max": "node {nid}: on_fail_max {limit} 는 1 이상의 정수여야 한다",
    "graph.skill_field": "node {nid}: `skill` 은 스킬 이름 문자열이나 그 목록이다 (retro 에는 못 단다)",
    "graph.agent_field": ("node {nid}: `agent` 는 subagent_type 문자열이나 그 목록,"
                          " `agent_mode` 는 require|recommend 다 (retro 에는 못 단다)"),
    "graph.carry_field": "node {nid}: `carry` 는 `code` 만, check 가 있는 노드에만 단다",
    "graph.gate_field": ("node {nid}: `protected`·`floor` 는 boolean 이고 `retro` 에는 못 단다;"
                         " `floor` 는 check 가 있어야 하고 `carry` 를 못 단다"),
    "graph.param_unsaid": ("node {nid}: check/when 이 param {params} 를 읽는데 이 노드나"
                           " 앞 노드의 `do` 가 그 이름을 말하지 않는다 — `do` 에 무엇을 wf_set"
                           " 해야 하는지(값의 형식, 만들어야 할 파일·키)를 적어라"),
    "graph.when_trivial": "node {nid}: `when` {when} 는 갈래를 가르지 못한다 — 빼면 else 다",
    "graph.on_fail_not_ancestor": "node {nid}: on_fail {to} 는 이 노드의 앞(조상) 노드가 아니다 — {help}",
    "graph.fields_type": "node {nid}: 필드는 객체이거나 null(삭제)이다",
    "graph.retro_after_needed": "retro.after 를 이 패치에 적어라 — 예전 그래프는 자동 재계산 표시가 없다",
    "graph.yaml_unreadable": "graph YAML 을 읽지 못했다: {exc}",
    "graph.yaml_comment": ("YAML 이 `#` 뒤를 주석으로 잘랐다 — 그 값을 따옴표로 감싸거나 graph 를"
                           " JSON 객체로 내라"),
    "graph.not_object": "graph 는 {{nodes: [...], params: {{...}}}} 객체여야 한다",
    "graph.nodes_needed": "graph 는 `nodes` 목록(do/check/after/next)이 필요하다 — retro 하나로는 안 된다",
    "graph.check_needed": ("graph 에 check 가 있는 노드가 하나는 있어야 한다 — 확인할 것이 없는 그래프는"
                           " 잠금 해제일 뿐이다"),
    "graph.unset_note": ("  param {params} 미설정 — check 가 이것 때문에"
                         " 통과할 수 없다. wf_complete(params=…) 나 wf_set 으로 먼저 설정하라."),
    "graph.not_param": "`{name}` 는 이 그래프의 param 이 아니다: {params}",
    "graph.param_mismatch": "param `{name}`={value} 가 {pattern} 에 맞지 않는다",
    "graph.task_open": "이 이슈에 열린 task #{id} ({type}) 가 있다 — wf_attach",
    "graph.issue_url": "issue_url 은 GitHub 이슈 URL 이어야 한다: {url}",
    "graph.title_needed": "이슈가 없으면 title(사용자 요청 요약)이 필요하다",
    "graph.dir_missing": "dir {dir} 가 없다 — check 가 실행될 디렉터리",
    "graph.dir_absent": "dir {dir} 가 없다",
    "graph.new_needs_graph": "type=new 는 graph(YAML: nodes 와 선택적 params) 제출이 필요하다",
    "graph.similar_type": ("비슷한 유형이 라이브러리에 있다: {hits} — 그 type 으로 시작하라."
                           " 정말 새 유형이면 new_reason 에 이유를 적어라 (기록된다)."),
    "graph.graph_only_new": "graph 는 type=new 에만 낸다 — 실행 중 수정은 wf_revise",
    "graph.issue_required": "이 유형은 이슈가 필요하다 — issue_url 을 주거나 다른 유형/new",
    "graph.task_race": "이 이슈에 방금 다른 task 가 열렸다 — wf_status",
    "graph.revise_reason": "wf_revise 에는 reason 이 필요하다 — 이탈로 기록된다",
    "graph.revise_held": "check·skill·필수 agent 가 남은 pending 노드 {ids} 는 지울 수 없다 — `wf_waive`",
    "graph.revise_bare": "pending 노드 {ids} 의 check 를 없앨 수 없다 — 다른 check 로 바꾸거나 `wf_waive`",
    "graph.revise_skill": "pending 노드 {ids} 의 skill 을 바꾸거나 없앨 수 없다 — `wf_waive`",
    "graph.revise_agent": "pending 노드 {ids} 의 agent 를 바꾸거나 없앨 수 없다 — `wf_waive`",
    "graph.revise_protected": ("`{nid}` 는 보호 게이트다 — 실행 중인 task 에서 지우거나 바꾸거나 그 뒤 노드를"
                               " 떼어낼 수 없다. type 을 바꾸고 새 task 를 시작한다"),
    "graph.revise_summary": "변경: {parts} — {reason}",
    "graph.none": "없음",
    "graph.skill": "스킬",
    "graph.required": "필수",
    "graph.recommended": "권장",
    "graph.max": "(최대 {n})",
    "graph.why_exhausted": "on_fail 한도 소진",
    "graph.now": "  ◄ 지금",
    "graph.current": "현재: {ids}",
    "graph.no_nodes_left": "남은 노드 없음",
    "graph.done_prefix": "✔ {n}개 완료: {path}",
    "graph.more_after": "… 이후 {n}개 노드 (wf_status 에서 전체)",
    "graph.nodes_header": "노드:",
    "graph.more_nodes": "… 외 {n}개 노드",
    "graph.called": "호출됨",
    "graph.not_called": "미호출",
    "graph.status_skill": "  스킬: {names} ({called})",
    "graph.status_agent": "  에이전트: {names} ({need}, {called})",
    "graph.last_failure": "  직전 실패 exit={code}:\n{tail}",
    "graph.reuse_default_only": "reuse_graph \"default\" 는 라이브러리에 있는 유형에만 쓴다",
    "graph.reuse_patch_shape": ("reuse_graph patch 는 {{base: \"default\", nodes: {{id: 필드|null}},"
                                " params: {{이름: regex|null}}}} 이다"),
    "graph.reuse_patch_node": "reuse_graph patch 노드 {nid}: 필드 객체이거나, 있는 노드를 지우는 null",
    "graph.reuse_patch_param": "reuse_graph patch param {name}: 없는 param 은 지울 수 없다",
    "graph.reuse_patch_after": "reuse_graph patch 노드 {nid}: after 는 노드 id 목록이다",
    "graph.reuse_patch_cycle": "reuse_graph patch 의 after/next 가 순환한다: {ids}",
    "graph.verdict_choice": "verdict 는 {choices}",
    "graph.deviations_needed": "deviations 에 실제 경로와 그래프의 차이(없으면 '없음')를 적어라",
    "graph.retro_open": "아직 닫히지 않은 노드 {ids} — 실제 경로 뒤에 구조검토를 판정한다",
    "graph.no_change_reason": "no_change needs structural grounds in reason even with deviations=없음",
    "graph.adopted_no_change": "채택한 개선은 저장해야 한다 — no_change 금지",
    "graph.dev_review": "\n구조검토: {id}",
    "graph.dev_keep": "\n구조 유지 사유: {reason}",
    "graph.dev_dismissed": "\n거절 사유: {dismissed}",
    "graph.new_must_save": "type=new task 는 그래프를 라이브러리에 남겨야 한다 — new_type 또는 update_type",
    "graph.new_type_fields": "new_type 은 kebab-case name 과 match 문장이 필요하다",
    "graph.type_exists": "유형 {name} 가 이미 있다 — update_type",
    "graph.similar_exists": ("비슷한 유형이 이미 있다: {hits} — 새 유형 대신"
                             " update_type(name={first}) 로 그 유형을 고쳐라"),
    "graph.type_missing": "유형 {name} 가 라이브러리에 없다 — type=new task 는 name 을 주거나 new_type",
    "graph.semantic_noop": "reuse_graph is a semantic noop — 개선 없으면 no_change 와 근거를 내라",
    "graph.weakened_reason": "check·갈래·선행·timeout·param 을 지우거나 바꾼다 {ids} — reason 필수 (커밋에 남는다)",
    "graph.msg_saved": "\n\n검토된 재사용본을 기본 유형에 즉시 저장; 기존 후보는 정리",
    "graph.msg_check_change": "\n\ncheck 변경 {ids} 사유: {reason}",
    "graph.hardcoded": ("노드 {ids} 가 이 task 의 이슈 번호·브랜치·dir 를 그대로 담았다 —"
                        " param 이나 $WF_ISSUE_NUMBER·$WF_BRANCH·$WF_DIR 로 바꿔라"),
    "graph.msg_deviations": "\n\n이탈: {deviations}",
    "graph.msg_applied": "\n적용한 제안: {applied}",
    "graph.type_stale": "유형 {name} 가 구조검토 뒤 바뀌었다 — stale, 다시 검토",
    "graph.protected": ("보호조건 변경 {ids} — task 가 추가·변경한 게이트/param"
                        " 이다; 독립 검토 finding 이 이름으로 다루고 그 finding 을"
                        " dismissed 에 사유와 함께 적거나, update_type 으로 기본"
                        " 유형에 남겨라"),
    "graph.no_library_change": "(라이브러리 변경 없음)",
    "graph.retro_no_issue": "retro 기록 ({verdict}). 이슈가 없으니 코멘트 없이 wf_complete(retro).\n{diff}",
    "graph.retro_issue": ("retro 기록 ({verdict}). 이슈에 `wf retro` 로 시작하는 코멘트로 이탈·판정·아래"
                          " diff 를 남기고 wf_complete(retro).\n{diff}"),
}
