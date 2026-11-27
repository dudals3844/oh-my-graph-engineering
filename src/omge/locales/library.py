"""library messages."""

EN: dict[str, str] = {
    "library.git_failed": "library git {cmd} failed: {out}",
    "library.stat_none": "none (n=0)",
    "library.past": "    past: {title}",
    "library.no_match": "(no matching type: {query})",
    "library.empty": "(library is empty)",
    "library.current": "current",
    "library.previous": "previous",
    "library.version_score": "  {label} {sha}: score {stat}",
    "library.version_stats": "    started {started} · done {done} · stall {stalled} · waived"
                             " {waived} · avg deviations {dev:.1f} · avg completion {hours:.1f}h",
    "library.worse": "  ⚠ the current version scores {by} or more below the previous one"
                     " — consider reverting: {fix}",
    "library.fit": "This template explains {fit:.0%} of the actual path (precision {prec:.0%})"
                   " — mean over {n} tasks",
    "library.fit_mine": "  This task: explains {fit:.0%} of the actual path (precision {prec:.0%})"
                        " · score so far {score:.2f}",
    "library.type_header": "Type {type}: {n} past tasks",
    "library.node_avg": " · avg {secs:.0f}s",
    "library.node_line": "- {node}: failed {failed}/{tried}{avg} · waived {waived} · skip {skipped}"
                         " · back {back} · revised {revised} · wf_fail calls {wf_fail}",
    "library.s_retry": "`{node}` (on_fail: retry) failed {n} times — add an edge back to an"
                       " earlier node (on_fail: <earlier node>)",
    "library.s_waived": "`{node}` waived in {n}/{total} tasks — removal candidate",
    "library.s_revised": "{n} tasks fixed `{node}` with wf_revise — fold that change into the"
                         " template's check·do·param",
    "library.s_back": "`{node}` → `{detail}` went back {times} times in {n} tasks — add a check"
                      " before `{detail}`",
    "library.s_fail": "`{node}`: same failure in {n} tasks: {detail!r} — pitfall/check candidate",
    "library.s_promote": "`{node}` added by wf_revise recurs in {n} tasks — promote it into the"
                         " template",
    "library.s_carry": "HEAD receipt nodes {nodes} lack `carry: code` — a commit with the same"
                       " code (whitespace, lint) buys the review again: add `carry: code`",
    "library.s_chain": "HEAD receipt nodes {nodes} run serially after another receipt node —"
                       " move them after the same predecessor so the reviews run in parallel"
                       " and the fix loop runs once",
    "library.s_fit": "The template explains only {fit:.0%} of the actual path — fold the"
                     " off-template steps (wf_revise additions, order, going back) into it",
    "library.s_idle": "Only {prec:.0%} of the template's nodes are used — nodes unused in most"
                      " tasks {rare}: candidates to remove or split into branches",
    "library.none": "none",
    "library.suggestions": "Suggestions:",
    "library.no_suggestions": "Suggestions: none",
    "library.versions": "Versions:",
    "library.not_in_library": "  (type not in the library)",
}

KO: dict[str, str] = {
    "library.git_failed": "라이브러리 git {cmd} 실패: {out}",
    "library.stat_none": "없음 (n=0)",
    "library.past": "    과거: {title}",
    "library.no_match": "(일치하는 유형 없음: {query})",
    "library.empty": "(라이브러리 비어 있음)",
    "library.current": "현재",
    "library.previous": "직전",
    "library.version_score": "  {label} {sha}: 점수 {stat}",
    "library.version_stats": "    시작 {started} · 완료 {done} · stall {stalled} · 면제"
                             " {waived} · 평균 이탈 {dev:.1f} · 평균 완료 {hours:.1f}h",
    "library.worse": "  ⚠ 현재 버전 점수가 직전보다 {by} 이상 낮다 — 되돌리기 검토: {fix}",
    "library.fit": "이 템플릿은 실제 경로의 {fit:.0%} 를 설명 (precision {prec:.0%})"
                   " — task {n}건 평균",
    "library.fit_mine": "  이 task: 실제 경로의 {fit:.0%} 를 설명 (precision {prec:.0%})"
                        " · 점수(지금까지) {score:.2f}",
    "library.type_header": "유형 {type}: 과거 task {n}건",
    "library.node_avg": " · 평균 {secs:.0f}s",
    "library.node_line": "- {node}: 실패 {failed}/{tried}{avg} · 면제 {waived} · skip {skipped}"
                         " · 되돌아감 {back} · 수정 {revised} · wf_fail 호출 {wf_fail}",
    "library.s_retry": "`{node}` (on_fail: retry) 가 {n}회 실패"
                       " — 앞 노드로 되돌아가는 간선(on_fail: <앞 노드>) 추가",
    "library.s_waived": "`{node}` 가 {n}/{total} task 에서 면제 — 제거 후보",
    "library.s_revised": "`{node}` 를 {n} task 가 wf_revise 로 고쳤다"
                         " — 그 변경을 템플릿 check·do·param 에 반영",
    "library.s_back": "`{node}` → `{detail}` 되돌아가기 {times}회·{n} task"
                      " — `{detail}` 보다 앞 단계에 check 추가",
    "library.s_fail": "`{node}` 의 같은 실패가 {n} task: {detail!r} — pitfall·check 후보",
    "library.s_promote": "wf_revise 로 추가된 `{node}` 가 {n} task 에서 반복 — 템플릿에 승격",
    "library.s_carry": "HEAD 영수증 노드 {nodes} 에 `carry: code` 가 없다 — 코드가 같은 커밋"
                       "(공백·lint)에도 리뷰를 다시 받는다: `carry: code` 추가",
    "library.s_chain": "HEAD 영수증 노드 {nodes} 가 다른 영수증 노드 뒤에 직렬이다 — 같은"
                       " 선행 노드 뒤로 옮겨 리뷰를 병렬로 받고 수정 루프를 한 번으로",
    "library.s_fit": "템플릿이 실제 경로의 {fit:.0%} 만 설명 — 템플릿 밖 단계(wf_revise 추가"
                     "·순서·되돌아감)를 템플릿에 반영",
    "library.s_idle": "템플릿 노드의 {prec:.0%} 만 쓰인다 — 과반 task 에서 안 쓰인 노드"
                      " {rare}: 제거·갈래화 후보",
    "library.none": "없음",
    "library.suggestions": "제안:",
    "library.no_suggestions": "제안: 없음",
    "library.versions": "버전:",
    "library.not_in_library": "  (라이브러리에 없는 유형)",
}
