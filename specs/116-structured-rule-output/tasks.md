# tasks 116 — Rule·Example 구조화 출력 v2

각 단계 = 구현→단위검증→반증검증→회귀 한 묶음. 체크는 증거가 있어야만 한다.
범위 밖 결함·원칙 위배 발견 시 **발견 즉시 이 파일에 편입**(보류 금지 — spec §15).

> **현재 작업 진입점(2026-08-05):** 이 파일의 과거 완료 체크에는 이후 반증·폐기된 설계가
> 포함된다. 현재 계약과 재개 순서는
> `specs/131-cross-node-semantic-grounding/history/handovers/README.md` 및
> `05-spec116-정정표.md`를 먼저 따른다. 특히 값·객체 추적, 내용 재요청, 전체 호출 fatal,
> arbitrary Python 의미의 결정론 자동 판정은 되살리지 않는다.

## 현재 활성 작업 — 2026-08-05 정본 동기화 이후

- [x] TA-145 **단일 운영 정본 생성 및 spec/plan/tasks 오인 방지 배너 동기화.**
      `specs/131-cross-node-semantic-grounding/history/handovers/`에 절대 계약, 책임 경계,
      성능 실측, 직접 검증법, spec 정정표, 재개 절차를 분리 기록했다. 사용자 진입 경로
      `D:\work\robo\project\robo-data-analyzer\specs\131-cross-node-semantic-grounding\history\handovers`은 동일 Git 정본을 가리킨다.
- [ ] TA-146 **Rule compact regex의 의미 동등성 판정과 공식 자료 조사.**
      실제 probe에서 output 25.5~52.6% 감소가 확인됐으나 한 extra 후보가 1→0으로 달라졌다.
      source/DDL/evidence와 grader를 직접 대조해 어느 후보가 맞는지 판정하고, SGLang 공식
      문서/소스·현재 OpenAPI·실배포 probe를 조인한다. 판정 전 production 채택 금지.
      딥리서치 반영(2026-08-05, handoff `03 §8.1`): 공백 제거는 요청수준 regex/EBNF만 가능
      (json_schema 불가, sglang #8250), 거대 regex 대신 EBNF+공유 규칙, extra 1→0은 분포
      왜곡 가설(Park/DISC)이 유력 — whitespace·nl 상한·enum 폐쇄·field 순서를 각각 독립
      1회 A/B로 분리 판정. **선행 게이트**: sglang #6675(Qwen3 thinking-off 구조화 실패)
      해당 버전 여부를 `/get_server_info` 프로브로 확인.
- [ ] TA-147 **Rule reference를 evidence-specific하게 닫는 범용 계약 검토.**
      현재 `C<number>`/`E<number>` regex는 모양만 닫고 요청에 없는 번호는 grader에서 거른다.
      요청별 enum/regex가 schema 크기·grammar compile·정확도·출력 토큰에 미치는 영향을 제한
      replay로 측정하고, 언어·함수·코드값 특화 없이 이득이 있을 때만 채택한다.
- [ ] TA-148 **scheduler revert 이후 회귀와 공식 cold 승인.**
      scheduler revert와 검증 도구 정리 이후 전체 unittest 867개 OK(skipped=5), prompt 28/28
      byte-identical, 관련 표적 31개 및 retained CLI 직접 실행이 통과했다(2026-08-05 01:28).
      compact 변경이 승인되면
      cache=false/concurrency64/Neo4j test cold를 실행해 wall 18분대 이하, trusted >=4,016,
      obligation4,100, unavailable0을 모두 확인한다. 11~15분은 추가 개선 목표다.
- [ ] TA-149 **전체 결정론·직접 의미 검증과 Stage 8 delta.**
      CALL/RW/RETURN/Rule-branch/Example 전수 대조, 코드노드 20개와 모든 테이블의 모든 LLM
      속성 및 비AST 관계 직접 의미 대조를 수행한다. 방법은 handoff `04-검증방법.md`를 따른다.
- [x] TA-150 **검증 도구의 폐기 설계·언어 특화·환경 경로 정리.**
      arbitrary source를 Python 의미식으로 번역하던 일회성 notation 벤치 2개를 제거하고 역사
      결과만 spec에 남겼다. prompt evidence audit에서 대문자/C형 호출 휴리스틱을 제거해 정확한
      evidence 행 도달만 검사하게 했고, semantic packet builder의 사용자/Claude 임시경로
      기본값을 없애 모든 입력을 required CLI 인자로 바꿨다. `_runs/`는 고객 prompt/evidence가
      원격에 섞이지 않도록 ignore했다.

- [ ] TA-151 **parser 소유 구조 구문으로 전환하고 analyzer 중복 source parser를 제거.**
      진행(2026-08-05): parser 측 생산 계약 구현 완료 — `Node.expression/target/operator` 신설,
      C(jumpStatement→RETURN/BREAK/CONTINUE/GOTO, statement-level ASSIGNMENT, IF/SWITCH/LOOP
      test-only/CASE 조건), Java(RETURN/THROW/BREAK/CONTINUE/ASSIGNMENT/조건), Python(RETURN/
      RAISE/BREAK/CONTINUE/ASSIGNMENT/조건), PLSQL/PG(기존 RETURN·ASSIGNMENT에 표현 필드,
      IF/ELSIF/WHILE/CASE/WHEN 조건). leaf emit 으로 기존 FUNCTION_CALL 수·부모 보존.
      red 7건→green, parser 전체 132 tests OK, golden byte 계약은 diff 전수 검토 후 재생성.
      Java WS skip 토큰 결합의 원문 훼손(`x + 1`→`x+1`) 실결함을 exact CharStream 슬라이스로 수리.
      analyzer 소비 전환도 구현: ontology `statement` role, CodeNode 표현 필드+graph 저장,
      reader `_fw_statement`, step6 statement/call-line facts, `build_inventory` AST 경로
      (종료행·multiline 반환식·대입·entry·직접호출). AST-경로 반증 6/6, 전체 873 tests OK,
      prompt 28/28. **미완**: E-3b 조건식 전환(CASE default 구조 판정 포함), 지역 선언
      초기화 합성, corpus 재파싱 후 source↔AST 0-delta, legacy scanner·ontology keyword 삭제,
      PLSQL EXIT/RAISE emit 판단. 상세는 handoff `06 §3.1`.
      실제 C AST corpus의 `RETURN` node 0과 PL/SQL listener의 RETURN/ASSIGNMENT emit을 대조해
      언어별 계약 불균일을 확인했다. `branch_inventory.py`의 `_termination_rows`,
      `_return_expressions`, `_calls`, `_assignment_pairs`, `_entry_statement_line`, condition/source
      재구성과 기타 pattern scanner를 전수 분류한다. grammar가 아는 구문은 parser spec
      `antlr-code-parser/specs/016-structural-statement-ast-contract/`에 따라 listener/AST JSON으로
      이동하고 analyzer는 언어중립 role만 소비한다. 지원 언어별 source occurrence↔AST node의
      missing/extra/wrong-range/wrong-parent 0, analyzer AST-only 통합, parser/analyzer 전체 회귀,
      실제 corpus 재파싱을 통과한 뒤에만 legacy scanner를 삭제한다. parser와 analyzer 양쪽 fallback
      병존, 언어명/키워드 특화, 값·객체 상태 추적은 금지한다.
- [x] TA-152 **무한 반복 종료선 정본화.**
      model content retry 0, transport 최대 3 attempts, 동일 공통 원인의 범용 prompt/schema 수정
      1회 replay, 후보당 cold 1회(경계 불명확 시 확인 1회), 18분대+정확도 기준 이후 안전한 범용
      개선이 없으면 성능 루프 종료를 handoff `01` §6과 `06` §4.1에 기록했다.

> **아래 0단계부터 TA-144까지는 역사 실행 원장이다.** 완료 체크는 당시 증거를 보존할 뿐 현재
> 채택을 뜻하지 않으며, 미완료 체크도 위 TA-146~149보다 우선하지 않는다.

## 0단계 — 파이프라인 수리 (선행)

- [x] T0-1 raw 보존 수리 — 실측 정정: include_raw fallback이 raw를 이미 보존(langchain
      1.3.5 원문 확인). 실제 결함 2건 수리: ① parsing_error 사유를 metadata+raise에 전파
      (`yaml_llm_runner.py`) ② RepetitionLoopAborted에 partial_text 탑재
      (`repetition_guard.py`, `invocation.py` `_extract_partial_content` 일반화)
- [x] T0-2 자연어 필드 괄호검사 제거: `_require_closed_semantic_text` 헬퍼·호출 삭제,
      빈 statement·완전 중복 거절은 유지 (`pipeline/models/code_analysis_output.py`)
- [x] T0-3 반증: `tests/llm/test_failure_evidence_contract.py` — 검증 실패 주입 시 raw 보존
      +사유 표면화 / 폭주 중단 partial 기록 / 짝 없는 괄호 statement·example 통과 /
      빈 statement 거절 유지 (34/34)
- [x] T0-4 회귀: 전체 unittest 583/583 OK (skipped 5) — 2026-08-03

## 1단계 — 분기표 강화 (정답지 승격)

- [x] T1-1 종료문 행: 문장 첫 토큰 기반 [exit]/[handler] 행. 종료 키워드는
      `ontology_roles.yaml`(언어 표준만), 핸들러 마커(PFM_CATCH)는 **SKILL 카드 fence →
      ScanResult.handler_markers → step7 ctx.extra → build_inventory 주입**.
      분기 행과 같은 줄이면 분기 행이 대표(줄번호 유일 유지)
- [x] T1-1a **원칙 §8 오염 정정(사용자 지적, 발견 즉시 편입)**: 최초 구현이 PFM_CATCH를
      범용 C 언어 표에 굳혔음 — 프레임워크 지식의 소유자는 SKILL 카드다. lexicon에서
      제거하고 skill 계약 주입 사슬로 교체(contracts.py·catalog.py·scan_codebase_step·
      step_entry·control_prompt). 미주입 시 [handler] 행이 생기지 않는 반증 테스트 추가.
      실측 재확인: z10020 80→87행 동일, 전체 593/593
- [x] T1-2 else-if 부정 보존: `_complement_context`/`_paired_decision` — 파서의 두 형상
      (else=형제 / else=자식)을 모두 판정. 직접 짝 decision 부정 + 외곽 decision 별
      성립/부정 명시. "조건도 상속" 문구 제거
- [x] T1-3 case 값 정규화: `canonical_numeric`(8진·16진→십진) — CASE 행에 "(수치 02=2)"
      병기, `octal_case_labels` 언어 플래그(c·java)
- [x] T1-4 JSON 실체화: `BranchInventory.table_rows()`(조건원문·코드값·canon·callees·
      effect_atoms·complement 부정 좌표) + fanout `_persist_branch_table` →
      `branch-tables/<대상>.json` (실패는 log_warning 표면화 후 계속)
- [x] T1-5 검증: zapamcom10020 실측 **80→87행**(+return 5·PFM_CATCH 1·catch-return 1 —
      사고①의 소실 종료 계약이 전부 행으로 복원) / 부정 문구·형상 2종·switch default·
      종료문·8진·스냅샷 단위테스트 신설 / 전체 회귀 592/592 OK (2026-08-03)

## 2단계 — 값·객체 추적기 (1단계와 순서 교환 가능)

- [x] T2-1 위치별 마지막 대입: `pipeline/quality_gates/value_tracking.py` 신규 —
      `Assignment`/`ValueTrace.last_assignment_before(symbol, 절대줄)`. 한 줄 문장만
      읽고 못 읽는 쓰기는 미상으로 센다(지어내기 금지 — 모듈 독스트링에 한계 명시)
- [x] T2-2 callee 반환집합: `ValueTrace.return_values()`(반환식 원문 집합) →
      `ctx.extra["return_values"]` 로 bottom-up 동승, caller 의
      `callee_search_texts` JSON 에 `returns` 필드로 노출(cycle 등 부재 시 생략)
- [x] T2-3 lexicon 확장(`ontology_roles.yaml`): assignment_operators(sql=`:=`)·
      write_through_calls(strcpy/strncpy/memcpy/sprintf/snprintf — **언어 표준만**,
      source_from 으로 size 인자 제외)·comparison_idioms(strcmp/strncmp/memcmp)
- [x] T2-4 proframe 카드 `api_contracts` fence(mpfmdbio: sql_key·success_value=RC_NRM /
      mpfm_dlcall: callee_module — 문서 보증 사실만) + `SKILL_CONTRACT_KEYS` 등록.
      소비는 4단계 채점기가 `load_skill_contract(matched_skill)` 로 직접
- [x] T2-5 미상 카운터: `unknown_writes` + fanout `value_trace_unknown_writes` 경고 로그
- [x] T2-6 검증: 표 주도 단위테스트 13종(4개 언어·오탐 차단·미상 반증) / 시료 대조
      b100(대입 6·반환 4·미상 0·반환집합 {RC_ERR,RC_NRM})·z10020(대입 8·반환 6·미상 0·
      strncpy 6건 — py1 채점 대조 사실과 일치; b450 원본은 _runs 삭제로 소실, z10020 대체) /
      실측 중 오탐 2종 발견·수리(문자열 안 `=` 미상 오인, snprintf size 인자 혼입) /
      전체 회귀 604/604 OK (2026-08-03)

## 3단계 — 출력 계약 교체 (4단계보다 반드시 선행)

- [x] T3-0a **선행 프로브 완료(2026-08-03, 8콜)**: 문법 강제 **작동** — 위반 유도 5/5
      마스킹(enum·pattern), xgrammar pattern regex 지원 확인. **발견**: 운영 전송은
      function_calling이 아니라 `response_format json_schema strict`(httpx wire 캡처로
      확정 — langchain-openai 1.3.5 자동 선택). 헌법 VII 전제와 불일치 — hang 미재현,
      개정은 사용자 governance 안건. 직접 tools+tool_choice도 tool_calls 정상
- [x] T3-0b **선행 스모크 완료**: min_new_tokens **미작동**(200 요청→completion 2) —
      T3-4의 min_new_tokens 배선 취소. 축퇴 차단은 뺄셈·행 재요청 전담(4단계)
- [x] T3-1 어휘 단일 정의 모듈 `pipeline/models/rule_expression.py` — EFFECT_FUNCTIONS(8)·
      COND_HELPERS(F/else_of; `starts_with`는 TA-82에서 제거)·LEAF_PATTERN(개행금지+500자 = 문법강제 끝 경계)·
      parse_condition/parse_effect(행 단위 거절 사유용 예외 타입)
- [x] T3-2 새 Pydantic 스키마: `RulesExamplesV2Output`/`RuleV2Item`/`ExampleV2Item` —
      anchor_line 첫 필드(evidence-first echo)·cond/then leaf pattern·examples=파이썬 등식·
      nl=pattern 상한(개방형 마지막이어도 끝 경계)·Rule 상한 제거·ast는 채점기 몫
      (모델 validator에 넣으면 행 하나가 전체 거부 — 행 단위 재요청 설계 보존)
- [x] T3-3(부분) **감사 블록 유/무 A/B 실측 완료(2026-08-03, 2콜)**: 신 계약으로 z10020
      20행 — 무감사 20/20·ast_fail 0·사유 0(py1 재현 성공) vs 감사 블록 추가 시
      **ast_fail 11**(then에 break 혼입). **"제출 직전 완전성 감사" 블록 제거 확정**
      (R′-⑦ 자체 실증). 증거: `evidence-…/v2test/py2_*.parsed.json`, `py2_ab_report.txt`.
      남은 것: yaml 실제 교체(fanout 전환과 원자적으로 — 4단계 통합 시)
- [ ] T3-4 ~~min_new_tokens 배선~~(스모크 실측 미작동 — 취소), max_tokens 72tok/rule 기준 재산정
- [ ] T3-5 enrichment 코드 채움: rule_id="R-"+anchor, when=함수심볼, affected_tables 연결
- [ ] T3-6 프롬프트 해시 갱신 + `check_byte_identical` 통과(사유 기록)
- [ ] T3-7 검증: harness py1 재현(신 계약 20/20·ast_fail 0) / 어휘 drift 테스트 / 회귀

## 4단계 — 채점기 + 배칭

- [x] T4-1 grade_py 제품화: `pipeline/quality_gates/rule_grading.py` — 행 단위
      RowVerdict/GradeReport, 확실한 위반만 거절(문법·어휘·창작·모순·echo), 하네스 오탐
      2종 수리(else_of 참조는 형식·실재 검사 / F() 원문은 부분문자열 검사),
      retry_message()가 실패 사유·행 번호 구체 명시
- [x] T4-2 ast 정규화 비교기: normalize/canonical_form/conditions_equal — 교환법칙
      정렬(BoolOp 평탄화+정렬)·이중부정·`not(a==b)`→`a!=b`·상수측 통일. 구현 중
      반증 테스트가 실결함 검출(자식 교체 미반영 시 이중부정 의미 반전) → 수리
- [ ] T4-3 분기표 대조: 커버리지 뺄셈·조건 트리/집합·then↔callees/effect_atoms 등호
- [x] T4-4 사전 예측 배칭: `pipeline/steps/step7_analyze_code/rule_row_batching.py` —
      `plan_batches`(의무 행 분할, 기본 20행/콜=py1 실측 72tok/rule 기준)·입력 통째 유지.
      남은 것: fanout 의 rules concern 을 이 오케스트레이션으로 전환(yaml 교체와 원자적)
- [x] T4-5 행 단위 거절·재요청: `collect_rules_by_rows` — 실패 행만 구체 사유
      (retry_message)와 함께 재요청, 상한 3(MAX_ROW_ATTEMPTS), 소진 시 unresolved
      표면화, 통과 행 재생성 금지. 반증 테스트 4종(재요청 할당 축소·상한 정지·
      unresolved·구체 사유 포함) — 전체 회귀 625/625 (2026-08-03)
- [x] T4-6(부분) 반증: `tests/pipeline/quality_gates/test_rule_grading.py` 17종 —
      가짜 행·반전(모순 AND)·창작·어휘 밖·축퇴(뺄셈)·중복·할당 밖·행 단위 격리
      (옆 행 생존)·else_of 미실재 참조·재요청 메시지 구체성·정규화 동치 5종 전부 검출/통과.
      남은 것: 상한 소진 시나리오(재요청 오케스트레이션과 함께)
- [ ] T4-7 실측: b200급 배칭 1회(소량 API — 대량이면 사용자 통지)

## 전환 슬라이스 설계 (2026-08-03 실코드 조사로 확정 — 다음 세션은 이대로 구현)

원칙 재확인(사용자 지시): **데드 요소·DRY 엄수** — v1 잔재를 죽은 코드로 남기지 않는다.

- [x] TS-1 **완료(2026-08-03)** 원자적 전환((b) 결합): yaml v2 교체 + fanout rules concern
      v2(collect_rules_by_rows) + 빌더 v2 개조(Rule id=`<fid>::R<anchor>`·규칙 단위
      AFFECTS·v1은 dbms 공존 분기, 미지 형식은 즉시 실패) + graph 노드/Cypher v2
      (RULE: rule_id·anchor_line·cond·then[]·nl·when / EXAMPLE: kind·given[]·then_[]) +
      해시 갱신(32 prompts OK). **중간 상태 부재 증명**:
      `test_rules_v2_transport_contract.py` — fanout 직렬화→빌더 통과·RULE/EXAMPLE/
      AFFECTS 생성 단언 + 미지 형식 즉시 실패 + v1 공존 생존 + 미해석 테이블 표면화.
      구현 중 실결함 1건 검출·수리(빌더 마지막 줄이 v2 rules를 rule_by_hash로 덮어씀)
- [ ] TS-2 fanout 전환 상세(`three_split_llm_fanout.py` 248-343행 기준):
      rules concern의 `run_concern_with_retry`+`run_yaml_rule(RulesExamplesOutput)` →
      `collect_rules_by_rows`(invoke는 run_yaml_rule로 `{assigned_rows}`/`{retry_feedback}`
      치환, **feedback에 재요청 회차 명시**로 response_cache 동일키 재적중 방지).
      예외 정책: BudgetExceededError 전파 / RepetitionLoopAborted·기타 예외는 해당 배치
      pending을 unresolved로 표면화하고 그 배치 중단(반복가드 "재시도 없음" 계약 유지) —
      `collect_rules_by_rows`에 이 정책 반영+반증 테스트.
- [ ] TS-3 v2 전환 시 정리(데드 차단): rules 경로의 `check_rule_quality` 호출 은퇴(채점기가
      대체 — rule_quality.py는 dbms가 소비하는지 확인 후 처분), `_measure_grounding`/
      `check_grounding` 호출 은퇴(grade가 대체 — grounding.py 소비자 grep 후 처분),
      `constrain_tables_to_rw`의 rules 인자 제거(v2는 LLM affected_tables 없음),
      `serialize_rules_with_table_effects` → v2 직렬화로 교체,
      `_format_child_business_logic`의 `statement` → v2 `nl`/cond·then 렌더.
      **5·6단계 완료 시 v1 모델(RulesExamplesOutput 등)·rule_evidence_contract.md의
      framework 참조 grep 0 확인.**
- [ ] TS-4 enrichment(구 T3-5): fn.extra["rules"] v2 직렬화 형식 =
      {rule_id:"R-<anchor>", anchor_line, cond, then, nl, when:<함수 심볼 — 코드가 채움>,
      examples:[{kind,given,then}], affected_tables:<결정론 — then의
      select/insert/update/delete("테이블")를 reads/writes·table_op과 대조해 코드가 부착>,
      grading:{unresolved,calls,retries}}. 노드 id는 fn.id 결합으로 전역 유일(빌더 소관).
- [ ] TS-5 yaml: `code_rules_examples.yaml` user_template_specific을 py2 검증본(INSTRUCTIONS,
      감사 블록 없음 — evidence/scratchpad `rerun_py2_z10020.py` 원문)으로 교체,
      evidence 절에 `{assigned_rows}`/`{retry_feedback}` placeholder, max_tokens 6000
      (72tok/rule×20행×여유). extends `_common` 유지(시스템 byte-identical 불변).
      `prompt_hashes.json` 갱신+사유. 고정 테스트(test_semantic_generality_contract 등)
      의 yaml 구조 단언 갱신.
- [x] TS-2 **완료**: fanout 전환 + 피드백 회차 명시(캐시 동일키 방지) + 예외 정책
      (Budget 전파 / RepetitionLoopAborted 재요청 없음 / **ValidationError=검증 실패 →
      행 재요청 축**(TS-6 실측이 잡은 정책 결함 수리) / 기타 예외 배치 누락 표면화) —
      반증 테스트 8/8
- [x] TS-3(부분) v1 잔재 은퇴: framework 경로에서 check_rule_quality·grounding 측정·
      serialize_rules_with_table_effects(rules 인자)·"제출 직전 감사" 제거. 잔존 v1
      (RulesExamplesOutput·rule_quality.py·grounding.py·rule_evidence_contract.md·빌더
      v1 분기·Rule.statement/Example.when 레거시 필드)은 **dbms 소비 중 — 5단계 완료 시
      삭제+grep 0 게이트**
- [x] TS-4 **완료**: `serialize_rules_v2`+`v2_rule_table_effects`(rw_table_filter) —
      rule_id/when 코드 채움, then의 select/insert/update/delete→R/W 대조 결정론 귀속
      (권위 table_op=SCANNER), 미해석은 생략+경고. `fn.extra["rule_grading"]`
      (obligation_rows·unresolved·calls·retries·row_miss_rate) + 축퇴율 경고 로그(§11.1)
- [x] TS-5 **완료**: yaml v2(§16-3 A/B 검증본, 할당·재요청 placeholder, max_tokens 6000,
      extends _common 유지) + prompt_hashes 갱신(사유: v2 계약 교체) + 고정 테스트 4종
      v2 갱신(dbms v1 고정은 유지)
- [x] TS-6 **완료**: 대형 배칭 실측 — z10020 전 행(b200 소실 대체): 의무 79행→4배치,
      5콜(재요청 2), **수확 78/79**, unresolved `687`(축퇴율 1.27% — §11.1 근거 데이터).
      행 687 서사가 계약 실증: 파싱실패→구체사유 재요청→창작 함수명 검출→pattern 위반→
      상한 소진→누락 표면화. 발견: **array items pattern은 xgrammar 미강제 정황**(클라이언트
      Pydantic이 차단) — 서버 안건 근거 병기. 증거: evidence v2test/py3_full_*. §18-3 해소

## 5단계 — DBMS v2 적용 (실코드 조사 2026-08-03 — 이 설계대로 구현)

현행: `batch_analyze._analyze_batch`가 `_collect_keyed_concern`으로 형제 노드 배치 —
rules 2변종(`code_rules_examples_batch` + `code_gate_rules_batch`(skeleton_body)),
`BatchRulesOutput{nodes:[BatchRulesItem{node_key, rules:[v1 RuleItem]}]}`,
`_partition_keyed_items`가 node_key 계약 검증.

- [x] T5-1 **완료(2026-08-03)** 모델: `BatchRulesV2Item`/`BatchRulesV2Output` —
      node_key echo 계약 유지, 규칙 leaf만 v2, 상한 제거. 전체 회귀 OK.
- [x] T5-2 **완료(2026-08-03)** DRY 추출: 공유 leaf 계약을 `common/rule_leaf_contract.md`
      로 단일화(framework·dbms·gate 3개 yaml이 공유 — 동일성은 테스트가 단언), dbms
      yaml 2종 v2 교체, `rule_evidence_contract.md` **삭제**(소비자 0 확인, 부재 게이트
      테스트로 재유입 차단), 해시 갱신(32 OK)
- [x] T5-3 **완료**: `_partition_keyed_items`가 v2 leaf 사전검사(`_v2_rules_leaf_ok` —
      anchor 중복·파이썬 파싱·닫힌 어휘, 실패 키는 기존 키 재시도 1회 계약에 태움),
      `_apply_node_analysis`가 노드별 inventory 로 `grade_rules` 채점 — 위반 행 드롭+
      누락과 함께 지표 표면화. **결정: DBMS는 행 단위 재요청을 결합하지 않는다**(키 단위
      1회 재시도 유지 — DBMS 노드는 소형 range 라 복잡도 대비 이득 없음)
- [x] T5-4 **완료**: dbms fn.extra["rules"]를 `serialize_rules_v2` 통일(when=signature,
      then 결정론 귀속), `rule_grading` 지표(obligation·rejected·missing) 배선.
      constrain_tables_to_rw 의 rules 인자 제거. 전체 회귀 그린
- [x] T5-5 **완료(2026-08-03)** v1 잔재 전체 삭제 + grep 0 게이트 통과: v1 모델 5종·
      require_* 검증기·rule_quality.py·grounding.py(+전용 테스트, 분기표·lexing 계약은
      분리 보존)·serialize_rules_with_table_effects·_final_table_effect·빌더 v1 분기·
      rule_hash·Rule.statement/Example.when 레거시 필드·rule_evidence_contract.md
      (부재 게이트 테스트로 재유입 차단)·낡은 주석 4곳 정정.
      **사용자 지적 반영 2건(발견 즉시 편입)**: ① "V2" 접미는 마이그레이션 상대 이름 —
      v1 삭제와 함께 본질 이름 계승(RulesExamplesOutput/RuleItem/ExampleItem/
      BatchRulesItem/serialize_rules/rule_table_effects 등, 17파일 일괄) ② EXAMPLE
      `when_`/`then_` 밑줄 폐기(예약어 회피 근거는 실측 부정 — todo 메모리 보류 트랙
      해소): 속성 then 통일·when 은 필드 소멸, 저장소 내 소비자 4곳(writer·
      evidence_reader·export 스크립트·트래버설 테스트) 한 계약으로 갱신.
      graph_delta_stream 표시 속성도 leaf 계약으로 갱신
- [x] T5-6 **완료**: SQL 시료 채점 테스트(표 lexicon 만 교체해 동일 채점 — 최상위 RETURN
      의무·창작 검출) — 시료가 실결함 1건 검출·수리(값 풀이 소수 0.8 을 0/8 로 쪼개
      창작 오판). batch 키 계약 테스트 v2 갱신(중복 anchor·leaf 문법 위반 키 재시도).
      전체 회귀 그린 + 해시 32 OK

## 6단계 — Neo4j 새 스키마 (통째 교체)

- [x] T6-1 **완료**(전환 슬라이스에서): 빌더·RULE/EXAMPLE 노드·Cypher v2, AFFECTS 규칙
      단위 이관, then/kind 속성 통일(밑줄 폐기), evidence_reader·export·delta stream
      소비자 정합. rel_types 변경 없음(관계명 동일)
- [x] T6-2 **완료(2026-08-03)** 로컬 Neo4j 왕복: 별도 database `spec116test`(봉인된
      `neo4j` 체크포인트 비접촉) — 실제 writer(write_example_mapping)로 쓰고 실제
      reader(evidence_reader.read_rules)로 읽어 leaf·tables·examples 필드 전 항목
      일치 확인, 종료 후 생성 노드 정확 삭제(잔존 0). **왕복이 실결함 1건 검출·수리**:
      AFFECTS_TABLE upsert Cypher 가 옛 EXAMPLE 소스 라벨을 MATCH → 조용한 엣지 0
      (v2 는 RULE 소스) — Cypher 교정 + 계약 테스트 갱신. 전체 회귀 그린

## 7단계 — 독립트랙 (0단계 이후 언제든 병행 가능)

- [x] T7-1 **완료(2026-08-03)** stereotype: `ontology_roles.yaml` `stereotype_rules`
      (priority 동점 해소 순서 + write_forbidden 배제) — `exclude_contradicted_stereotype`
      가 쓰기 근거 모순(Query/DataQuery/MetadataLookup)을 빈 값 강제+표면화,
      우선순위는 `{stereotype_priority}` 로 summary 프롬프트 2종(framework·dbms)에
      데이터 주입(정의처는 ontology 하나). 적용부 2곳(fanout·batch) 배선. 해시 갱신
- [x] T7-2 **완료** glossary 전역 차단: `collect_global_identifier_names`(전 코드노드·
      테이블·컬럼·런타임 함수 이름, glob 제외) → step_entry 1회 계산·ctx 주입 →
      `global_forbidden_for` 가 **사람용 주석·메시지 문자열에 실재하는 표기는 생존**시킨
      뒤 로컬 구조 금지와 합류(fanout·batch 동일 계약)
- [x] T7-3 검증: 표 주도 8종 — write_forbidden 3종 강제·무쓰기 생존·Command 생존·
      우선순위 렌더 / 전역 집합 구성(mpfmdbio·테이블·컬럼·RC_NRM 포함, PRINT_* 제외)·
      타 함수 이름 표제어 차단·주석/메시지 생존 2종. 실측 오답 2건(d100·
      get_card_ser_num)의 **교정 확인은 8단계 delta 몫**(모델 선택이 필요한 부분 —
      결정론은 모순 강제+우선순위 데이터 주입까지). 전체 회귀 그린

## 추가 편입 — leaf 지침 강화 · 정답지 앵커 배선 (2026-08-03 저녁, 사용자 지시)

- [x] TP-1 **완료** leaf 계약 강화(`rule_leaf_contract.md` — 교차 언어 실측 36/36 반영,
      spec §7 결정): 이름/문법 분리·원문 연산자 목록·호출형 비교 번역·호출 의역
      금지(NULL 창작 차단)·값 자리 문장 금지·변수 치환 금지·if식 금지. 미채택 2건
      (값-only then·삼항 행 분해 / "( 잔존 자기검사")과 사유는 spec §7.
      해시 갱신 + 일반성 계약 테스트 단언 갱신
- [x] TP-2 **완료** 채점기 IfExp 결정론 거절(AST 타입 검사 — JS 삼항식 직역 회귀 차단)
      + 반증 2종
- [x] TP-3 **완료** 선언 상수값 앵커 배선 수리: `build_control_inventory` →
      `declared_values`(immutable_constant) — 생산 미배선 갭(probe 실측 '3'/'2' 창작
      오탐)을 수리, 배선 반증 2종(`test_control_prompt_declared_values.py`)
- [x] TP-4 **완료** §18-8-a: [entry] then 최상위 효과 완전성 ↔ 값추적기 대조
      (`_compare_entry_then_with_value_trace`, findings 축·미상 병기) — 직선 함수
      "기준점 없음" 사용자 지적 해소. 반증 4종
- [x] TP-5 **완료** probe 재검증(2콜 — 직전 상한 소진 행들): z10020 512·518·524
      **3/3 첫 시도 통과**(직전 0/3), b100 263 통과·220 은 NULL 의역 잔존(재요청 축
      담당 — 두더지잡기 중단은 사용자 경고대로). 전체 회귀 **645/645 OK** + 해시 32.
      증거: `evidence-…/v19ab-값사실주입/probe_*.json`

## 8단계 — 전체 재분석 (사용자 새 승인 후에만)

- [x] T8-1a **1차 run 완료(2026-08-03, `20260803_092019_cc06b6e76c6a`, DB `test`)**:
      416노드·rules 3,521·examples 3,319·questions 359, 실패 0·[ERROR] 0.
      **비용 이상 발견**(사용자 지적): 11~13분 → 52분. 원인 전수 실측은 spec §20 —
      디코드 바운드(벽시계 = 총출력÷집계처리량 검산 일치), rules 가 LLM 시간 91.6%,
      v1↔v2 동일 19함수 콜 4.7배·출력 2.45배(계약의 구조적 비용), 재요청 낭비 27.8%
- [x] T8-1b **낭비 3종 수리(2026-08-03)**: ① 창작 오탐(구분자 한 글자·이스케이프 표기차)
      → 풀 합류 ② 거절 행에 "누락" 중복 통보 제거 ③ then 배열 원소 단위 계약 명시.
      반증 5종 신설, 전체 회귀 **650/650 OK**, 해시 32 OK
- [x] T8-1c **완료(2026-08-03)** 비용 회복 — spec §21: ① EXAMPLE 결정론 합성
      (`example_synthesis.py`, LLM 스키마·프롬프트·채점기에서 예시 은퇴, 드모르간
      실결함 1건 검수로 검출·수리) ② 창작 검사 오탐 수리(소스 실재 호출식이 거절돼
      재요청으로도 회복 불가였음 — z10020 행 18개 소실). 전/후 실측: z10020 콜 7→5·
      완성 15,989→7,922·수확 81→83/83·재요청 2→0, b100 콜 3→1·완성 1,538→931·수확 5→7/7.
      전체 회귀 **664/664 OK**, 해시 32 OK
- [ ] T8-1d 전체 재분석 재실행 — 벽시계 11~14분대 복귀 확인(사용자 목표)
- [ ] T8-2 고정20 재측정 · delta 보고 (새 run manifest + 직접 판정)

## 추가 편입 — [entry] 무조건 계약 행 (2026-08-03, 사용자 질문으로 재개정)

- [x] TE-1 "직선 함수 = 규칙 0" 폐기: 분기 밖 최상위 직접 효과가 있으면 첫 활성 줄을
      **[entry] 의무 행 1개**로 결정론 합성(`BranchInventory._entry_line`) — cond=null
      규칙으로 무조건 계약 복원, 뺄셈 열거성 유지. plan_batches·grade_rules 의무 편입,
      프롬프트 3종 갱신(gate 는 명시 제외 + 채점 assigned 제외), 해시 갱신.
      테스트: entry 합성 3종(직선/전분기/분기 앞 최상위) + fanout 2건 갱신 +
      좌표 계약 갱신 — 전체 회귀 그린

## 추가 편입 — 값 사실 프롬프트 주입 (ⓐ, 2026-08-03 사용자 지시 — 계약은 spec §19)

- [x] TV-1 **완료(2026-08-03)** value_tracking: `signature_parameters`(최상위 쉼표 분해·
      마지막 식별자, 결정론 — void/함수포인터 근사 한계 문서화) +
      `ValueTrace.object_writes(params)`(파라미터 base + 필드/인덱스 또는 via, 단순
      재대입 제외, 대상별 값 집합 병합) — 표 주도 7시그니처 + 선별 2종, 14/14
- [x] TV-2 **완료** api_contracts 배선: `ApiContract` 모델(`agents/contracts.py`) →
      `_apply_skill_contract` 전파(pydantic 재검증) → step_entry
      `ctx.extra["api_contracts"]` — 전파 반증 2종(proframe 실 fence 도달·미매칭 무생성)
- [x] TV-3 **완료** fanout: 자기 object_writes ctx 동승(자기 프롬프트 비주입 — §19),
      `_callee_interface_items` 로 렌더·풀 원천 단일화, `object_writes`/`writes_unknown`
      렌더, rules yaml `{api_contracts}` evidence 절 + 해시 갱신(32 OK)
- [x] TV-4 **완료** 채점 풀 결합: `_injected_value_facts` →
      `collect_rules_by_rows(extra_values)` → `grade_rules(extra_values)` —
      F() 검사는 소스 전용 불변(반증으로 고정)
- [x] TV-5 **완료** 반증: 주입값 창작 오탐 해소 전/후 대조·F() 불변·extra_values 공집합
      무변화·로컬 쓰기 미유출·파라미터 재대입 제외·미주입 시 절 부재·렌더=풀 동일성 —
      전체 회귀 636/636 OK (skipped=5)
- [x] TV-6 **완료(2026-08-03)** 전/후 소량 실측(18콜, 제품 코드 그대로 — 프롬프트
      조립·분기표·배칭·채점·문법강제): 총 수확 동률 88/90(A)=88/90(B), 주입 사실이
      실패 사유에 등장 0, z10020 프롬프트 +6.1%, b100 은 B 가 3콜→1콜. 부수 발견
      즉시 수리: 인용부호 값(`"00"`)의 벗긴 표기 풀 합류. **주입 유지 판정**
      (되돌릴 조건 미충족, delta 확정은 8단계). 증거:
      `evidence-…/v19ab-값사실주입/` + spec §19 실측 표. 전체 회귀 637/637 + 해시 32 OK

- [ ] diff 재독 / spec §12 Constitution 표 갱신 / 원칙 위배 점검 / 범위 밖 발견 편입 /
      dirty 132 비접촉 확인

## 긴급 재감사 — 2026-08-04 현재 실행이 권위

이 절은 위의 과거 완료 표기를 그대로 신뢰하지 않고 현재 코드·로그로 재판정한다. 사용자가
확정한 상위 목표는 analyzer 전체의 정확도·성능·속도·전 언어 확장성·유지보수성이다.
결정론 코드의 언어·샘플·이름 특화와 값/객체 상태 추적은 허용하지 않는다.

- [x] TA-1 **구현·전체 회귀 완료(2026-08-04)** `rule_grading._string_constant_ok` 의 임의
      정규화 부분문자열 허용을 제거하고, leaf 문법별 허용 근거를 구조적으로 검증했다.
      실 corpus 재분석 정확도는 TA-12에서 별도 검증한다.
- [ ] TA-2 프롬프트 순서 변경을 KV 개선으로 간주하지 말고 실측으로 확정 또는 되돌린다.
      중단 run: 1,033콜·입력 7,767,306으로 4M 기준을 반증했다.
- [ ] TA-3 값·객체 추적(`value_tracking`, `object_writes`, entry 값 대조, 주입 채점 풀)을
      생산자부터 소비자·테스트·문서까지 제거한다. callee 반환은 상태 추적이 아닌 소스의
      반환 표현식 집합으로 별도 소유한다.
- [ ] TA-4 결정론 EXAMPLE에서 `not_satisfy`를 제거하고 RULE에서 파생되는 `satisfy`만
      전 노드 Python 의미식으로 검증한다.
- [ ] TA-5 내용 오류/누락 일반 재시도를 제거한다. 연결 끊김·서버 오류만 transport 층에서
      재시도하고 그 밖의 실패는 원인과 좌표를 완전히 기록해 표면화한다.
- [ ] TA-6 `api_contracts` 입력의 정확도 이득과 토큰 비용을 대조해 유지/제거를 판정한다.
- [x] TA-7 **구현·오프라인 전수 대조·전체 회귀 완료(2026-08-04)** `referenced_symbols`
      공백 138/481의 원인을 전수 분류하고, 심볼 종류를 AST/ontology
      근거의 전역·필드·지역·매개변수·상수로 구조화한다. 이름 접두 판별은 금지한다.
      재구성 실측에서 481은 rules 호출 수, 138은 재시도 포함 빈 호출이며 고유 함수는
      416개/빈 135개였다. 현재 AST REFERENCES와 전수 대조해 빈 135개 전부 정상,
      누락·과잉 심볼 ID 0을 확인했다. 실제 결함은 참조 심볼 411개의 AST label
      (`GLOBAL_VARIABLE` 147, `MEMBER` 104, `DEFINE` 159, `CONSTANT_FIELD` 1)을
      `variable/constant`로 손실 압축하는 것과 DBMS reader의 선언 종류
      (`PARAMETER/VARIABLE/PACKAGE_VARIABLE`) 코드 하드코딩이다. parser 역할과 prompt scope를
      ontology 데이터 한 정의처로 옮기고 C/Java/Python/SQL 정상·미등록 실패를 반증했다.
      변경 후 현재 corpus 재구성은 global 147/field 104/constant 160이며 공백 135개는
      그대로 정답이다. 전체 653 tests와 hash 31 통과. 새 콜드 prompt 실측은 TA-12 몫이다.
- [x] TA-8 **완료(2026-08-04)** 분기표 감사 파일을 활성 run context가 소유할 때만 기록한다.
      전체 652 tests 전후 `logs/branch-tables` 416→416으로 오염 0을 반증했다.
- [x] TA-9 **구현·전체 회귀 완료(2026-08-04)** 할당 행을 키로 한 동적 고정키 출력
      스키마를 framework/DBMS 양쪽에 연결했다. 모든 행 키와 DBMS node 키는 required,
      `anchor_line`은 행별 const, extra는 forbid이며 응답 캐시 키는 모델명이 아니라 canonical
      JSON schema 전체를 포함한다. 누락·과잉·가짜 anchor·DBMS node 누락 반증이 모두
      schema 단계에서 실패하고 canonical 기존 모델로의 정규화도 검증했다. 24행 schema는
      25,414 bytes로 측정되어 실제 토큰·지연 영향은 TA-11/TA-12 콜드 run에서 별도 판정한다.
- [ ] TA-10 ontology·그래프 스키마·생산자·writer·query·프롬프트를 전 언어 확장성과 성능
      관점에서 감사하고, 구조 결함은 영향도 전체를 함께 수정한다.
- [ ] TA-11 콜별 지연·토큰·in-flight·Neo4j 쿼리 시간을 분리 계측해 1~2분 요청의 원인을
      특정한다. 세마포어 64 조건을 유지하되 서버 포화와 클라이언트 직렬화를 구분한다.
- [ ] TA-12 전체 회귀·해시 후 `test` DB 콜드 run, 전 노드 CALL/R/W/RULE/EXAMPLE 정답지
      대조, LLM 전 속성 기계 검증, 코드 20개+모든 테이블 의미 직접 판정을 반복한다.
- [ ] TA-13 저장소 전역 원칙 감사: 이번 변경 파일에 한정하지 않고 작업 중 발견한 해피패스
      특화·이름/코드값 하드코딩·언어별 결정론 분기·조용한 실패·빈값 폴백·의미 하이재킹·
      dead code·옛 이름·중복·DRY 위반·생산자/소비자 단절을 즉시 편입해 근거 확인 후
      같은 goal에서 수정한다. 전체 종료 전 관련 패턴과 옛 심볼 grep 0을 증명한다.
- [ ] TA-14 전역 감사 발견분: module_pre_analysis·module_summary·component_naming·
      glossary_synthesis의 `return_exceptions=True`/원본 유지/결과 drop을 제거하고 의미 산출 실패를
      fail-closed로 통일한다. 멤버 summary 일부 누락 상태의 모듈 합성을 금지한다.
- [ ] TA-15 결정론 관계 fail-open 제거: external CALL review 실패 시 전량 유지, scan_result 검증
      실패 시 R/W linker skip, 미지원 언어를 빈 관계로 바꾸는 경로를 생산자→소비자 전체에서
      실패 표면화한다. CALL/R/W 정답지 전수 대조로 회귀를 고정한다.
- [ ] TA-16 `large_code_one_liner` 실패의 원문 fallback이 소비자에서 원문 전체를 보존하는지,
      context 절단·토큰 상한·정확도 영향이 없는지 양방향 추적한다. 안전한 선택적 가속기일 때만
      명시 계약으로 유지하고, 부분/절단 의미를 대신 쓰는 경로면 제거한다. 추적 결과 실제 소비자는
      `ancestor_context` 한 곳뿐이며, 성공 시 정확한 조상 skeleton을 자유형 LLM 문장 120자로 절단해
      대체하고 현재 run 로그에는 호출 실적도 없다. 입증되지 않은 손실 압축 생산자·필드·프롬프트와
      그 전용 `return_exceptions` 실패 정책을 제거하고 원문 구조 골격 보존을 회귀 계약으로 고정한다.
- [ ] TA-17 내용 오류 fail-closed 전환 뒤 도달 불가능해진 DBMS `failed_parts`·`partial` 집계와
      Neo4j/NDJSON `analysis_partial` 외부 계약을 제거한다. 분석 대상 summary 누락은 경고 후 계속
      진행하지 않고 불변식 위반으로 실패시키며, 옛 생산자·소비자 참조 0을 검증한다.
- [ ] TA-18 공통 Agent/링킹 실패 계약을 근본 정리한다. `run_structured_agent` submit 누락 재요청,
      ExternalSkill 7후보×수리×held-out 재제안, invalid `scan_result`의 CALL/R/W/잔여별 상이한
      fallback, external CALL review 전량 유지, R/W source/regex 오류 skip을 제거한다. 한 번 제안과
      결정론 검증을 유지하고, CodeNode 소유 파일만 스캔하며 모든 소비자가 단일 `ScanResult`
      검증 객체를 사용하도록 한 뒤 실패·취소·정상 경로를 회귀한다.
- [x] TA-19 **구현·전체 회귀 완료(2026-08-04)** leaf 효과 인자 계약을 단일 정의처에서
      구조적으로 강제했다. 기존 `parse_effect`는
      최상위 함수명만 검사해 `call("fn", ...)`, 중첩 임의 호출, 잘못된 `set` 대상/인자 수를
      통과시킬 수 있고, 2026-08-03 실 run 응답 444건 전수 분류에서 이 계약 밖 형태가 실제로
      관측됐다. `call/ret/set/raise_error/select/insert/update/delete`별 arity·AST shape를 고정하고,
      함수명은 inventory의 정확한 callee, 값은 정확한 리터럴/토큰 또는 `F("원문 식")`, 테이블은
      결정론 R/W 집합에서 검증한다. 부분 식별자·원문 문장 통째 인용·중첩 효과를 반증 테스트로
      차단하고 프롬프트·parser·grader·직렬화 소비자를 같은 계약으로 맞췄다. 실 corpus 재분석은
      TA-12에서 별도 검증한다.
- [x] TA-20 **완료(2026-08-04)** 전역 원칙 감사 발견분: `pipeline.languages`가 parser 지원
      확장자와 소스 lexing
      프로필을 코드 상수 하나로 혼합해, fail-closed R/W 링커가 실제 CodeNode를 가진 Pro*C
      (`.pc`)·COBOL(`.cbl`) 임베디드 SQL에서 `NoSupportedSourceError`로 중단한다. 전체 회귀
      650건 중 4건 실패로 재현했다. parser가 실제 지원하는 확장자와 parser 없이도 링커가
      소비할 수 있는 lexing-only 확장자를 `ontology_roles.yaml` 데이터에서 분리하고,
      확장자 중복·어휘표 누락은 로딩 시 실패시킨다. `.pc`를 근거 없이 C parser 지원으로
      승격하지 않으며, COBOL 고정형/인라인 주석 속 SQL이 관계로 생성되지 않는 반증과
      미등록 확장자의 명시 실패를 함께 고정했다. 표적 66 tests와 전체 652 tests가 통과했다.
- [x] TA-21 **구현·전체 회귀 완료(2026-08-04)** TA-9 영향도 감사 발견분: DBMS
      `_apply_node_analysis`가 `BatchRulesItem`은 있으나
      `rules=[]`이면 `grade_rules`를 건너뛰어 의무 분기 행 누락을 정상 저장할 수 있다.
      `build_control_inventory`는 항상 객체를 반환하는데 남은 `None` 분기도 죽은 fallback이다.
      framework/DBMS 동적 고정키 schema가 모든 할당 행을 required+anchor const로 강제하고,
      canonical grader도 빈 결과를 포함해 항상 실행하도록 만들어 schema 우회 테스트에서도
      누락이 명시 실패하는 것을 반증했다. 전체 659 tests OK(skipped=5), prompt hash 31,
      branch-table 416→416. 실제 LLM strict-schema 호환과 실 corpus 결과는 아직 미검증이다.
- [x] TA-22 **완료(2026-08-04)** 전역 원칙 감사 발견분: 내용 재요청 제거 뒤에도 DBMS
      현재 코드의 주석·docstring과
      `_partition_keyed_items(..., attempt=1)`/로그 필드에 과거 미니 재요청 계약이 남아 있다.
      현재 한 번 호출·한 번 판정 계약에 맞게 죽은 회차 API와 상대적 재요청 용어를 제거하고,
      생산자·호출자·테스트의 옛 참조 0 및 실패 로그의 완전한 좌표 보존을 검증했다.
      표적 10개와 전체 659 tests OK(skipped=5), hash 31, branch-table 416→416.
- [x] TA-23 **구현·전체 회귀 완료, 콜드 재검증 대기(2026-08-04)** 전역 원칙 감사
      발견분: module-boundary REFERENCES가
      `import_scopes.py`에서 Java/Python/C import 문법과 소유권 해석을 코드 분기로 직접
      소유하고, 그 밖의 언어는 `pass2_unsupported` 로그 뒤 같은 파일 연결만 남기는
      정확도 저하를 정상 진행한다. C switch parser 소유권 결함도 analyzer가
      `lang == "c"`로 AST를 변형해 숨긴다. parser→AST/import 계약→ontology→REFERENCES/
      INIT_BY 전 소비자를 추적해 언어 지식을 데이터/정규화 계약으로 옮기고, 지원 선언된
      언어의 의미 단계를 조용히 생략하지 않게 한다. 현재 콜드 run 결과와 전수 정답지를
      건드리기 전 영향 기준선으로 보존한다. 추가 추적에서 `token_matcher.py`의 주석·문자열
      제거, 식별자 문법, 상속 선언도 C/Java/Python 정규식과 언어명 딕셔너리를 직접 소유하고,
      줄 단위 제거라 여러 줄 블록 주석 상태를 잃는 결함을 확인했다. source lexicon/import
      resolver profile을 ontology 단일 진실로 만들고 기존 `split_source`를 재사용한다.
      CASE 범위·자식 소유권은 analyzer 보정이 아니라 `antlr-code-parser` 생산자에서 고쳐
      생산 AST 자체와 parser 회귀를 먼저 통과시킨 뒤 analyzer 보정기 참조 0을 검증한다.
      import/identifier/inheritance/주석 문법은 ontology profile로 이동했고 범용 resolver와
      `split_source`가 소비한다. 미등록 resolver는 fail-closed다. parser와 analyzer 전체
      회귀가 통과했다(analyzer 670 tests, skipped=5; hash 31; branch-table 312→312).
      새 parser AST는 옛 AST와 파일 23/노드 17,046/함수 416/호출 12,112/SWITCH 147/CASE
      756을 보존하고 malformed ownership만 303/254→0/0으로 고쳤으며 analyzer가 23개를
      보정 없이 읽었다. 실 콜드 결과는 다음 run에서 검증한다.
- [x] TA-24 **완료(2026-08-04)** 전역 원칙 감사 발견분: 공개 운영자 CLI
      `scripts/resynthesize_glossary.py`가
      제거된 `gather_with_warmup(..., return_exceptions=True)` 계약을 호출하고 실패 결과를
      건너뛰며 부분 사전을 저장한다. 현행 glossary는 source `code_text/comment`만 근거로
      검증하는데 이 CLI의 Neo4j query는 생성된 summary/search_text만 읽어 원문 근거도
      재구성하지 못한다. 소비자는 README 두 곳뿐임을 확인했다. 복구할 수 없는 옛 우회
      기능을 CLI·문서·현재 테스트에서 제거했다. 현재 실행/문서 참조 0, owner-scope 표적
      3개와 전체 660 tests OK(skipped=5)을 확인했다. 역사 spec의 과거 경로 기록은 보존한다.
- [x] TA-25 **구현·전체 회귀 완료, 콜드 재검증 대기(2026-08-04)** 첫 동적-schema 콜드
      run 실측 결함: 106콜/3분 6초 시점에 오류 출력 규칙이
      `raise_error("메시지 요약")`을 내어 정확 리터럴 채점에 거절됐다. 프롬프트는
      `raise_error("메시지ID")`와 “메시지 문장 원문은 옮기지 않음”을 동시에 요구하지만
      실제 소스에는 ID 없이 포맷 문장만 있는 일반 경로가 있어 통과 표현이 모순된다.
      텍스트 인자를 의미 근거처럼 만들지 말고 최소 언어중립 효과 `raise_error()`로
      모델·프롬프트·parser/grader·EXAMPLE·serializer/graph/reader·테스트 계약을 함께
      바꾼다. 첫 run은 strict dynamic schema 호환·required 좌표 강제가 실제 서버에서
      작동했음을 확인했지만 전체 성능/정확도 판정 자료는 아니다. `raise_error()`만 허용하고
      텍스트 인자를 거절하는 반증을 추가했으며 표적 57개와 전체 660 tests, hash 31,
      branch-table 69→69가 통과했다. 다음 콜드 run에서 실 모델 출력을 재검증한다.
- [x] TA-26 **구현·전체 회귀 완료, 콜드 재검증 대기(2026-08-04)** 성능/원칙 감사
      발견분: run 전역 `PrioritySemaphore(64)`는 실제 LLM 요청
      총량의 단일 진실이라고 선언됐지만 framework는 노드 전체, DBMS는 배치 전체에서
      슬롯 1개를 잡고 그 내부 rules/insights 등 복수 LLM 호출을 동시에 생성한다. 설정 64와
      실제 in-flight가 어긋나 서버 queue/장기 tail 가능성이 있다. 현재 콜드 run의 호출
      시작/종료·latency 분포로 원인 기여도를 먼저 측정하고, framework/DBMS/후처리 모두
      같은 run 슬롯을 **실제 run_yaml_rule 호출 경계**에서 정확히 하나씩 점유하도록
      생산자·호출자·pause/cancel·우선순위 테스트를 함께 수정했다. 수정 전 두 번째 partial
      run에서 split 동시성이 실제 최대 80까지 올라 설정 64를 위반했고, 성공 summary 59콜은
      p50 28.8초/p95 45.8초/max 50.2초였다. framework/DBMS runner mock 안에서 1-slot의
      `free == 0`을 직접 단언했고, 대기 취소 전 coroutine을 만들지 않게 호출 factory로
      바꿨다. 전체 660 tests OK(skipped=5), hash 31, branch-table 67→67. 변경 후 실제
      in-flight/지연은 다음 콜드 run에서 판정한다.
- [x] TA-27 **구현·전체 회귀 완료, 콜드 재검증 대기(2026-08-04)** 콜드 run 로그의
      전역 원칙 감사 발견분: per-node summary의 `domain_terms`가
      source evidence 밖이거나 구조 식별자여도 `normalize_domain_terms`가 항목만 drop하고
      남은 summary/search_text를 정상 저장한다. 경고가 있어도 내용 오류를 부분 성공으로
      바꾸는 의미 손실이다. validator가 source/구조 근거를 전수 판정하고 한 항목이라도
      invalid/duplicate면 code_id·항목별 사유와 함께 호출 전체를 실패시키도록 framework/
      DBMS 양쪽 fan-in을 맞춘다. 선택 필드이므로 모델은 애매하면 빈 목록을 낼 수 있지만,
      낸 의미를 코드가 몰래 정화해 다른 의미로 바꾸지는 않는다. 검증 실패 전에
      summary/stereotype/logical_name/search_text를 노드나 context에 먼저 쓰지 않고,
      **모든 필드가 검증된 뒤 원자적으로 적용**되는 반증까지 포함한다. framework에서
      context가 먼저 변하던 실제 결함을 새 반증이 잡아 수정했다. 전체 663 tests
      OK(skipped=5), prompt hash 31, branch-table 312→312. 실 모델은 다음 콜드 run에서
      재검증한다.
- [x] TA-28 **완료(2026-08-04)** TA-23 영향 추적 중 발견한 reader 분리 잔재:
      `framework_reader.py`가 더 이상
      소유하지 않는 DBMS 변수 label 상수와 json/settings/DBMS extension/그래프 모델/로거
      import를 보유하고, 옛 단일-reader 설명도 실제 모듈 경계와 어긋난다. 현재 소비 0을
      AST로 확인한 뒤 제거하고 framework reader의 생산물·책임 설명을 현행으로 맞췄다.
      AST 기반 미사용 import 검사에서 `__future__.annotations` 외 잔재 0, 전체 analyzer
      670 tests OK(skipped=5).
- [x] TA-29 **완료(2026-08-04)** 새 parser 복제본 콜드 run에서 재현: CLI `--data-dir`가 source/ddl과 manifest의
      `Codebase.corpus_root`는 바꾸지만 `ast_and_source_roots`는 전역 SETTINGS의
      `analysis/`를 고정 사용해 **새 source + 옛 AST**를 섞는다. 실제 로그에서 root는
      `_runs/.../source`, ast_dir은 기존 코퍼스 `analysis`로 갈라졌고 canonical 검사에
      즉시 실패했다(LLM 0콜). `corpus_root`를 AST/source base의 run-scoped 단일 진실로
      전달하며, 명시 corpus 밖 source면 폴백하지 않고 실패하는 교차 코퍼스 반증을 추가했다.
      표적 10 tests와 전체 analyzer 672 tests OK(skipped=5), prompt hash 31,
      branch-table 0→0 통과. 신선 AST 기반 콜드 전체 run은 재실행 전이라 미검증이다.
- [x] TA-30 **완료(2026-08-04)** TA-29 후 신선 AST 콜드 run에서 구조화 submit 배선 모순 재현: 로그에는
      `submit_scan 완료`가 찍혔지만 `run_structured_agent`는 submit 호출 누락으로 실패했다.
      6콜/입력 120,756/출력 1,957/cache hit 0, 38.5초의 실패 run이며 성능 결과가 아니다.
      실제 원인은 호출 누락이 아니라 필수 `table_access_patterns` 누락 Pydantic 오류를
      ToolNode가 error ToolMessage로 바꾼 것이었다. 공용 경계가 invalid submit/미호출/유효
      payload의 state 손실을 별도 예외로 판정하고, tool error를 완료로 기록하지 않게 했다.
      default-factory의 실제 생략 계약도 동적 schema에 보존했다. 전체 679 tests 통과.
      실 모델 submit 성공은 다음 콜드 run에서 재검증한다.
- [x] TA-31 **완료(2026-08-04)** 같은 fatal `run_failed`가 NDJSON과 오류 로그에 명시됐는데 공개 CLI 프로세스가
      exit code 0을 반환했다. 자동화가 실패 분석을 성공으로 오인하는 조용한 실패다.
      `execute_run`은 예외를 정상 재전파했지만 CLI의 프로젝트 루프가 출력만 하고 삼킨 것이
      원인이었다. 배치는 나머지를 처리한 뒤 실패 목록을 `AnalysisBatchError`로 올리며,
      잘못된 target/빈 source도 실패한다. 정상/실패/입력 오류 4 tests와 전체 회귀 통과.
- [x] TA-32 **완료(2026-08-04)** Scan Agent가 매칭 SKILL의 결정론 YAML 계약 전체를 LLM 출력으로 다시 받아쓰고,
      `_finalize`는 그 복사본을 검증한 뒤 step 경계의 `_apply_skill_contract`가 원문 계약으로
      다시 덮는다. 토큰 낭비·약한 모델의 필드 누락·DRY 위반이며, 실제 최종 주입값과 검증값이
      달라질 수 있다. LLM은 skill 선택/근거와 미매칭 custom 규칙만 출력하고, 매칭 시 코드는
      원문 계약을 즉시 hydrate한 뒤 **그 최종값을** 단일 검증 관문에 통과시킨다. custom 경로는
      규칙/패턴이 없으면 `no_match_reason`을, 모든 경로는 실제 evidence location 3개를 강제한다.
      step 사후 덮어쓰기/경고/중복 parser를 제거하고 DSL parse도 compiler 단일 진실로 옮겼다.
      표적 57 tests, 전체 679 tests, prompt hash 31, branch-table 0→0 통과. 실 콜드 미검증.
- [ ] TA-33 **구현·전체 회귀 완료, 콜드 재검증 대기(2026-08-04)** TA-30~TA-32 후 신선 AST 콜드 run에서 residual 1콜이
      입력 23,824·출력 8,000(max)·56.125초를 쓰고 자유문만 반복한 채
      `submit_residual`을 호출하지 않아 전체 분석이 실패했다. residual은 Agent 도구로
      `verify_table_regex`를 실행하게 하지만 호출자도 제안 직후 같은 production
      `verify_table_patterns`를 결정론 실행한다. 동일 검증 책임의 중복이 약한 모델에게
      자유 탐색 루프를 열고 토큰·지연·실패를 만든다. residual 제안은 전용 YAML prompt의
      단일 strict-schema `run_yaml_rule` 호출로 바꾸고, 검증은 production 결정론 관문 한
      곳만 소유하게 한다. 빈 제안은 비어 있지 않은 `no_pattern_reason`을 schema 의미 계약으로
      강제한다. 모델이 패턴을 냈는데 결정론 검증에 실패한 경우 경고 후 패턴을 버리고 미해결로
      계속하는 현재 부분 성공도 금지하고, 좌표가 있는 명시 오류로 전체 분석을 실패시킨다.
      관련 Agent prompt/tool/max-call 소비자를 제거하고 옛 심볼 참조 0, 정상·빈 제안·schema
      오류·검증 거절·실제 prompt 증거/usage 적재를 표적 테스트한 뒤 전체 회귀와 콜드 run으로
      재검증한다. Agent prompt 103줄을 34줄 YAML rule로 교체하고 반복 정규식 본문은
      registry 한 번만 보내도록 구조화했다. 패턴/변환/명시적 무제안 schema, 변환-only 해소,
      검증 거절 fatal 반증과 전체 686 tests OK(skipped=5), prompt hash 31을 통과했다.
- [ ] TA-34 **구현·전체 회귀 완료, 콜드 재검증 대기(2026-08-04)** TA-33 영향 추적 중 공용 정규식 검증기의 fail-open을
      production 최소 재현했다. 실제 소스에서 정상 패턴 1개와 매치 0인 죽은 패턴 1개를 함께
      `verify_table_patterns`에 넣자 정상 패턴의 집계 실적이 죽은 패턴을 가려 `ok=True`가
      반환됐다(매치 1·edge 1·인식/해소 100%). 또한 Scan `_apply_verdict`는 패턴 집합 검증이
      실패해도 confidence와 사유만 바꾼 뒤 실패 패턴을 최종 `ScanResult`에 그대로 보낸다.
      검증기는 파일을 다시 읽는 N회 우회 없이 같은 production link pass에서 패턴별 지표를
      보존하고, 패턴 ID 중복도 거절하며, 모든 패턴이 각각 support/부분추출/token/모순/중의
      계약을 통과할 때만 전체를 승인한다. 제안 패턴이 하나라도 실패하면 Scan과 residual 모두
      내용 오류로 명시 실패하고 downstream 적용은 0이어야 한다. 명시적 무패턴+사유 경로와
      embedded rule 전용 경로는 정상 계약으로 보존한다. 한 파일 read/pass에 `rule_stats`를
      함께 누적하고 정규식·embedded rule ID 중복과 각 rule의 독립 실패를 거절한다. Scan은
      제안 실패를 raise한다. 최소 재현·정상·중복·embedded 회귀와 전체 686 tests가 통과했다.
- [ ] TA-35 **구현·전체 회귀 완료, 콜드 재검증 대기(2026-08-04)** residual 입력 토큰 절별 계측 중 Agent 증거 로그가 최초
      user 메시지를 버리는 결함을 실측했다. scan은 7턴 중 6턴(36,416자), call-review는 3턴
      중 2턴(478자)만 user 내용이 있고, residual은 유일한 첫 턴이 0자라 입력 23,824토큰의
      구조 입력을 사후 조사할 수 없다. `append_conversation_turn`이 system 메시지가 Agent
      state index 0에 있다고 가정해 첫 요청을 `start=1`부터 자르지만 실제 system prompt는
      별도이고 state index 0은 최초 HumanMessage다. 첫 turn은 index 0부터 기록하고, 이후는
      직전 AI response offset 뒤 새 Tool/Human 메시지만 기록해 중복 없이 전체 대화를 보존한다.
      최초 입력·후속 tool turn·빈 request 경계와 실제 Agent log를 반증한다. residual은 YAML
      runner 전환으로 별도 full prompt record도 확보한다. 최초 Human+후속 Tool offset 단위
      반증과 전체 686 tests가 통과했으며 실제 Agent 로그는 다음 콜드 run에서 확인한다.
- [ ] TA-36 **구현·전체 회귀 완료, 콜드 재검증 대기(2026-08-04)** 전역 감사에서
      `table_prompt_context.py`가 비-SQL 언어를 코드의 언어명 집합으로 분기하고, 소스 member
      owner가 DDL table 이름으로 시작하면 해당 table 소유 컬럼이라고 판정하며, 한 글자 중복을
      제거한 이름까지 같은 컬럼으로 인정하는 오늘 변경을 확인했다. 이는 언어·이름·특정 표기
      특화 결정론이고 실제 컬럼 소유권 근거가 아니다. framework/DBMS 양쪽 prompt와 LLM
      컬럼 allowlist가 이 값에 의존하므로 단순 함수 삭제가 아니라 생산자(R/W/DDL/AST)부터
      prompt·schema·validator·step8 통합까지 영향도를 추적한다. 컬럼→테이블 귀속은 parser/
      linker/DDL 관계처럼 증명 가능한 구조 근거만 허용하며, 증명할 수 없는 code-node 후보를
      이름 유사도로 만들지 않는다. 객체·런타임 값 추적이나 새 접두 규칙으로 대체하지 않는다.
      hard-coded 언어 집합·table 접두·중복 철자 보정 생산자를 제거하고 code-node prompt는
      구조적으로 증명할 수 없는 컬럼을 주지 않도록 고정했다. DDL 전체 컬럼은 step8 TABLE
      보강 책임에 남겼다. framework/DBMS 소비자와 표적 회귀 및 전체 764 tests가 통과했지만
      실제 콜드 prompt·저장 결과는 아직 미검증이다.
- [ ] TA-37 **구현·전체 회귀 완료, 콜드 재검증 대기(2026-08-04)** TA-34 적용 후 콜드 run이
      SKILL의 `proframe_mpfmdbio_seq` 규칙에서 중단됐다. 패턴별 실측은 13매치 중 테이블 해소
      1, DDL 모순 12(92%)였고 전체 run은 31.6초/7콜/입력 159,448/출력 1,534/cache hit 0에서
      exit 1이었다. 이 규칙은 `<table>_s_<op><nnnn>` 키를 실제 TABLE이 아닌 시퀀스 DB 객체라고
      문서화하면서도 본 TABLE READ로 단일화한다. 그래프에 없는 객체를 TABLE 관계로 둔갑시키는
      영역 축소는 정확도 계약이 아니다. 현재 parser/DDL/graph가 SEQUENCE를 모델링하지 않는다면
      해당 호출로 TABLE edge를 만들지 않고, 정상 본-table R/W 규칙은 그대로 보존한다. SKILL/
      compiler/linker/residual/테스트의 생산·소비 경로와 정상·시퀀스·DDL 동명이인 반증을 함께
      검증한다. 해당 시퀀스 규칙과 본 TABLE READ 단일화 설명을 SKILL에서 제거하고 정상
      `<table>_<op><nnnn>` 규칙은 보존했다. loader/compiler 반대 사례와 전체 764 tests가
      통과했지만 실 콜드에서 scan/linker가 끝까지 통과하는지는 아직 미검증이다. 이 실패 run은
      성능 성공 자료로 사용하지 않는다.
- [ ] TA-38 **구현·전체 회귀 완료, 콜드 재검증 대기(2026-08-04)** 전역 감사에서 step8
      `_preserve_source_column_meaning`가 source 설명의 괄호 앞 문자열을 영숫자 정규화한 뒤
      LLM description의 부분문자열인지 검사하고, 없으면 LLM의 logical_name/description을
      source 문장으로 사후 덮는 오늘 변경을 확인했다. 결정론 코드가 의미 동치를 판정할 수
      없는데도 유사 문자열로 LLM 의미를 하이재킹하고, 실패를 schema 오류로 표면화하지 않는
      조용한 사후 보정이다. 권위 source 의미는 입력/schema/결정론 파생 계약에서 처음부터
      보존하고, LLM이 맡은 의미는 그대로 검증·수용 또는 명시 실패시킨다. 부분문자열 grader와
      사후 덮어쓰기를 제거했다. source 의미가 있으면 동적 schema의 exact const로 보존하고,
      비어 있을 때만 LLM이 생성하게 해 응답 뒤 의미 덮어쓰기를 없앴다. source 있음/없음·변경
      거절 표적 회귀와 전체 764 tests가 통과했지만 Neo4j round-trip은 콜드 성공 후 검증한다.
- [ ] TA-39 **구현·전체 회귀 완료, 콜드 재검증 대기(2026-08-04)** TA-36 소비자 추적에서
      `TableInsightsOutput`이 자유 배열이고 `constrain_tables_to_rw`가 R/W 밖 table, 모순 role,
      DDL allowlist 밖 column을 경고 후 삭제해 나머지 의미를 정상 저장함을 확인했다. 입력의
      직접 R/W table 집합을 고정키 required schema로 만들고 table ID는 const, role은 결정론
      R/W/op가 허용하는 폐쇄 집합으로 강제한다. 증명 가능한 code-node 컬럼 관계가 없는 현재
      경로는 `columns`를 빈 고정 계약으로 강제하며, 허용 밖/누락/중복/역할 모순을 사후 drop하지
      않고 schema/content failure로 중단한다. framework/DBMS 양쪽을 공용 동적 fixed-key schema와
      canonical normalization으로 연결했다. 누락·과잉·table const·operation별 role·비어 있지 않은
      columns 및 실제 semaphore wiring 반증이 통과했고, 옛 drop/집계 생산·소비 참조는 0이다.
      전체 764 tests가 통과했지만 실제 서버 schema와 저장 결과는 아직 미검증이다.
- [x] TA-40 **완료(2026-08-04)** TA-37 표적 검증에서
      `tests/agents/test_rule_compiler.py`의 함수식 테스트가 unittest 명령에 수집되지 않음을
      확인했다. AST 전수 계측 결과 top-level test 함수 파일 18개 중 7개, 총 44개가 bridge/
      `load_tests` 없이 빠지며, 이 중 12개는 pytest monkeypatch/async fixture 의존이라 단순
      호출도 불가능하다. 프로젝트 권위 러너는 `unittest discover ... -t .` 하나이므로 모든
      44개를 실제 unittest case로 편입하고 fixture 의존을 `unittest.mock`/격리 setup으로
      바꿨다. 공용 `load_function_cases()`를 단일 정의처로 두고 10개 중복 bridge를 제거했다.
      AST 감사에서 누락 수집 0, legacy bridge 0, fixture 인자 0을 확인했고 권위 명령으로
      **764 tests OK(skipped=5)**를 통과했다.
- [ ] TA-41 **구현·전체 회귀 완료, 콜드 round-trip 대기(2026-08-04)** TA-38 소비자 재감사에서 step8이
      동적 schema를 통과한 테이블/컬럼 의미를 state와 Neo4j에 반영할 때 다시 fail-open으로
      바뀌는 경로를 확인했다. `build_consolidated_column_updates`는 결과 `None`, 누락·중복·허용
      밖 컬럼, 빈 의미를 `continue`로 버리고 부분 update를 만들며, `_warn_unmatched`는 Neo4j
      매칭 부족을 “무해한 잉여”로 INFO만 남긴다. `_replace_by_id`도 state에 없는 update ID와
      빈 값을 조용히 버린다. 이는 schema 강제·그래프/state 동일성 계약을 저장 직전에 무효화한다.
      table/column 결과 집합은 DDL/HAS_COLUMN allowlist와 정확히 같아야 하며 None·중복·누락·
      과잉을 명시 실패시킨다. 반대로 공용 prompt가 근거 부족 시 허용하는 빈 의미값은 key가
      존재하는 유효 응답으로 그대로 보존해야 하며, source 값이 있으면 동적 const가 빈값을
      거절한다. state node/update ID도 중복 없이 정확히 대응시킨다.
      Neo4j는 매칭 수가 입력 수와 다르면 어떤 노드도 부분 SET하지 않는 단일 원자 쿼리로
      바꿨다. 정상·None·누락·중복·unknown·빈 의미·state ID 불일치·DB 미매칭 반대 사례,
      step8/graph suite와 전체 **770 tests OK(skipped=5)**, compileall, prompt hash 31이
      통과했다. 실제 Neo4j managed transaction과 graph/state round-trip은 콜드 run에서 검증한다.
- [ ] TA-42 **구현·전체 회귀 완료, 실 콜드 대기(2026-08-04)** TA-36~TA-41 후 신선 AST 콜드는 57.3초,
      10콜, input 160,281, output 3,005, response-cache hit 0에서 residual 검증 실패로
      exit 1 종료됐다. residual 표본은 sequence가 아니라 정상 규칙으로 추출됐지만 DDL에
      없는 일반 미해결 entity였다. 모델이 자연어 `codebase_notes`의 비-TABLE 설명을 무시해
      sequence 형식 raw pattern을 제안했고, production 전 파일 검증이 12매치/edge 0/모순
      100%를 찾아 정상 중단했다. 비-TABLE 형식을 SKILL 소유의 언어중립 폐쇄 DSL로 표현해
      loader→ScanResult→scan/linker→residual prompt·검증·재적용에 무손실 전달한다. 제외
      규칙도 source support를 검증하고, TABLE/제외 규칙의 동일 물리 지점 충돌과 residual
      제안 overlap은 fail-closed한다. 정상 TABLE, DDL-missing 정상 잔여, 비-TABLE, 죽은 제외,
      충돌, DDL 동명이인까지 production 경로로 반증한다. generic `wrapper_key` exclusion
      DSL과 별도 non-edge pattern 타입을 만들고, 한 파일 read pass에서 exclusion support와
      TABLE match 물리 span overlap을 계측한다. 직접 linker는 충돌을 raise하고 verifier는
      규칙 ID·좌표를 포함한 실패 verdict를 반환한다. residual prompt에는 regex가 아닌
      구조화 rule JSON을 주며 baseline/검증/재적용 모두 같은 exclusion을 사용한다. 이 실패
      run은 성능 결과가 아니다.
- [ ] TA-43 **구현·전체 회귀 완료, 실 콜드 대기(2026-08-04)** `_hydrate_skill_contract`가 구조화한
      `handler_markers`와 `api_contracts`를 `_finalize`가 새 `ScanResult` 생성 중 누락해
      기본 빈 배열로 조용히 소실한다. handler branch inventory와 rules prompt/창작 근거가
      사라지는 정확도 결함이다. loader→hydrate→finalize→workflow dict→step7 context→
      inventory/prompt 소비를 한 fixture로 전 경로 대조하고, 모든 hydrate 필드를 무손실
      보존하도록 복사 책임과 회귀 계약을 고친다. `_finalize`의 수동 필드 나열 생성자를
      제거하고 전체 validated model dump에 finalize 소유 필드만 갱신해 새 필드도 자동
      보존한다. 실제 SKILL fixture의 handler/API/exclusion이 finalize 뒤 유지됨을 확인했다.
- [ ] TA-44 **구현·전체 회귀 완료, 실 콜드 대기(2026-08-04)** step4의
      `_remove_project_functions_from_framework_drop`은 runtime drop 목록이 프로젝트 정의
      함수와 겹치면 경고 후 항목을 삭제해 성공시키는 사후 의미 정화다. matched SKILL/custom
      모두 최종 scan 검증에서 exact/glob 충돌을 좌표와 이름으로 실패시키고, step은 검증된
      결과를 직렬화만 하도록 한다. 정상 runtime·exact 충돌·glob 충돌·빈 목록과 downstream
      CALL 분류를 반증한다. 이름별 예외나 경고 후 계속은 금지한다. step4 사후 삭제 함수를
      제거하고 finalize에서 실제 CALL 분류와 같은 `fnmatchcase` 의미로 exact/glob 충돌을
      실패시킨다.
- [ ] TA-45 **구현·전체 회귀 완료, 실 콜드 대기(2026-08-04)** TA-42 배선 재감사에서 matched SKILL
      hydrate가 fence에 있는 key만 덮고 없는 exclusion/runtime/handler/API 등은 LLM 초안
      값을 남기는 주입 경로를 확인했다. matched 경로의 모든 SKILL 소유 필드는 문서 값 또는
      명시적 빈 기본값으로 폐쇄하고 LLM 값을 보존하지 않는다. 비-TABLE exclusion은 source
      match만으로 의미가 입증되지 않으므로 matched SKILL 전용이며 custom 미매칭 출력은
      거절한다. key 부재·빈값·agent 주입·정상 fence를 반증한다. matched 경로는 모든 SKILL
      소유 필드를 문서 값 또는 빈/default로 무조건 덮고, unmatched ScanResult의 exclusion은
      모델 validator가 거절한다. TA-42~45 표적 27건, agents 67 + step4 53 + step5 107,
      전체 **776 tests OK(skipped=5)**, compileall, prompt hash 31, diff-check가 통과했다.
- [ ] TA-46 **구현·전체 회귀 완료, 실 콜드 대기(2026-08-04)** TA-42~TA-45 후 콜드는 약 18.1초,
      scan 2콜/input 26,014/output 60/cache hit 0에서 exit 1 종료됐다. 첫 응답은 `ls`, 두 번째는
      content `"[Collapse]"`/tool calls 0이라 submit이 없었다. transport 오류가 아니므로 내용
      재시도하지 않는다. registered SKILL은 이미 access/exclusion/DDL 구조 계약과 production
      verifier가 있으므로 각 계약을 실제 source/DDL에 일반형으로 검증해 정확히 한 후보와
      서로 다른 좌표 3개 이상이면 LLM 0회로 선택·hydrate·finalize한다. 0개만 unknown/custom
      Agent로 보내고 2개 이상은 후보·근거를 남겨 실패한다. verdict를 finalize에 재사용해
      선택된 source를 중복 스캔하지 않는다. 이름/언어별 결정론 분기 금지. known 0콜,
      unknown fallback, ambiguity, 깨진/dead skill, evidence와 전 필드 보존을 반증한다.
      verifier가 bounded distinct source 좌표를 반환하고, 등록 SKILL을 안정 순서로 모두
      검증해 유일 후보 verdict를 finalize에 재사용하도록 구현했다. known fixture LLM 0회,
      unknown 1회 위임, 다중 후보 fatal 반증과 agents 70/step4 53/step5 107, 전체
      **779 tests OK(skipped=5)**, compileall, prompt hash 31, diff-check가 통과했다.
- [ ] TA-47 **구현·전체 회귀 완료, 실 콜드 대기(2026-08-04)** TA-46 후 콜드는 약 49초/4콜/input
      27,903/output 1,259/cache hit 0에서 residual 제안이 structured non-TABLE exclusion과
      동일 ID를 다시 TABLE pattern으로 제출해 공용 duplicate-ID 검증에서 exit 1 종료됐다.
      deterministic scan은 ProFrame을 25좌표/LLM 0회 선택했고 main pattern 291매치/98 edge/
      완전 not-found 193과 exclusion support를 통과했다. residual 입력은 기존 pattern이 이미
      매치했지만 table/caller가 해소되지 않은 사례뿐이며, matched SKILL의 access/exclusion/
      DDL transform은 문서 소유다. known SKILL에서는 residual LLM을 0회로 만들고 case/evidence를
      unresolved로 그대로 보존한다. 변종은 runtime override가 아니라 SKILL 데이터와 회귀로
      승격한다. unmatched custom의 기존 residual 제안·검증·fatal 경로는 보존해 반증한다.
      red 최소 재현이 수정 전 known 경로의 baseline linker 호출을 잡았다. `run_residual_loop`
      최초 경계에서 matched 계약을 닫아 linker/LLM 0회, 입력 case 순서·dict 객체·attempt evidence
      무손실 반환을 단언했고 unmatched 회귀를 보존했다. step5 108 tests, 전체 **780 tests
      OK(skipped=5)**, compileall, prompt hash 31, diff-check 통과. 실제 corpus 0콜·193건 보존과
      전체 콜드 성공은 아직 미검증이라 체크하지 않는다.
- [ ] TA-48 **구현·전체 회귀 완료, 실 Scan 시간 대기(2026-08-04)** TA-47 후 콜드에서 known SKILL 선택은
      LLM 0회였지만 17.8초가 걸렸다. `_select_verified_skill`이 ambiguity 검출을 위해 6개
      등록 계약을 모두 판정하는 것은 정확하지만, 각 `verify_table_patterns`가 동일한 23개
      CodeNode 소유 파일을 다시 읽고 live-source lexing과 C adjacent/nested view를 다시 만든다.
      source bytes/lexed views는 run 불변 입력이므로 한 번만 fail-closed 준비해 후보 verifier가
      재사용하고, 후보별 regex/exclusion/DDL transform/table resolve/verdict는 독립 유지한다.
      첫 통과 조기종료나 skill/언어/이름 prefilter는 금지한다. 다중 후보 fixture의 read/lexing
      1회·verdict 동등성·unknown/unique/ambiguity 회귀와 실제 Scan 전후 시간을 검증한다.
      불변 `PreparedLinkSources`를 도입하고 root/file scope 불일치 재사용을 거절한다. unique·
      unknown·ambiguity에서 파일당 lexing 1회, 전체 793 tests, compileall, hash 31,
      diff-check가 통과했다. 실제 corpus Scan 시간은 다음 콜드에서 판정한다.
- [ ] TA-49 **구현·전체 회귀 완료, 콜드/직접 의미 검증 대기(2026-08-04)** TA-47 후 콜드는 known SKILL residual 193건을
      0.0초·LLM 0회로 보존하고 전역 LLM peak 64까지 진행했지만, code summary의 자유
      `domain_terms` 배열이 실제 call identifier를 표제어로 제출해 92.3초/54 성공콜에서
      fail-closed 종료됐다. 성공 summary 28건 중 용어를 낸 응답은 이 한 건뿐이었다. 현재
      schema는 term/meaning/evidence의 모양과 10개 상한만 강제하고 실제 구조 금지집합은
      prompt/schema에 없으므로 자연어 부탁을 무시한 약한 모델이 문법상 유효한 오답을 만들 수
      있다. validator 완화·항목 drop·내용 재요청은 금지한다. source와 linked external evidence의
      exact lexical span을 ontology lexer 기반으로 전수 inventory하고 구조 식별자를 exact
      제외한다. 최종 출력은 evidence token span 좌표만 property key로 허용하는 폐쇄 map
      (`propertyNames.enum`, `maxProperties=10`)으로 강제하고 term/evidence는 코드가 좌표에서
      파생한다.
      framework/DBMS 공용 factory와 canonical normalization을 쓰며 DBMS node key도 required
      object로 닫는다. prompt가 허용하던 external-only term을 validator가 source-only로
      거절하던 계약 모순도 모델-visible external raw/numbered evidence까지 같은 정의로 고친다.
      구조 이름·정상 사람용 동명이인·Unicode/공백/구두점·external-only·빈 후보·10/11·unknown
      key·node 누락/과잉·schema cache를 red/green 반증하고, prompt+schema 크기와 cold token/
      latency·직접 의미 정확도를 비교한다. 특정 corpus 이름/언어 분기를 넣지 않는다.
      초기 후보별 optional field는 실제 대상에서 약 15.4KB라 폐기했고 compact map 진단은
      241후보·7,816 bytes였다. synthetic 실제 서버 호출은 schema 강제를 통과했으나 streaming
      Pydantic 직렬화 경고가 드러나 TA-50으로 편입했다. TA-51~TA-53까지 반영한 전체 792 tests와
      prompt hash 31이 통과했지만 cold 결과와 직접 의미 판정은 아직 미검증이다.
- [ ] TA-50 **구현·전체 회귀·실서버 반증 완료, 콜드 대기(2026-08-04)** TA-49 synthetic 실제 서버 호출에서 동적 응답은
      정상 파싱됐지만 `langchain_openai` streaming chunk `model_dump()`가 SDK의
      `parsed=None` 선언과 동적 Pydantic instance 불일치 경고를 냈다. 로컬 dump와
      MockTransport는 경고 0, 실제 서버에서 Pydantic class 경계는 경고 1, 동일 canonical JSON
      Schema dict 경계는 경고 0으로 재현했다. 공급자에는 canonical JSON Schema를 전달하고,
      raw/usage 확보 뒤 runner가 원 response model로 `model_validate`하여 내부 타입을 복원한다.
      JSON parse/Pydantic validation 실패는 raw와 원인을 기록하고 fail-closed하며 내용 재시도는
      없다. streaming repetition guard·usage·cache schema fingerprint를 유지하고 static/dynamic,
      cache hit, invalid JSON, semantic invalid, actual warning 0을 반증한다. canonical JSON
      Schema 공급자 경계와 strict raw JSON/Pydantic 복원을 구현했고 잘린 JSON 보정도 거절한다.
      실제 streaming 동적 model 호출 warning 0·1.1초와 전체 792 tests가 통과했다. Anthropic
      실호출은 미검증이다.
- [ ] TA-51 **구현·전체 회귀 완료, 콜드 대기(2026-08-04)** TA-49 재감사에서 candidate builder가 활성 코드의
      모든 lexical atom을 schema 후보로 넣어 parser/context에 없는 지역변수·예약어·미해소
      호출명까지 약한 모델이 선택할 수 있음을 확인했다. prompt의 “구조 식별자 금지”와 schema가
      모순이고 schema 토큰도 증가한다. ontology `identifier_pattern`과 문자열 마스킹으로 활성
      identifier exact set을 후보/validator 공통 금지집합에 합치고, raw active atom 후보 생성은
      제거한다. 주석·문자열·linked external 사람용 span은 유지하며 이름 모양 휴리스틱은 금지한다.
      keyword/local/unknown-call 제외, 주석 고유명과 한 단어·다단어 문자열 보존, Unicode와 언어
      profile 회귀, 실제 후보/schema 크기를 반증한다. 활성 atom 후보 생성을 제거하고 ontology
      identifier exact set 및 identifier 포함 span을 차단했으며 전체 792 tests가 통과했다.
- [ ] TA-52 **구현·전체 회귀 완료, 콜드 대기(2026-08-04)** 폐쇄 selection map은 JSON key 순서를 의미로
      보장하지 않는데 normalization이 모델 key 순서를 canonical list/최종 dict에 그대로
      전파한다. 같은 선택 집합은 candidate inventory의 첫 source 좌표 순서로 항상 조립하고,
      meaning은 해당 선택 key 값을 그대로 보존한다. framework/DBMS 역순 key와 cache
      round-trip의 동일 결과를 반증한다. inventory source 순서 정규화와 역순 반증, 전체 792
      tests가 통과했다.
- [ ] TA-53 **구현·전체 회귀·실서버 schema 반증 완료, 콜드 대기(2026-08-04)** TA-51 후 partial summary 28건의 비내용 계측에서
      candidate min/median/p95/max 0/114/587/678, schema bytes 1,128/5,424/26,175/28,703을
      확인했다(external 0). 모든 n-gram term 문자열을 schema enum에 반복하는 구조가 토큰/
      grammar 크기를 키운다. 후보 절단 없이 human evidence segment를 한 번만 indexed token
      map으로 summary user 입력에 주고, schema는 `E1:0-2` 같은 짧은 span 좌표 key만 허용한다.
      코드는 immutable inventory에서 exact term/evidence를 파생하며 구조 identifier token을
      포함한 span은 제외한다. framework summary 전용/DBMS node별 입력 격리, 정확도 반대 사례,
      cache fingerprint, 실제 서버와 cold token/latency를 반증한다. 구현 후 같은 28건에서
      schema+evidence 합계는 min/median/p95/max 1,130/3,132/9,308/12,112 bytes로 감소했고,
      실제 서버 좌표 schema는 warning 0·1.0초, 전체 792 tests와 prompt hash 31이 통과했다.
      cold token/latency와 직접 의미 정확도는 아직 미검증이다.
- [x] TA-54 **완료(2026-08-04)** `global_forbidden_for`가 ontology lexer의
      모든 예외를 잡아 아무 기록 없이 전역 구조 이름 전체를 금지집합으로 반환한다. 이는
      lexer/언어 계약 실패를 숨기고 주석·메시지의 정상 사람용 동명이인 표제어를 조용히 제거하는
      정확도 손실이며, 바로 뒤 inventory builder가 같은 lexer를 다시 호출하므로 복구 경계도
      아니다. 광범위 catch를 제거해 원인 그대로 fail-closed하고 정상 주석/문자열 생존 계약과
      전체 회귀를 반증한다. red 테스트가 기존 silent fallback을 재현했고 catch 제거 후 lexer
      원인이 그대로 전파되며 정상 주석/문자열 생존 계약을 보존했다. 전체 793 tests,
      compileall, hash 31, diff-check가 통과했다.
- [ ] TA-55 **구현·전체 회귀 완료, 실데이터 대조 대기(2026-08-04)** 전역 감사에서 외부 연결 resolver
      이름/키 인자 위치(`resolver_catalog.py`), XML 주소 역할(`key_roles.py`), scanner의
      manifest·확장자 category(`scanner_input_collector.py`)가 Python 상수로 소유됨을 확인했다.
      새 언어·프레임워크/형식 추가에 코드 변경이 필요해 ontology 데이터 단일 진실과 전 언어
      확장 원칙을 위반한다. scanner manifest는 `errors="ignore"`로 디코딩 손실도 조용히 허용한다.
      구조 표를 `ontology_roles.yaml`의 명시 schema로 옮기고 loader validation→resolver/parser/
      scanner 소비를 단일화하며 unknown profile·잘못된 인자 위치·중복·UTF-8 실패를 fail-closed로
      반증한다. resolver/주소 역할/manifest/category를 ontology section으로 옮기고 모든 소비자의
      Python 상수를 제거했다. 빈/중복/음수·bool 위치, category 확장자 충돌, root manifest 이름을
      loader가 거절하며 manifest UTF-8 오류를 더는 삭제하지 않는다. 데이터 주도 consumer와 strict
      decode를 포함한 표적 27 tests와 전체 808 tests가 통과했다. 실 external link 동등성은 미검증이다.
- [ ] TA-56 **구현·표적 회귀·실제 동일 schema 재현 완료, 콜드 대기(2026-08-04)** `send_real_prompt_to_gpu.py replay`가
      “실제와 동일한 Pydantic schema”를 재생한다고 쓰면서 target별 동적 `code_summary`를 정적
      `SummaryOutput`으로 보낸다. 현재 record에도 canonical response schema가 없어 이번 반복
      실패를 같은 문법으로 재현할 수 없다. 모든 YAML 호출 record에 실제 전송 canonical schema,
      SHA-256, bytes를 보존하고 replay가 기록 schema를 그대로 공급자에 전달한다. dynamic replay는
      생성 단계의 동일성만 주장하고 내부 Pydantic 복원 미검증을 명시한다. schema 있음/없음 단건
      대조로 반복 원인이 문법/입력/확률성 중 어디인지 반증한다. 실제 전송 schema를 SHA-256/byte와
      함께 JSONL record에 보존하고 replay가 무결성 확인 후 그대로 전송하도록 구현했다. 동적 schema의
      내부 Pydantic 복원은 주장하지 않는다. schema 변조·누락과 저장/runner 경계 표적 18 tests가
      통과했다. 새 JSONL 관측 run의 실패 target을 같은 prompt·같은 canonical schema로
      재전송해 `search_text` 문자열 내부 period-1 탭 반복을 다시 재현했다. 무-schema 호출은
      빨리 끝났지만 JSON이 깨져 strict client 검증에서 거절됐으므로 대안이 아니다.
- [ ] TA-57 **구현·전체 회귀·합성 성능 완료, 콜드 대기(2026-08-04)** `write_rule_record`가 호출마다 같은 rule의 전체
      JSON을 read→append→sort→indent→atomic replace하여 O(n²) write amplification과 전역 lock의
      event-loop blocking을 만든다. 평균 28KB 합성 record는 416건 최종 12.33MB인데 208.5배 누적
      rewrite로 17.23초가 걸렸다(28건 0.122초, 100건 1.137초, 200건 4.128초). 여러 rule에 중복돼
      목표 시간에서 무시할 수 없지만 27초 LLM tail 전부의 원인은 아니다. 호출당 O(1) immutable
      append evidence와 공용 strict reader의 read-time stable sort로 단일화하고, crash/parallel/
      corrupt line/write failure/기존 모든 소비자를 반증한다. 구형 병행 format/alias는 남기지 않는다.
      rule별 canonical `.jsonl` append와 strict 공용 reader로 전환했고 숫자 sort key의 타입을
      문자열로 지워 `10`이 `2`보다 앞서던 결함도 반증했다. 병렬 append·corrupt line·schema evidence·
      기존 consumer 표적 18 tests와 전체 808 tests가 통과했다. 같은 416건·11.78MB 재계측은 append 0.186초,
      read+sort 0.065초로 기존 17.23초 대비 append 기준 92.6배 빨랐다.
- [ ] TA-58 **원인 확정·구현·전체 회귀 완료, 콜드 대기(2026-08-04)** 첫 콜드 실패의 깨진 구형 record에서 partial 250자를
      복원한 결과, period-1 반복은 `domain_terms`가 아니라 첫 `search_text` 문자열에서 발생했다.
      소스의 따옴표 포함 리터럴을 설명하던 도중 탭 문자를 반복했으므로 용어 개수 상한만으로는
      해결되지 않는다. 정상 두 호출은 8,479 input/2.36s와 5,029 input/27.94s여서 입력 크기 단독
      가설도 현재 소표본에서는 지지되지 않는다. 새 immutable record로 input chars/tokens, schema
      bytes/depth/key count, 후보·분기 수, 시작 필드/문자, latency를 수집한다. 동일 prompt의 exact
      schema/무-schema 및 충분한 의미 길이를 갖는 bounded-string schema를 고정 표본으로 비교해
      구조·입력·동시성·확률성을 분리한다. prompt 부탁, 빈값 성공, 내용 재시도, 원인 미확정 schema
      변경은 금지한다. 새 관측 run에서도 동일 target·동일 `search_text` 위치에서 반복됐고 exact
      replay의 json_schema는 1.1초에 반복된 반면 같은 schema의 function_calling은 3.3초에 필수
      키를 모두 생성했다. 문자열 pattern과 prompt 지시 변형은 모두 같은 225자 지점에서 실패해
      이 두 축은 폐기한다. function_calling을 서로 다른 동적 schema 형태에 제한 검증한 뒤 운영
      채널 여부를 판정한다. 새 exact replay와 partial 재주입으로 quote가 JSON 문자열 종료로
      해석된 뒤 모델은 리터럴 공백, grammar는 쉼표를 원해 구조 공백만 반복하는 교착을 확정했다.
      동일 실제 prompt의 function call은 원본 schema 사후검증까지 3.1초에 통과했지만 24행 RULE
      schema는 Invalid grammar request로 거절돼 전역 전환은 폐기했다. 배포 기본 json_schema와
      framework summary의 검증된 function override 구현은 완료했고 cold 전건 검증은 대기다.
- [x] TA-59 **완료(2026-08-04)** `_persist_branch_table`이 활성 run의
      RULE 정답지 쓰기 실패를 광범위 catch로 경고만 남기고 분석을 계속한다. branch table은
      RULE↔의무행 뺄셈과 직접 의미 검증의 필수 증거이므로 보조 진단이 아니다. 활성 run에서는
      directory/file write 실패를 원인과 함께 그대로 전파해 분석을 실패시키고, run 없는 독립
      단위 호출만 기존처럼 산출을 생략한다. write failure·run 없음·정상 immutable file과 전체
      fan-out 실패 전파를 반증한다. 광범위 catch를 제거해 활성 run 쓰기 실패를 그대로 전파하고
      run 없는 단위 호출만 적재를 생략한다. 정상·run 없음·write failure 표적 회귀가 통과했다.
- [x] TA-60 **완료(2026-08-04)** function-calling 경로에서 LangChain은 생성
      인자를 `AIMessage.tool_calls[*].args`로 정규화할 수 있는데 진단기의 raw 추출은
      `additional_kwargs.tool_calls[*].function.arguments`만 읽어 성공 응답을 "raw 없음"으로
      오판한다. streaming callback도 빈 token일 때 `chunk.text`만 읽어 tool-call argument delta의
      반복을 놓칠 수 있다. 두 표현을 공용 의미로 추출하되 중복 공급은 피하고, string/dict args,
      ChatGenerationChunk.message, 빈 delta, 반복 중단 partial을 반증한다. 진단기는 parsed payload를
      기록 canonical schema로 다시 검증해 required/extra/type 위반을 성공으로 보고하지 않는다.
      추가 실측에서 `with_structured_output(..., method="function_calling", strict=True)` 변환이 전달된
      schema dict를 제자리 변경해 typed-map의 `additionalProperties: {$ref: ...}`를 `false`로
      바꾸는 것도 확인했다. 공급자 전달본은 deep copy로 격리하고 원본 fingerprint·bytes가 호출
      전후 동일한지 반증했다. 변형된 공급자 tool schema와 원본 사후 검증 schema를 혼동하지
      않는다. normalized/provider raw와 tool delta, mutation 표적 회귀가 통과했다.
- [ ] TA-61 **채널 판정·구현·전체 회귀 완료, 콜드 대기(2026-08-04)** 현 헌법 1.3.0은 2026-08-03 probe에서
      hang이 재현되지 않았다는 근거로 simple call의 `response_format json_schema strict`를
      공인했다. 그러나 신선한 실제 prompt·동일 schema replay에서 xgrammar 경로의 반복이 재현됐고
      같은 schema의 tool/function-calling 경로는 성공했다. 한 성공으로 전역 전환하지 않는다.
      summary의 다후보 폐쇄 map, RULE의 다행 required map, table insight의 다키 map을 제한된
      실서버 호출로 검증하고 모두 strict schema·raw·usage·repetition guard 계약을 만족할 때만
      runner를 명시적 `function_calling, strict=True`로 전환한다. 그 경우 헌법을 1.3.1로 개정하고
      spec §9의 역사 문구와 현행 계약을 분리해 모순을 없앤다. 실패하면 이 경로도 폐기하고 조기
      중단·명시적 실패 및 사람 제공 glossary/일반 상한 같은 별도 책임의 대안을 채택한다.
      프롬프트 변형 반복은 금지한다. 특히 strict tool schema가 typed dynamic-key map을
      `additionalProperties:false`로 변환해 표현하지 못하는 경우, 실제 서버가 우연히 유효값을 낸
      한 건을 문법 강제로 간주하지 않는다. 원본 schema 위반 유도 probe 또는 wire 캡처로 강제
      여부를 증명하지 못하면 전역 전환하지 않는다.
      실제 판정은 전역 function 전환 기각이다. LLM config의 배포 기본+출력계약 YAML override,
      schema deep copy, cache-key 채널 격리를 구현하고 헌법을 1.3.1로 개정했다. SGLang 기본은
      json_schema, 실제 exact schema가 통과한 framework summary만 function_calling이다. 표적
      runner/loader/cache/prompt 계약은 통과했고 전체/콜드는 대기다.
- [ ] TA-62 **구현·전체 회귀 완료, 콜드 대기(2026-08-04)** domain term의
      `dict[Literal[candidate keys], Meaning]`은 JSON Schema에서는
      `propertyNames.enum + additionalProperties:$ref`로 올바르지만, LangChain/OpenAI strict
      function tool 변환은 모든 object의 `additionalProperties`를 `false`로 바꿔 후보 key를
      전부 금지한다. 실제 wire 캡처로 변형을 확인했다. 후보 좌표를 한 번만 enum으로 갖는
      `[{key, meaning}]` 최대 10개 평탄 배열로 바꾸고 unknown key·11개·중복 key·빈 후보·역순
      canonicalization을 Pydantic 경계에서 fail-closed한다. term/evidence는 계속 immutable
      inventory에서 파생한다. schema bytes는 기존 typed map과 비교하고, function tool wire가
      satisfiable하며 strict 변환 후에도 후보 enum/maxItems/required가 보존되는지 반증한다.
      이 변경은 tool channel을 채택하기 위한 땜빵이 아니라 두 표준 schema 표현의 실제 호환성
      결함을 제거하는 계약 변경이며 framework/DBMS 공용 factory와 prompt를 함께 갱신했다.
      241후보 schema는 4,239 bytes, strict tool wire 표준 validator 통과, unknown/11개/중복/
      empty/order와 framework/DBMS 표적 회귀가 통과했다.
- [ ] TA-63 **구현·표적·전체 회귀 완료, 콜드 증거 대기(2026-08-04)** 출력 강제 채널이 config 기본과 rule override로
      갈라졌지만 prompt evidence에는 실제 resolve된 `structured_output_method`가 없다. 따라서
      `send_real_prompt_to_gpu.py replay`가 과거 호출과 다른 현행 config 채널을 사용하면서도 동일
      재현처럼 보일 수 있어 TA-58/61의 원인 반증 계약을 깨뜨린다. 성공·캐시 적중·전송 재시도·최종
      실패·usage 부재의 모든 record metadata에 resolve된 채널을 기록하고, schema replay는 명시적
      CLI override가 없으면 기록 채널만 사용하며 구형 기록의 채널 누락은 추정하지 않고 실패시킨다.
      평문/no-schema 진단은 이 요구에서 제외한다. 성공·캐시·반복 중단·usage 부재·구형 evidence·
      명시 override를 반증했고 관련 표적 58/48 tests와 전체 808 tests가 통과했다. 콜드 record에서
      실제 rule별 채널이 남는지는 다음 run으로 검증한다.
- [x] TA-64 **완료(2026-08-04)** 외부 모델/version 고유명과 무관하게
      현행 Rule·Example 계약을 생산 코드 docstring/comment/error, graph schema와 LLM prompt에서
      계속 `v2`라고 부르는 상대 이름이 남아 있다. 동작 분기에는 쓰이지 않지만 현재 계약을 과거
      버전과 비교해야만 이해하게 만들고 prompt 토큰도 낭비한다. 진행 중 cold의 prompt를 중간 변경해
      증거를 섞지 말고 run 종료 뒤 `structured Rule·Example contract`처럼 책임을 나타내는 안정된
      이름으로 일괄 정리한다. 외부 모델명·표준 문법의 정식 `v2`는 건드리지 않는다. 생산 코드·
      prompt·graph·test의 상대 `v1/v2` 이름과 test 파일명을 안정된 책임명으로 바꿨고, 남은 검색
      결과는 API/model/document contract의 실제 버전뿐이다. 표적 126 tests와 prompt hash 31이
      통과했다.
- [ ] TA-65 **원인 확정·구현·실서버 재현·표적 회귀 완료, 전체/콜드 대기(2026-08-04)** framework summary의 이전 단건 function call
      성공을 전체 동적 summary schema 호환성으로 일반화한 판정이 첫 cold target에서 반증됐다.
      새 평탄 domain-term 배열 schema는 표준 validator와 LangChain strict 변환을 통과했지만 실제
      SGLang은 모델 생성 전 172ms에 nested `$defs/$ref`를 포함한 tool grammar를
      `Invalid grammar request`로 거절했다. 동일 recorded prompt/schema에서 로컬 ref만 의미
      동일하게 inline한 제한 역실험으로 `$ref` 지원 여부를 분리한다. 성공하면 provider adapter의
      일반 local-ref 해소를 canonical schema 불변·사후 원본 검증과 함께 설계하고, 실패하면
      function override를 폐기해 조기중단/명시실패 또는 glossary/일반 상한의 별도 대안으로 간다.
      한 표본 성공을 다시 전건 안정성으로 단정하지 않고 동적 후보 경계와 full cold로 검증한다.
      동일 실패 record에서 canonical 1,371-byte schema는 즉시 거절됐고 local ref inline
      1,274-byte provider copy는 3.9초에 통과했다. provider copy만 RFC 6901 local ref를 일반
      해소하고 canonical evidence/사후 검증 schema는 불변 보존하도록 구현했다. 누락·외부 ref·
      sibling keyword·recursive ref는 fail-closed하며 표적 31/126 tests와 exact replay 3.1초가
      통과했다.
- [x] TA-66 **완료(2026-08-04)** `response_cache.load`의 적중 파일
      atime touch 실패와 `_extract_partial_content`의 ValidationError 입력 복원 실패가
      `except ...: pass`로 사라진다. 둘 다 보조 관측 실패라 원래 cache hit/원 예외를 대체하면 안
      되지만 조용히 숨겨서도 안 된다. 원래 결과·실패 흐름은 보존하면서 component/event/error를
      구조화 경고로 남기고 오류주입 테스트로 고정한다. 예외 클래스 선언부의 `pass`와 정상 제어
      흐름은 이 대상이 아니다. 두 보조 실패 모두 구조화 경고를 남기도록 수정했고 원래 cache hit/
      호출 오류가 보존되는 오류주입을 포함한 표적 26 tests와 전체 812 tests가 통과했다.
- [ ] TA-67 **동시 cold에서 모델 한계 반증·자동 용어 경로 폐기 결정(2026-08-04)** local-ref 문제를 고친
      cold는 64 fan-out에 진입했으나 37 summary 중 한 건이 77.25초 뒤 schema의 124개 string enum에
      없는 domain-term 좌표 2개를 반환해 fail-closed했다. LangChain strict wire에는 enum/maxItems/
      required가 정확히 남아 있고 invalid 두 값은 없었다. 같은 record의 단독 exact replay는 3.8초에
      원본 schema 검증을 통과했으므로 배칭 조건에서 큰 문자열 enum 강제가 신뢰 불가능하다. 후보를
      자르거나 invalid를 drop하지 않는다. 허용 후보 전부에 연속 정수 ID를 부여하고 입력에 ID↔좌표
      표를 구조화해 주며 schema는 정수 `0..N-1` 범위로 강제하는 의미 동일 표현을 같은 실패 표본에서
      제한 검증한다. 성공하면 candidate inventory/summary schema/normalizer/prompt 양끝을 공용으로
      변경하고 unknown/out-of-range/duplicate/순서/빈 후보와 동시 cold를 반증한다. 실패하면 자동
      용어 추출의 모델 한계로 확정하고 사람 제공 glossary 계약 또는 명시적 종료를 선택한다.
      동일 실패 record의 후보 124개를 하나도 버리지 않고 `[candidate_id, 좌표]` 입력과 정수
      `0..123` schema로 바꾼 제한 실험은 4.2초, input 9,139/output 464로 끝났고 ID를 원래
      key로 복원한 뒤 기존 canonical schema 검증도 통과했다. 이를 framework/DBMS 공용 inventory와
      schema factory에 반영했다. prompt는 evidence와 ID↔좌표를 한 구조로 주며 output은
      `{candidate_id, meaning}`만 소유한다. exact term/evidence/source 순서는 코드가 immutable
      inventory에서 파생한다. out-of-range/음수/문자열/bool/duplicate/11개/빈 후보/역순과 prompt
      mapping을 포함한 표적 28 tests와 전체 812 tests가 통과했다. 단건 성공은 해결 판정이 아니며
      concurrency 64 cold가 권위다. 네 번째 cold에서 31 summary 중 30건은 통과했지만 한 건이
      74.765초 뒤 4개 항목 중 같은 candidate ID를 서로 다른 meaning으로 두 번 반환했다. 입력
      9,224/output 430이고 반복 폭주는 아니며, 정수 범위·maxItems·required는 보존됐지만 JSON
      Schema가 item 내부 ID의 배열 전역 유일성을 문법으로 강제하지 못했다. 이 내용 오류는
      재시도 없이 fail-closed했고 run은 113초/55콜/input 395,418/output 10,936에서 종료됐다.
      앞서 정한 종료선대로 후보/상한/프롬프트 변형을 더 반복하지 않고 TA-72의 사람 제공 glossary
      경계로 전환한다. 콜드 전체 성공과 graph 정확도는 미검증이므로 TA-67 자체는 닫지 않는다.
- [x] TA-68 **완료(2026-08-04)** 외부 파일 inventory의 config/document/XML
      분류 확장자와 `.env` 이름 규칙이 `pipeline/steps/step3_load_external/inventory.py`의 Python
      상수·분기에 남아 있다. source/외부 linker/scanner의 형식 지식은 ontology 데이터 단일 진실로
      옮겼는데 새 설정·문서 형식을 지원하려면 이 파일만 다시 고쳐야 하므로 같은 원칙을 위반한다.
      실제 parser 구현 보유 여부를 나타내는 `DOCUMENT_ADAPTER_SUFFIXES`는 코드 capability로 구분하고,
      일반 text category suffix/name 규칙만 `ontology_roles.yaml`의 검증된 registry로 옮긴다. 빈 값,
      dot 없는 확장자, category 간 중복, binary precedence, `.env` 보안 skip과 기존 inventory 결과
      전수를 반증한다. 일반 text category를 ontology registry로 옮기고 Python 분기표를 제거했다.
      `path.name.endswith('.env')`는 `.env`가 보안 skip되고 나머지는 suffix 분기로 이미 잡혀 도달
      효과가 없는 중복이어서 제거했다. 표적 11 tests와 전체 821 tests가 통과했다.
- [x] TA-69 **완료(2026-08-04)** summary prompt는 다섯 필드를 각각
      한 번 쓰라고 하지만 canonical/dynamic schema의 required는 `summary`와 `domain_terms`뿐이라
      `search_text`, `logical_name`, `stereotype` 누락을 provider 문법이 막지 못한다. Pydantic default와
      `coerce_stereotype`은 누락/unknown 값을 빈 문자열로 바꿔 원래 모델 실패도 숨긴다. framework와
      DBMS의 실제 strategy별 ontology stereotype 목록을 dynamic schema enum으로 강제하고 다섯 필드를
      모두 required로 만든다. empty/unknown stereotype, 각 키 누락, extra, framework↔DBMS 교차값을
      provider schema와 내부 Pydantic 양쪽에서 fail-closed한다. 결정론 write contradiction은 모델
      의미를 다른 값으로 하이재킹하지 말고 현재처럼 빈 값+구조화 경고로 사람 검토를 표면화할지,
      전체 호출 실패로 올릴지 직접 정확도 증거로 판정한다. 코드노드 framework/DBMS의 다섯 필드와
      module의 세 필드를 모두 required+extra forbid로 만들고 strategy별 ontology stereotype enum을
      provider schema에 넣었다. unknown/empty를 빈값으로 지우던 두 coerce 함수는 명시적 require
      검증으로 교체했다. 결정론 write contradiction은 다른 의미로 바꾸지 않고 기존처럼 빈값과
      구조화 경고로 사람 검토를 표면화한다. 표적 71 tests와 전체 821 tests가 통과했다.
- [ ] TA-70 **원인 확정·구현·전체 회귀 완료, cold 대기(2026-08-04)** concurrency 64에서 summary
      121건은 구조 위반 없이 완료됐지만 한 요청이 62 stream chunk 뒤 120초 무응답으로
      `StreamChunkTimeoutError`를 내고 전체 run이 212.6초에 fail-closed했다. 같은 recorded
      prompt/schema/method는 부하 없는 exact replay에서 3.8초, input 8,478/output 494로 통과해
      요청 내용·작은 1,371-byte schema 문제가 아니라 고동시성 서버 정체/stream idle임을 분리했다.
      현재 transport retry allowlist가 LangChain SDK의 이 명시적 stream timeout을 몰라 허용된
      연결/서버 오류 재시도도 하지 않는다. 해당 타입만 transport retry로 분류하고 repetition/
      schema/content 오류는 계속 재시도하지 않는다. callback을 변수로 보존해 SDK timeout에도
      수신한 tool-argument tail·chars를 error evidence에 남겨 `chunks_received>0`인데 partial 0인
      관측 사각지대를 제거한다. 1차 timeout record→정해진 backoff→성공, 3회 실패, semantic 무재시도,
      partial 보존을 반증한다. server/model/동시성/timeout 설정은 바꾸지 않는다. SDK stream idle
      타입만 allowlist에 추가하고 callback detector tail을 각 retry/final error record에 연결했다.
      3회 제한 retry·semantic 1회 종료·partial 우선순위 표적 tests와 전체 821 tests가 통과했다.
- [x] TA-71 **완료(2026-08-04)** RULE row object는
      anchor만 required이고 canonical default 때문에 `cond`, `then`, `nl` 누락을 허용하며, top-level/
      node별 `questions`도 생략 가능하다. TableDescription의 빈 `columns`, ModulePreAnalysis `intent`,
      Glossary `aliases`와 일부 direct output의 extra field도 같은 문제다. 모든 실제 LLM response
      model의 JSON Schema를 기계적으로 inventory해 prompt가 소유한 키는 의미상 빈 값이 허용돼도
      **키 자체는 required**, object는 extra-forbid로 통일한다. Rule의 cond는 required nullable,
      then은 required list, nl은 required bounded string, Question은 text/reason 모두 required로 만든다.
      빈 분기/빈 질문/빈 용어 같은 정상 의미와 키 누락을 구분하고 framework/DBMS/후처리 전 경로,
      provider schema와 Pydantic 양쪽을 반증한다. 실제 prompt-owned 응답 모델을 기계 inventory하는
      회귀를 추가하고 Rule cond/then/nl/questions, Question reason, module intent, table columns,
      ExternalSkill 설명/provenance 등 누락 키를 required+extra-forbid로 수리했다. ScanResult의 SKILL
      결정론 hydration용 의도적 생략만 별도 예외로 문서화했다. TA-72까지 합친 전체 808 tests가
      통과했으며 실제 provider cold는 다음 실행 대기다.
- [ ] TA-72 **구현·전체 회귀 완료, cold 대기(2026-08-04)** 자동 domain-term 선택을
      code summary의 치명 경로에서 제거한다. 임의로 최대 3개처럼 상한을 낮추면 이번 중복을
      우연히 피할 뿐 유효 용어를 버려 정확도와 맞바꾸므로 금지한다. 프로젝트가 명시적으로 제공한
      glossary가 있으면 그 표제어·별칭·뜻을 폐쇄된 권위 입력으로 사용한다. 제공 glossary가 없으면
      모델에게 용어를 창작·선택시키지 않고
      `domain_terms=[]`를 명시 계약으로 생성하며 summary/search_text/logical_name/stereotype은 계속
      required·fail-closed로 분석한다. 기존 step8 glossary synthesis의 자동 후보 의미 생성과 사람
      입력의 소유권을 섞지 않는다. 먼저 현행 입력/config/graph 소비자를 전수 추적해 단일 입력 형식,
      provenance, 충돌·unknown·중복·빈값 실패 정책과 공개 입력 위치를 설계하고,
      사람 glossary 있음/없음·잘못된 입력·모든 언어 문자열·기존 summary 정확도 불변을 반증한다.
      구현은 코퍼스의 `analysis/glossary.yaml` 하나를 schema 1.0 폐쇄 입력으로 정했다. 모든 키 필수,
      extra/type/앞뒤 공백/빈 별칭/casefold 표기 충돌/1MiB·1000항목·항목당 20별칭 상한/strict UTF-8을
      그래프 wipe 전에 검증하며 오류에는 고객 용어 값을 노출하지 않는다. 파일 자체가 사람이 승인한
      권위 증거이므로 analyzer가 코드 등장 여부를 추측해 per-node 귀속하지 않는다. 없으면 summary
      provider schema가 required `domain_terms=[]`(`maxItems=0`)를 강제한다. 자동 후보 inventory,
      전역 식별자 필터, 용어별 glossary LLM fanout, 세 synthesis prompt, per-node graph field/codec을
      삭제했다. 있으면 기존 검색 소비 계약의 GLOSSARY에 원문 값과 source kind/hash/schema version을
      저장하고 manifest와 의미검증 exporter에 provenance를 남긴다. 표적 65 tests, 전체 808 tests
      `OK (skipped=5)`, compileall, prompt hash 28/28, diff-check를 통과했다. 실제 cache-off/c64 cold와
      graph 검색 소비 동작은 아직 미검증이므로 task는 닫지 않는다. 다섯 번째 cold에서는 사람
      glossary가 없는 경로의 summary 278콜이 모두 `domain_terms=[]` 경계를 통과해 이전 용어 오류는
      재발하지 않았다. 다만 뒤의 RULE 의미 오류로 run이 종료되어 supplied glossary graph/search는
      여전히 unit test 밖에서 검증하지 못했다.
- [x] TA-73 **완료(2026-08-04, cold 중 원칙 감사에서 발견)** Python 상대 `from` import의 공통
      source-module resolver가 `module_path` 대신 존재하지 않는 `mod_path`를 참조해 절대 import
      해피패스 테스트만 통과하고 상대 import 입력에서 `NameError`가 났다. 언어 문법은 계속
      `ontology_roles.yaml.reference_imports`가 소유하게 두고, resolver의 일반 입력 변수만 바로잡았다.
      `from .config import LIMIT`이 같은 package의 선언으로 연결되는 반증 테스트를 추가했다.
- [x] TA-74 **완료(2026-08-04, cold 중 원칙 감사에서 발견)** R/W `CodeLocator`가 절대/상대
      경로 불일치 시 문자열 suffix의 첫 경로를, 같은 줄에 겹치는 코드 범위도 정렬상 첫 노드를
      근거 없이 선택했다. 비슷한 디렉터리 이름·중복 basename·중첩 함수에서 조용한 오귀속이
      가능했다. 경로 구성요소 단위의 유일한 suffix만 허용하고, 중첩 범위는 가장 좁은 소유자를
      선택하며 복수 경로와 동일 범위 동률은 명시 실패하도록 일반화했다. 네 반증 케이스를 추가했다.
- [x] TA-75 **완료(2026-08-04, cold 중 원칙 감사에서 발견)** 외부 `REFER_TO` 링커에 문서상
      폐기한 값 추적이 `SymbolTable`이라는 다른 이름으로 남아 있었다. Java/C의 `=`와
      `final|const`, PL/SQL의 `:=`, 지역 재대입, 상수 `+` 연결식을 코드 정규식과 재귀로 해석해
      전 언어 확장성과 상태 비추적 계약을 위반했다. 직접 호출 인자 리터럴과 AST `REFERENCES`가
      유일하게 지목한 완전한 단일 리터럴 `Constant.value`만 선언 사실로 보존했다. 지역/가변 변수,
      연결식, INIT_BY 재귀 평가와 죽은 인자를 제거하고 `LiteralConstantIndex`로 책임을 다시 명명했다.
      동명이인 상수는 임의 선택하지 않고 미해소하며 언어별 문법 분기는 없다.
- [x] TA-76 **조사 완료·목표 중단선 도달(2026-08-04)** 다섯 번째 cache-off/c64 cold
      (`spec116-cold-20260804-090243`, fresh corpus, `test`)는 215.4초에 RULE leaf 내용 오류로
      fail-closed했다. 319콜, input 2,284,997/output 89,137/cache 0이며 summary 278콜의 latency는
      median 27.327초/p95 45.280초/max 72.000초, rules 11콜은 median 42.609초/p95 55.641초/
      max 57.468초였다. 실패 행의 의미는 맞았지만 prompt가 명시한 `F("원문 호출식")` 대신 허용
      어휘 밖 Python `len(...)`을 생성했다. 외곽 고정키·모든 required·extra-forbid schema는 지켰고
      AST 채점기가 거절했다. 같은 기록의 저부하 exact replay는 2.9초(input 3,943/output 383)에
      JSON Schema를 통과해 57.468초 중 큰 부분이 고동시성 queue/batch tail이라는 증거가 됐지만,
      진단 도구가 leaf 내용을 출력하지 않아 replay 의미 통과 여부는 모른다. `len` 허용/자동 치환은
      언어 의미 완화·코드 하이재킹이고, 내용 재시도는 폐기 계약 위반이다. 이미 enum 불이행→중복 ID→
      이번 폐쇄 어휘 불이행까지 평탄한 구조 내부의 서로 다른 의미 오류가 반복됐으므로 추가 prompt
      문구 변경과 전체 cold 반복을 중단한다. 성공 graph가 없어 시간 목표, 결정론 전수, 의미 20개+
      모든 TABLE, 8단계 delta는 미검증이다.

## 2026-08-04 재개 — 품질 finding 경계·Python 의미 계약·성능/정확도 완주

- [x] TA-77 **완료(2026-08-04) — 전체 run 종료 경계 바로잡기.** 다섯 번째 cold의 단일 RULE
      검증 finding이 `RuleRowsRejectedError → run_failed`로 승격되어 모든 독립 LLM 결과와
      후속 검증을 폐기했다. 모델의 schema/반복/길이/leaf/근거/의미 위반은 운영 실패가 아니라
      좌표 있는 품질 finding이다. 유효 sibling 행은 보존하고 invalid/unverified 후보는 신뢰
      RULE·EXAMPLE·AFFECTS 생성에서 격리하며, candidate/evidence record와 정확 사유를
      `CodeNode.extra`·구조화 로그·run 집계에 남긴 뒤 독립 작업을 계속한다. content 재시도와
      빈 성공 폴백은 금지한다. 전송 재시도 고갈, budget, raw/usage/evidence 부재, 입력 부재,
      결정론 불변식 붕괴는 계속 fatal이다. framework/DBMS와 summary/rules/insights/module/table/
      component의 같은 경계를 생산자→소비자 끝까지 inventory하고 공용 타입으로 수렴한다.
- [x] TA-78 **완료(2026-08-04) — source→canonical Python 조건 의미 대조 제품화.** `conditions_equal`은
      테스트에만 있고 생산 채점기는 고정 `COND_HELPERS`만 검사해 실제 의미를 대조하지 않았다.
      언어별 원문 관용구는 `ontology_roles.yaml` 데이터에만 선언하고 범용 compiler가 분기표
      원문을 Python AST leaf로 정규화한다. C `strlen(x) != 13` ↔ `len(F("x")) != 13`처럼
      원문과 언어 데이터가 정확히 증명한 intrinsic만 허용한다. 단순 `len` 전역 허용, 함수명
      hardcode, 모델 문자열 자동 치환은 금지한다. 증명 불가 표현은 오답으로 단정하지 않고
      `unverified` finding으로 남긴다. decision/LOOP의 `cond=null`, 의미 반전, 다른 인자,
      근거 없는 builtin, complement를 반증한다.
- [x] TA-79 **완료(2026-08-04) — 규칙 행 의미/효과 대조 완결.** 다섯 번째 실패 응답에서 LOOP R2012의
      `cond=null`과 entry R2005의 `then=[]`가 현 채점기를 통과했다. 분기표 role별 cond 필수성,
      entry/exit/handler 직접 효과, then↔직접 call/R/W/return의 확실 사실과 finding 축을 분리해
      제품화한다. 결정론이 증명하지 못하는 의미를 거절로 만들지 않는다. 정답지/분기표/채점기
      자체가 틀릴 수 있으므로 실제 원문과 반대 사례로 먼저 반증한다.
- [ ] TA-80 **진행 중 — 성공 cold·성능 원인 분리·최종 검증.** TA-77~79와 전체 회귀/hash 후
      cache=false, concurrency=64, fresh corpus, Neo4j `test`로 cold를 실행한다. 입력 token으로
      KV 순서를 판정하고 call별 queue/TTFT/generation/latency·동시성·repetition/length·Neo4j
      query latency/index를 로그와 read-only 측정으로 분리한다. 목표는 정확도 저하 없이 11~15분.
      성공 graph에서 deterministic CALL/R/W/RULE↔분기표/EXAMPLE 전수 정답지, 모든 EXAMPLE
      Python 시스템 비교, 코드노드 20개+모든 TABLE의 LLM 속성 직접 의미 대조, 관계/ontology
      감사와 8단계 delta를 완료한다. 통제 불가능한 모델 랜덤성은 제한 재현과 대안 검증 후에만
      한계로 선언하고 맨땅 반복 실험을 중단한다.
- [x] TA-81 **완료(2026-08-04, TA-79 감사에서 발견)** `Branch.callees/effect_atoms`가 AST
      branch의 전체 start~end를 읽어 child/else range의 호출과 효과까지 부모의 “직접” 사실로
      귀속했다. 부모가 자식/형제 호출을 then에 복사해도 채점기가 통과할 수 있는 소유권 결함이다.
      언어 키워드나 이름으로 보정하지 않고 parser child 좌표 범위를 direct body에서 제외해 각 행이
      자기 직접 효과만 소유하게 했다. 중첩 IF/ELSE의 x/y/z가 각각 행 1/5/9에만 귀속되는 반증과
      기존 branch/rule 회귀 62건이 통과했다.
- [x] TA-82 **완료(2026-08-04) — 조건 계약의 언어 특화·인용 오류 제거.** `starts_with`를
      Python 표준처럼 만든 helper와 C 전용 comparison idiom을 제거했다. 소스 호출은
      `F("원문 호출")`로 보존하고 Python 연산자만 정규화한다. ontology가 증명한 `len` 예외만
      범용 compiler가 허용한다. 문자열 리터럴 내부의 identifier를 치환하지 않는 quote-aware
      경계와 정상/역방향 비교 반증을 추가했다.
- [x] TA-83 **완료(2026-08-04) — 행 직접 효과 소유권 완결.** branch별 direct source에서
      return·assignment를 추출하고, C의 복사 함수 효과 지식은 이름 분기 코드가 아니라
      `ontology_roles.yaml` 데이터로 선언했다. `ret`·`set`·`call`·table effect가 현재 행의
      직접 근거와 맞는지 검증하며, 결정론이 증명하지 못한 mutation/assignment는 오답으로
      추측하지 않고 `unverified` finding으로 격리한다.
- [x] TA-84 **완료(2026-08-04) — agent 계열 모델 출력 오류 격리.** agent runtime의 schema 누락,
      반복, 길이, 모델 call limit과 scan/external/residual/call-review 후보 검증 실패를 공통
      `AgentModelOutputError` 경계로 수렴했다. 좌표 있는 finding을 기록하고 해당 후보만 격리하며,
      결정론 결과와 독립 단계는 보존한다. 내부 state/invariant와 운영 실패는 계속 fatal이다.
- [x] TA-85 **완료(2026-08-04, cold preflight에서 발견) — stale parser AST 오류 좌표.** 기본
      corpus의 오래된 C AST가 CASE body를 SWITCH sibling으로 평탄화한 채 남아 cold가 6.8초/
      LLM 0콜에 fail-closed 했다. analyzer 보정은 추가하지 않는다. 다만 예외가 파일을 숨겨 원인
      추적이 어려웠으므로 AST 경로와 SWITCH/child 줄 좌표를 오류에 포함하고 반증 테스트로
      고정했다. 성능 cold는 canonical parser 복제본으로 다시 실행한다.
- [x] TA-86 **완료(2026-08-04, canonical cold에서 발견) — R/W 소유자 계약 수리.**
      `CodeLocator`가 모든 CodeNode를 후보로 받아 IF/ELSE/CASE를 R/W source로 선택했고,
      canonical else-if AST의 동일 범위 ELSE+IF 259개 중 하나에서 동률 fatal이 났다. CALL과
      동일한 공용 `ROUTINE_LABELS`만 R/W 소유 후보로 사용한다. 제어 구조는 RULE 근거일 뿐
      READS/WRITES 주체가 아니다. 제어 노드 동률과 중첩 routine/경로 ambiguity 반증을 함께
      유지한다.
- [x] TA-87 **완료(2026-08-04) — run-scoped LLM slot 단일 진실 복원.** `run_executor`는 모든 LLM
      호출이 하나의 `PrioritySemaphore`와 peak 계측을 공유한다고 선언하지만
      `module_pre_analysis.py`만 별도 `asyncio.Semaphore(max_concurrency)`를 만든다. 현 corpus의
      23콜은 다음 단계와 겹치지 않아 현재 폭주의 증거는 아니지만, 향후 단계 중첩 시 총량 64를
      초과하고 최종 peak에서 누락될 수 있는 DRY/관측성 결함이었다. `AnalysisHandoff.slots`를
      module pre-analysis에도 주입하고, 외부에서 slot을 점유한 동안 실제 LLM 호출이 대기하는
      반증으로 하나의 peak 계측에 포함됨을 고정했다.
- [x] TA-88 **완료(2026-08-04, cold 2분 tail 실측) — partial stream stall 재시도 제거.**
      summary 최소 8콜이 tool argument 76 chunks/약 172자를 낸 뒤 120초 idle timeout에
      걸렸는데 `StreamChunkTimeoutError` 이름만 보고 최대 3회 transport retry했다. 첫 chunk가
      없는 idle은 연결/서버 실패로 제한 재시도하되, partial이 있는 idle은 `ModelOutputError
      (stalled)` finding으로 한 번만 기록·격리한다. 모델 의미를 자동 복구하거나 빈 성공으로
      만들지 않는다. 무partial/partial 반대 사례를 각각 고정한다.
- [x] TA-89 **완료(2026-08-04) — 품질 finding을 보존한 첫 cold 완주와 기존 성능 가설 반증.**
      canonical parser corpus, response cache false, concurrency 64, Neo4j `test`로
      `spec116-cold-20260804-111000`을 끝까지 실행했다. run은 단일 모델 오류로 죽지 않고
      `completed_with_findings`로 끝났으며 1,896.5초(31분 36.5초), 1,025 successful calls,
      input 7,939,273/output 674,393/cache hit 0, peak 64였다. 4,109 의무 행은 trusted RULE
      1,500 + rules finding 2,609로 뺄셈 0이다. 따라서 프롬프트 바이트를 줄이지 않은 순서
      변경이 API input을 4M 이하로 만든다는 주장은 현재 실행으로 다시 반증됐다. 전체 finding은
      rejected 1,036/unverified 1,223/unavailable 373이며, 성공을 가장하지 않는다.
- [x] TA-90 **구현·회귀 완료, cold 판정은 TA-80(2026-08-04) — RULE의 기계적 cond/then 재생성 제거.** 첫 완주에서 rules
      478 successful calls가 output 518,179 tokens, 누적 latency 82,260.9초를 차지했고 latency
      median 124.0초/p95 440.2초/max 920.6초였다. 60초 초과가 365/478이므로 일부 repetition이
      아니라 계약 전체가 병목이다. 실제 4행 응답 하나는 최상위 필드 복사 70개를 `then`에 다시
      타이핑해 3,231 output tokens/593.2초를 썼다. branch inventory가 이미 source 좌표·조건·직접
      return/assignment/call을 결정론적으로 보유하고 채점 정답지로 사용하는데 모델에게 같은
      문자열을 재생성시키는 것은 원칙 I 위반이며 오탈자·개행 때문에 정상 sibling까지 잃는다.
      언어 지식은 ontology에만 두고 범용 source-condition compiler와 direct-effect materializer가
      `anchor_line/cond/then`을 소유하게 한다. LLM은 행별 `nl`과 실제 공백 `questions`만 고정키로
      생성한다. compiler가 의미 구조를 증명하지 못한 조건은 원문 fragment를 정직하게 보존하고
      정규화 수준을 표면화하며, 이름/언어/코퍼스 특화와 모델 출력 자동 치환은 금지한다. 모든 행
      뺄셈, EXAMPLE 파생, 기존 의미 비교, 다언어 반대 사례와 출력 토큰 예산을 먼저 반증한다.
- [ ] TA-91 **진행 중(2026-08-04) — 구조 위반의 행 단위 격리.** provider가 nested array
      item pattern을 강제하지 않아 한 `then` 문자열에 개행으로 여러 효과를 합친 응답이 22배치에서
      관측됐다. 현재 Pydantic outer parse가 한 행의 leaf 오류를 배치 전체 실패로 만들어 정상
      sibling도 unavailable 처리한다. 새 NL-only wire 계약에서도 한 행의 NL/좌표 오류가 정상 행을
      폐기하지 않도록 provider 구조 검증과 행별 의미 검증 경계를 분리하고, 후보/사유/evidence를
      해당 좌표에만 기록한다.
- [x] TA-92 **완료(2026-08-04, TA-90 반증에서 발견) — 조건 추출·번역의 다언어 오판 제거.**
      Python/SQL의 `if len(...)`/`IF length(...)`에서 첫 함수 호출 괄호를 전체 조건 괄호로
      오인했고, SQL 연산자 치환은 문자열 리터럴 안의 `=`/`AND`까지 바꿀 수 있었다. 괄호형
      조건 여부와 source operator는 `ontology_roles.yaml` 데이터로 옮기고, 범용 scanner가
      인용부호 밖에서만 치환한다. ontology가 선언한 C/Python/SQL length intrinsic과 boolean
      shell은 같은 Python AST가 되며, 미등록 COBOL/연쇄 비교는 부분 해석하지 않고 정확한
      `F("source")` 원자로 보존한다. 이전 모델 전용 500자 leaf 상한도 source-owned 조건을
      절단하므로 RULE에서 제거하고 EXAMPLE 표시 leaf에만 유지했다.
- [x] TA-93 **완료(2026-08-04, 전체 회귀가 발견) — RULE 토큰 절감 중 의미 힌트 소실 복원.**
      `inherit_inputs:false`로 중복 SOURCE를 제거하면서 referenced symbols, external evidence,
      R/W·table ops, callee search_text/return 집합, SKILL api_contracts까지 함께 빠진 것은 구조화
      입력 원칙 위반이었다. 전체 SOURCE는 되살리지 않고 이 권위 사실만 compact JSON으로
      framework/DBMS RULE prompt에 복원했다. 현재 summary search_text도 prompt-only 문맥으로
      전달하며 direct_effects의 소유권을 확대하지 않는다. 전체 838 tests OK(skipped=5),
      compileall, prompt hash 28, diff-check를 통과했다. 새 cold의 벽시계·토큰·의미 정확도는
      아직 미검증이며 TA-80에서 판정한다.
- [x] TA-94 **완료(2026-08-04, 두 번째 cold 대기 감사) — inactive AST의 가짜 RULE 제거.**
      직전 run 분기표의 decision 2,547행을 전수 compiler에 넣자 9행이 조건·효과 모두 빈 채
      실패했다. 실제 source 대조 결과 전부 ontology-declared `#if 0` 안의 과거 코드였지만 parser
      좌표가 남아 의무 RULE로 승격된 것이었다. 진행 중 cold는 성능 가치가 없어 정확한 PID만
      종료했다. `build_inventory`는 언어 문법을 알지 않고 기존 `split_source.live()`가 완전히 빈
      subtree를 실행 불가능 좌표로 제외한다. active sibling은 보존하는 반증을 추가했다.
- [x] TA-95 **완료(2026-08-04, materializer 전수 감사) — CASE와 중첩 도달 조건 복원.**
      최초 materializer는 CASE를 `F("case 1")`로 만들고 nested/else-if decision에서 조상 도달
      조건을 누락했다. CASE label prefix는 ontology 데이터로 선언하고 controller selector와 exact
      label을 비교하며 C octal canonical 값을 보존한다. 일반 nested decision은 established ancestor,
      complement 아래 decision은 대응 ancestor의 `else_of`를 path condition에 포함한다. complement도
      direct negation과 outer established/negated 조건을 함께 보존한다. grader는 모델이 재작성한
      원문과 비교하지 않고 동일 materializer의 expected AST와 의미 비교한다. 전체 840 tests
      OK(skipped=5), compileall/hash/diff-check를 통과했다.
- [x] TA-96 **완료(2026-08-04, 두 번째 cold에서 발견) — prompt 값의 JSON 무결성 복원.**
      `_safe_format`이 placeholder 치환 뒤 템플릿 brace escape를 전역 치환하여, 주입된
      `rule_evidence_json`의 중첩 `}}`까지 하나씩 삭제했다. 저장된 cold prompt 316건에서
      실제 malformed JSON을 재현했다. 템플릿을 한 번만 스캔해 escape와 placeholder를
      처리하고 주입값은 opaque byte sequence로 보존한다. nested JSON/escape/unknown brace
      회귀 테스트를 추가했고 새 prompt 값은 nested JSON 그대로 보존된다. cold 재측정은 TA-80이다.
- [x] TA-97 **완료(2026-08-04, 두 번째 cold에서 발견) — Python 의미 동등값 채점.**
      materializer가 C `0x00`을 Python 정수 `0`으로 올바르게 정규화했지만 grader가 원시
      문자열과 비교해 entry RULE을 대량 rejected 처리했다. 정답지와 candidate를 같은
      Python AST scalar 의미로 비교하되, 증명할 수 없는 source fragment는 기존 exact `F`
      비교를 유지한다. `0x00`↔`0` 통과와 quoted numeric↔number 거부를 회귀로 고정했다.
- [x] TA-98 **완료(2026-08-04, 두 번째 cold 15분 초과에서 발견) — 유한한 호출 경계.**
      stream idle timeout은 청크를 조금씩 계속 내는 장출력/비정확 반복을 끊지 못했다.
      content/model 출력 하나가 전체 run을 닫지 않도록 호출별 총시간·출력 경계를 명시하고
      해당 batch만 `unavailable` finding으로 격리한다. 정상 20행 응답을 자르지 않는 경계는
      실제 latency/입출력 분포를 기준으로 framework/DBMS 의미 호출에 180초 총시간 경계를
      두었다. deadline은 transport 재시도하지 않고 `deadline` ModelOutputError로 해당 대상만
      격리한다. 최종 적정성은 malformed JSON 수정 후 cold에서 재측정한다.
- [x] TA-99 **완료(2026-08-04, 입력 압축 반증에서 발견) — nested complement 외부 조건 복원.**
      inventory의 외부 조건 성립 상태 정본은 `holding`인데 materializer가 존재하지 않는
      `established`만 검사해 nested else에서 바깥 조건을 누락할 수 있었다. 양 표기를 안전하게
      읽되 현행 `holding`을 권위로 처리하고, `outer AND else_of(inner)` AST와 NL evidence refs를
      반증 테스트로 고정했다.
- [x] TA-100 **완료(2026-08-04) — RULE NL 증거의 중첩 경로 중복 제거.**
      모델 책임이 아닌 완전 확장 Python `condition`을 모든 sibling 행마다 반복해 큰 시료에서
      행 증거 52,227자 중 약 45,783자를 중복 소비했다. 최종 Rule cond는 기존 materializer에
      그대로 보존하고, NL 입력은 local `condition_definitions` 한 벌과 행별
      `condition_refs(satisfy/not_satisfy)`로 정규화했다. 언어별 로직 없이 inventory 관계만 쓰며
      JSON parse·중첩 경로·CASE 계약을 테스트했다.
- [x] TA-101 **완료(2026-08-04, 세 번째 cold 정답지 게이트 발견) — complement 짝 타입.**
      C `else if`의 부모 IF 안에 LOOP와 ELSE가 나란히 있는 parser 형상에서 “가장 가까운 decision”이
      LOOP를 ELSE의 직접 짝으로 골라 `i_flag==1 AND i_flag==2` 모순 RULE 3행을 만들었다. ELSE가
      어느 제어 타입을 보완할 수 있는지는 언어별 ontology 데이터로 선언하고, parser 소유권인
      허용 parent를 먼저 사용하며 평탄 형상에서만 허용 predecessor를 사용한다. C else-if,
      nested IF/ELSE, Python loop-else 계열을
      C는 IF만, Python은 IF/LOOP를 허용하는 ontology 계약과 평면/중첩 회귀를 추가했다.
      실제 parser JSON+원문 Check_Sum32를 재조립한 7행은 rejected/missing/fabricated/duplicate/
      assignment-outside 모두 0이며 R6014는 `else_of(IF@6003) AND i_flag==2`로 복원됐다.
- [x] TA-102 **완료(2026-08-04, Check_Sum32 직접 정답지 대조에서 발견) — 계수형 LOOP 머리의 거짓 대입 제거.**
      범용 한 줄 대입 추출기가 `for(li=0;li<=10;li++)`를 직접 효과로 읽어
      `set(F("for(li"), ...)`라는 존재할 수 없는 대상 쓰기를 만들었다. 언어별
      `loop_header_parts`가 정확히 세 부분임을 입증한 머리만 대입 증거에서 제외하고,
      일반 LOOP/while 및 본문 대입은 보존한다. 실제 Check_Sum32 재조립과 회귀테스트로
      ontology가 증명한 3부분 counted-loop 머리만 대입 스캔에서 제외했다. 실제 Check_Sum32
      재조립에서 `F("for` target은 0이고, 일반 본문 대입·호출·반환은 보존됐다.
- [x] TA-103 **완료(2026-08-04, 동일 직접 대조에서 발견) — 선언식 대입 target 정규화.**
      entry RULE이 `long rc = 0`의 쓰기 대상을 `rc`가 아니라 `F("long rc")`로 저장한다.
      이는 Python 표기법으로 비교할 실제 lvalue가 아니며 이전 cold 로그에 다수 존재한다.
      언어별 선언 형상은 ontology 정규식 데이터에만 두고, 범용 추출기는 named `target`만
      읽어 실제 lvalue로 정규화한다. 초기화는 실제 실행 효과로만 보존하며 이후 현재값 힌트로
      전파하지 않는다. C/Java/Python/SQL 회귀와 실제 Check_Sum32 entry로 검증했다. 416루틴·
      4,033행 전수 감사에서 `F("long `·`F("int ` 같은 선언 target은 0이었다.
- [x] TA-104 **완료(2026-08-04, 416루틴 결정론 전수 대조에서 발견) — 경로 여집합 소유권 전수 복원.**
      parser corpus 416루틴·4,033 RULE 의무 행을 LLM 없이 재조립·채점하자 8루틴에서
      서로 배타적인 값이 `AND`로 누적된 정답지 모순이 남았다. Check_Sum32 단건 수정으로
      끝내지 않고 8개 parser 부모/형제 형상을 비교해 공통 구조 원인을 확정한다. 함수명·값
      특화 없이 ontology 소유권과 범용 경로 계산을 수정했다. 부모 우선·평탄 sibling 보조로
      바꾼 뒤 416루틴·4,033행 전수 채점 42.924초에서 rejected/missing/fabricated/duplicate/
      out-of-assignment가 모두 0임을 확인하고 cold를 재개했다.
- [ ] TA-105 **진행 중(2026-08-04, EXAMPLE 4,033행 전수 대조에서 발견) — else_of 합성 재귀 폭증 제거.**
      RULE 전수 materialize 11.1초·grade 42.9초와 달리 EXAMPLE 합성만 1분을 넘겨 중단됐다.
      깊은 else-if에서 동일한 이전 행 `else_of`를 규칙마다 재귀 재해석하는 중복을 호출 그래프
      단위 memo로 제거한다. 순환은 계속 빈 근거로 fail-closed하고, 긴 chain의 resolve 횟수
      상한 회귀와 전체 4,033행 EXAMPLE 전수 시간·then 동일성으로 검증한다.
- [ ] TA-106 **TODO(2026-08-04, 성능 우선 cold에서 격리) — handler 직접 효과 소유권 점검.**
      `R1160` handler 후보가 `cond=null`, `then=[]`로 생성되어 "handler 행은 직접 효과 then이
      비어 있을 수 없음" finding으로 rejected 됐다. 전체 실행은 닫지 않고 좌표 finding으로
      격리됐다. 성능 측정을 우선 완료한 뒤 parser subtree·원문·handler row 계약을 직접 대조해
      정답지 누락인지 잘못된 의무행인지 판정한다. 빈 성공, 특정 행/이름 예외는 금지한다.
- [ ] TA-107 **TODO(2026-08-04, 성능 우선 cold 로그 감사) — AST/source 좌표 동일성 게이트.**
      canonical corpus의 `zapamcom10080.c:c210_insert_zpay_spc`는 AST가 `[655,1064]`를 갖지만
      현재 source 정의는 640행에서 시작하고 다음 함수가 1056행에서 시작한다. 저장 RULE 좌표와
      source를 직접 대조하자 `R1046`은 source의 빈 줄을 가리켰다. 성능 cold는 중단하지 않되,
      이후 parser 산출과 source의 파일 hash/함수 이름·범위·본문 경계를 전수 검사해 producer
      좌표 드리프트인지 corpus 혼합인지 확정한다. analyzer의 임의 offset 보정이나 파일명 특화는
      금지하며, 불일치 corpus는 분석 전에 좌표와 파일을 명시해 fail-closed해야 한다.
- [x] TA-108 **완료(2026-08-04, 두 cold 역실험) — KV 관측 분리와 RULE batch 경계.**
      cache=false/concurrency=64 cold는 1,006 successful calls, input 6,034,517/output 339,365,
      API의 cached token 필드는 0으로 보고됐지만 사용자 확인상 서버 KV cache는 실제 동작한다.
      wall은 1,070.5초였다. RULE prompt 481콜을 required 행 수별로 직접 대조하면
      1~13행 364콜은 deadline 0인 반면 14행부터 실패가 생기고, 20행은 68콜 중 28콜(41.2%)이
      180초 deadline으로 끝났다. 총 36개 RULE deadline이 695행을 unavailable로 만들었다.
      KV cache 동작 여부와 API cached token 필드는 구분한다. 총 input token도 full prompt의
      논리 크기이지 KV reuse 양이 아니므로 cache hit 판정값으로 사용하지 않는다. 동일 system
      prefix 2,815자는 전역 warmup으로 유지하고 user prefix가 3자 뒤 갈리는 세 concern은 독립
      fan-out했다. RULE fixed-key schema는 13행 이하로 제한했다. 재측정 run
      `spec116-cold-20260804-140824`는 18분 6.6초, 1,107콜, input 6,398,830/output 378,439였다.
      RULE deadline은 36콜/695행에서 5콜/65행으로 줄어 정확도·격리 폭에는 유효했지만, 호출
      106개와 출력 39,074 token이 늘어 wall은 16.1초 악화됐다. 따라서 13행 상한은 품질 보호로
      유지하고 fan-out/batch를 잔여 속도 해법으로 주장하지 않는다.
- [x] TA-109 **완료(2026-08-04, 제한 실험으로 폐기) — 자유서술 의미 병합 반증.**
      최신 run의 성공 summary 406건에서 LLM `summary`는 67,184자, `search_text`는 146,590자라
      중복 생성은 실제 출력 지배항이다. 그러나 길이 상한과 자유서술 병합은 정확도를 보존하지
      못했다. hard max 240은 문장·식별자를 절단했고 prompt-only 상한은 6건 중 5건이 무시됐다.
      단일 간단 summary 20건은 b200의 수신자 거절 경로와 c000/c010의 상태→callee 매핑을
      생략했다. 자유 문자열 5-slot 스키마는 19건이 형식상 성공했지만 모델이 여러 필드를 한 글자로
      채웠고, 복잡한 1건은 1,500 token을 소진했다. 단일 상세 summary/search_text도 노드별로
      분기·상태 매핑을 무작위 생략했다. 따라서 summary를 만든 뒤 signature/RW/CALL/RETURN을
      문자열로 덧붙이는 materializer와 자유서술 두 필드 병합은 채택하지 않는다. 독립 5-field
      wire를 복원했고 표적 79 tests가 통과했다. 전체 회귀·prompt hash는 이 복원 뒤 미검증이다.
- [ ] TA-110 **진행 중(2026-08-04, 사용자 최종 의도 재확정) — 좌표 강제 canonical summary.**
      자유문장 세 벌을 생성하지 않는다. 코드가 확정한 활성 의미 후보(공개 입력·출력, 분기에서 쓰인
      변수/상수, 직접 callee·READ/WRITE·return, DDL/선언/외부 증거 연결)를 동적 schema의 required
      key로 만들고, 모델은 각 좌표를 `resolved(meaning+evidence refs)` 또는
      `needs_context(question+missing evidence)`로 반드시 채운다. 상태코드·리터럴 exact는 코드가
      보존하고 명시 mapping 없이는 의미를 생성하지 않는다. 공용 템플릿이 canonical `summary` 하나를
      조립하며 사람 표시·embedding/search·bottom-up에 동일하게 사용한다. 별도 LLM search_text,
      별도 callee 설명, summary→search_text 복사 alias, 추가 LLM 호출, 런타임 값/객체 추적,
      이름/언어/코퍼스 특화는 금지한다. 대표 20노드에서 직접 의미·근거·누락·token/latency를 판정한다.
- [x] TA-111 **완료(2026-08-04 15:47, 동시 세션 오염 수리) — 자유 search_text 하이재킹 제거.**
      다른 세션이 반증된 단일 자유 `search_text` 설계를 다시 적용해 wire에서 `summary`를 삭제하고,
      normalizer가 `search_text`를 `summary`에 복사하며 내부 모델이 두 필드 byte 동일성을 강제했다.
      15:37~15:43 파일 변경을 관측하고 안정된 뒤, 새 좌표 템플릿이 입증되기 전의 안전 기준인 독립
      `summary/search_text` 5-field closed wire로 Framework/DBMS schema·prompt·normalizer·consumer를
      일괄 복구했다. 한 필드가 다른 필드를 하이재킹하거나 빈값을 대신하지 않는 반증을 고정했고
      표적 52 tests가 통과했다. full suite와 prompt hash는 아직 미검증이다. 현재 graph의
      `apascrtu:c000_main_proc` R666도 권위 cond는 `not IS_LINK`인데 NL은 근거 없는 “협력사 B가 아닌
      경우”를 추가했다. 따라서 기존 RULE `nl`도 검증 없이 summary 재료로 복사하지 않는다.
- [x] TA-112 **완료(2026-08-04 16:12, 4노드 3회 제한 반증) — 조건 의미 라벨 프롬프트 한계 확정.**
      내부 evidence의 required-key schema는 b200 27키, c000 43키, c010 41키, get_card 3키를
      4/4 모두 누락 없이 생성했다. cache off, 동시 4콜에서 generic 최종 probe latency는
      6.3~16.2초, output 193~1,307 token으로 폭주가 없었다. 그러나 첫 probe는 c010의 동일
      납부방법 `"01"`을 은행/카드로 모순되게 해석했고, 특정 식별자 예를 준 두 번째 probe만
      안정됐다. 그 예를 추상 비교로 바꾼 세 번째 범용 probe는 b200의 서비스 코드 C/D에 다시
      유선/무선을 추측하고 한 callee를 `정보 없음`으로 채웠다. required key는 누락을 막지만
      source operand의 업무 뜻을 증명하지 못한다. 같은 prompt 조정은 종료하며 이 라벨을 trusted
      cond/nl로 저장하지 않는다. 증거는 `project/robo-data-analyzer/specs/131-cross-node-semantic-grounding/_artifacts/history-experiments/coordinate-template-20260804/run{,2,3}`.
- [x] TA-113 **구현·회귀·8행 실호출 완료(2026-08-04 20:55), cold 판정은 TA-121 — RULE 구조화 빈칸 채우기 복원.**
      required `R<anchor>` 아래에서 모델이 source-condition ref/극성, 고정형 effect 객체, `nl`을
      채우고 시스템은 선택된 구조를 canonical Python Rule 표기로만 직렬화한다. 결정론 코드는
      업무 의미를 생성·대체하지 않는다. Framework/DBMS 생산 경로를 같은 leaf로 통일했고 모든
      좌표·필드를 required+extra-forbid로 강제했다. 실제 cache-off 8행 호출은 7.56초,
      input 2,160/output 1,005 tokens, required 8/8·condition path 8/8·invalid effect 0이었다.
- [ ] TA-114 **진행 중(2026-08-04 16:41, 사용자 재검토) — 코드값 무해석·의미 부족 표면화.**
      상태코드·숫자·문자열 리터럴은 모델이 업무 뜻을 추론하지 않고 exact 원문으로 저장·표시한다.
      source enum/상수 선언, DDL 설명, 사람이 준 glossary처럼 뜻이 명시된 경우에만 의미를 병기한다.
      테이블·컬럼·변수도 이름 모양으로 확장하지 않고 DDL/source/현재 문맥/callee/사람 glossary의
      실제 근거로 분석한다. 분석 가능한데 GPU만 놓치면 입력 구조·좌표 연결을 한 번 개선하고,
      주 에이전트도 동일 입력으로 판정할 수 없으면 필요한 증거를 `needs_context`로 남긴다. 물리명은
      항상 보존하되 의미 부족을 조용히 물리명으로 대체하지 않는다. 함수 logical_name도 분석 목적상
      유지하지만 근거 좌표가 필요하며 상태값 의미를 섞지 않는다.
- [x] TA-115 **완료(2026-08-04) — summary 이행 중간 상태 정상화.**
      생산자·normalizer·consumer·graph/search/callee·prompt/test를 canonical `summary` 하나로 맞췄고
      옛 `search_text` 복사 alias를 만들지 않았다. 전체 854 tests 통과(skipped 5).
- [x] TA-116 **완료(2026-08-04), cold 판정은 TA-121 — 결정론 Rule 하이재킹 전수 제거.**
      NL-only schema와 raw-source materializer/compiler의 최종 Rule 생산 경로를 제거했다. 모델이 고른
      condition ref/극성과 effect를 canonical 표기로 직렬화할 뿐 의미를 고치지 않는다. exact branch
      path가 아닌 source-grounded 조건도 거절하는 반증과 invalid effect 한 행 격리 회귀를 추가했다.
- [ ] TA-117 **진행 중(2026-08-04 17:03) — 한 사실 한 번 전달·단일 canonical summary.**
      callee input의 `search_text + return_expressions + 기타 반복 facts`를 감사한다. callee 분석 시
      purpose/핵심 처리/데이터 영향/위임/호출자 output·return/미확정을 required 좌표로 채워 canonical
      summary 하나를 만들고 caller에는 그것만 전달한다. current SOURCE에 이미 있는 call site나 exact
      facts를 반복하지 않으며, 범위 밖 선언·상수 설명·직접 DDL·외부 계약만 보충한다. 모델은 좌표별
      의미 문자열 또는 null만 내고 exact/lines/evidence/status/question은 복창하지 않는다. 같은 exact
      return occurrence는 dedupe한다. summary/rules/insight를 한 평탄 fixed-key call로 합칠 후보를
      20노드에서 직접 의미·누락·token·latency로 한 번만 검증하고 실패 시 반복하지 않는다.
- [x] TA-118 **완료(2026-08-04 20:20) — 최신 cold 시간·품질 지배항 실측.**
      cache-off/concurrency64/test 완주는 16분 41.8초, input 4,655,545/output 553,002 tokens였다.
      Rule 8행 호출은 p50 91.1초, 45/318 실패, 최대 180초였고 전체 4,100 의무 행 중 trusted는
      1,031(25.1%)뿐이었다. 3,069 finding은 missing과 정확히 같았으며 Python 자유 문자열의 문법·
      wrapper·반복·deadline이 지배했다. cached_tokens=0은 서버 KV miss로 해석하지 않았다.
- [ ] TA-119 **최우선 — 정확도 보존형 최소 wire 20노드 실험.**
      SOURCE 한 번, 범위 밖 증거만 보충, branch별 required `cond/then/nl`, 필요한 의미 좌표의 짧은
      값/null, purpose/logical_name/stereotype/questions를 한 평탄 schema로 생성한다. `$defs`를 공유하고
      source/line/exact/evidence/status 복창과 별도 search_text/callee/return 서술을 제거한다. 기존 대비
      모든 Rule/Example·의미 직접 대조와 token/p50/p95/max가 함께 통과할 때만 제품에 적용한다.
- [ ] TA-120 **최우선 — 생산 경로 일괄 이행과 deterministic hijack 제거.**
      TA-119 계약으로 Framework/DBMS의 model/schema/prompt/normalizer/consumer/graph/search/bottom-up/
      evidence/tests를 한 번에 맞춘다. NL-only Rule과 raw-source materializer의 최종 cond/then 생산을
      제거하되 좌표/문법/allowlist/직접 사실 검사와 accepted Rule→EXAMPLE 파생은 유지한다. model-output
      실패는 행 finding으로 남기고 전체 run을 닫지 않는다.
- [ ] TA-121 **최우선 — 11~15분 cold 반복과 종료선.**
      전체 회귀 후 cache=false, concurrency=64, Neo4j `test`로 cold를 실행한다. 목표 미달이면 새 log의
      다음 시간 지배항만 수정한다. 동일 입력에서 주 에이전트는 가능한데 GPU만 실패한 경우 구조/prompt
      보정은 한 번만 하며 이후에는 모델 한계로 기록한다. 정확도 하락·특화·상태 의미 추론·조용한
      drop으로 얻은 시간은 무효다.
- [x] TA-122 **완료(2026-08-04, cold 중 원칙 감사) — semantic exporter 옛 필드 제거.**
      제품 graph/search/callee가 canonical `summary` 하나를 쓰는데 직접 의미검증 exporter만
      `n.search_text`를 별도 속성·검증 축으로 읽던 중복 소비자를 제거했다. exporter source에 옛 이름이
      다시 들어오지 않는 정적 회귀를 추가했다.
- [x] TA-123 **반증 완료(2026-08-04 20:56~21:09, 의도 중단) — verbose Rule effect 객체는 폐기.**
      cache-off/concurrency64/test에서 13분 시점 Rule record는 parsed 468, deadline batch 34였고
      deadline 한 배치의 모든 좌표를 표면화해 unavailable 행이 이미 256개였다. 이는 완료해도 정확도
      비협상 조건을 회복할 수 없어 실행을 중단했다(15분 초과가 중단 사유는 아님). 성공 8행의 output
      중앙값도 1,294 tokens로 과거 자유 leaf보다 커서 `op/target/value/from_source` 객체 반복이 동시
      생성 경합을 악화했다. 좌표·강제 구조는 유지하되 condition/effect를 compact evidence ref로
      선택하고, source에 없는 추가 의미만 구조 객체로 내는 wire로 교체한다.
- [x] TA-124 **구현·회귀·실호출 완료(2026-08-04 21:20), cold 판정은 TA-121 — compact Rule ref wire.**
      condition은 `C/!C`, AST 확정 CALL/RETURN/직접 대입은 중복 제거된 `E` ref로 한 번만 정의하고
      모델 `cond/then`은 ref만 선택하도록 스키마로 강제했다. 함수명 관습을 대입으로 바꾸는 객체 경로는
      제거하고 `extra`는 권위 table op/명시 오류만 허용한다. 모델이 ref 대신 객체를 선택한 1차 union
      스모크(842 tokens)는 폐기했다. 최종 8행 cache-off 실호출은 4.8초, input 2,265/output 627 tokens,
      cond 8/8·direct effect 8/8·extra 0·invalid 0이었다. 최초 객체 wire보다 output 37.6% 감소했다.
      summary만 bottom-up을 해제한다는 계약이 실제 슬롯 줄에서도 지켜지도록 공용 64 capacity는 그대로
      두고 summary wake priority만 -1, 나머지 step7은 0으로 분리했다. 전체 857 tests 통과(skipped 5),
      prompt hash 28/28, diff-check 통과.
- [x] TA-125 **콜드 실측 완료(2026-08-04 21:20~21:38) — compact wire 1차 전체 결과.**
      cache=false/concurrency=64/Neo4j `test`에서 1,068.7초(17분 48.7초), 1,265 successful calls,
      input 4,981,780/output 482,351 tokens였다. Rule 의무 4,100행 중 3,879행이 trusted graph에
      적재됐고 221행은 rejected 118/unverified 23/unavailable 80으로 명시 격리됐다. unavailable
      80행은 모두 180초 deadline 10배치이며 6개 대형 함수(추정 source token 7,791~12,330)에만
      발생했다. 16분 허용선을 108.7초 넘었으므로 성능·정확도 모두 미완료로 판정한다.
- [x] TA-126 **수정·회귀 완료(2026-08-04 21:48), 다음 cold 판정 대기 — 대형 Rule 꼬리와 handler 오판 제거.**
      언어·함수명 특화 없이 추정 source token 7,500 이상인 routine만 Rule 배치를 8행→4행으로
      줄이고, summary 최우선은 유지한 채 대기 Rule은 payload가 큰 순서(LPT)로 슬롯을 받게 했다.
      동시성 64는 변경하지 않았다. handler marker는 실행 효과가 아니라 예외 블록 진입 좌표일 수
      있는데 검증기가 무조건 then을 요구해 유효한 67행을 거짓 탈락시켰다. 67건 모두 그 사유 하나,
      then=[]·nl 존재였으므로 handler만 비어 있는 then을 허용하고 entry/exit 계약은 유지했다.
      중복 direct effect ref도 순서 보존 dedupe했다. 전체 861 tests 통과(skipped 5), prompt 28/28,
      diff-check 통과. 다음 cache-off cold에서 deadline·wall·4,100행 결과를 다시 판정한다.
- [x] TA-127 **완료(2026-08-04) — 범용 Agent 스키마의 특정 상태값 예시 제거.**
      `agents/contracts.py`의 범용 값 원문 설명이 특정 고객 코드값 `RC_NRM`을 예시로 사용해 모델 입력을
      편향할 수 있다. 현재 cold와 실행 코드의 일치를 지키기 위해 런 종료 뒤 `SUCCESS_CODE` 같은 범용
      표기로 교체하고, active code의 고객 코드값·이름 패턴 특화 grep 0과 전체 회귀를 확인한다.
- [x] TA-128 **완료(2026-08-04) — cache sweep 삭제 실패 표면화.**
      `llm/response_cache.py::sweep()`가 개별 cache 파일 `unlink()`의 `OSError`를 조용히 건너뛴다.
      cache는 보조물이지만 조용한 실패는 금지이므로 실패 건수·대표 오류를 집계해 기존 sweep 로그에
      함께 남기고 정상 분석은 계속한다. cache-off 성능 비교와 무관하며 현재 cold 종료 뒤 수정한다.
- [x] TA-129 **완료(2026-08-04) — 검색 selection mode의 조용한 legacy 폴백 제거.**
      `search/ranking/retriever.py`는 mode가 정확히 `global_anchor`가 아니면 오타·미지원 값도 모두
      `_retrieve_legacy`로 보낸다. 실제 rollback 계약·사용처를 확인한 뒤 유효값을 명시 검증하고,
      사용되지 않는 옛 경로라면 테스트·설정·코드를 함께 제거한다. 추정으로 삭제하지 않는다.
- [x] TA-130 **콜드 실측 완료(2026-08-04 21:48~22:03) — 16분 벽시계 통과, 정확도 미완료.**
      response cache false, concurrency 64, Neo4j `test`의 run
      `20260804_124842_1f692aeb80b1`은 **914.1초(15분 14.1초)**, successful usage
      1,314콜, input 5,121,410/output 493,241 tokens로 완주했다. 분기 의무 4,100행은
      trusted RULE 4,008행 + 격리 좌표 92행으로 뺄셈 0이다. 92행은 rejected 51좌표,
      unverified 25좌표, unavailable 16좌표이며 unavailable은 모두
      `zordmfax00001.c:b410_svc_proc`의 8행 배치 2건이 180초 deadline에 걸린 결과다.
      EXAMPLE 3,092개가 accepted RULE에서 파생됐다. 속도는 사용자 허용 16분을 통과했지만
      모든 행 정확도 계약은 아직 미완료다.
- [x] TA-131 **실측 반증·폐기(2026-08-04 22:33) — 실제 Rule 요청 크기 기반 사전 분할.**
      `ctx.token_count` 7,500 경계는 그래프의 줄번호 포함 본문(약 7.8K)과 실행 원문 추정
      (약 7.3K)이 달라 b410을 놓쳤다. 이 source proxy를 삭제하고 YAML runner와 배치 계획이
      같은 renderer를 공유해 **실제 user prompt가 13,000자를 넘는 8행 후보만 4행으로 사전
      분할**했다. 내용 실패 재시도는 추가하지 않았다. 특정 `RC_NRM` 예시는 `SUCCESS_CODE`로
      일반화하고, cache sweep unlink 실패 수·첫 오류를 경고하며, 검색 mode는
      `global_anchor|legacy` 외 값을 즉시 거부한다. 전체 **864 tests OK(skipped=5)**,
      prompt hash 28/28, diff-check가 통과했다. 그러나 cache-off cold
      `20260804_131625_e409df61ffcc`에서 1,041.4초(17분 21.4초), deadline 8행이 남았다.
      12,128자 8행도 deadline이었고 12,428자 4행은 169.2초가 걸려 입력 문자 경계가 출력
      폭주를 설명하지 못했다. 측정 없는 12K 하향 반복은 하지 않고 TA-135의 출력 구조 경계로 교체한다.
- [x] TA-132 **구현·표적 회귀 완료(2026-08-04 22:16), 현재 cold 판정 대기 — finding 전역 좌표.**
      기존 Rule finding의 `target_id=R<line>`은 서로 다른 함수의 같은 절대/상대 행과 충돌해
      prompt·분기표·그래프를 직접 조인할 수 없었다. 모든 Rule finding을 그래프 Rule과 같은
      `<code_id>::R-<line>`으로 기록하고 빈 scope를 거부한다. 표적 31 tests가 통과했다.
- [x] TA-133 **완료(2026-08-04 22:44) — Example Mapping 조용한 누락 제거.**
      `example_mapping_builder._collect_rules()`는 문서상 잘못된 transport를 즉시 거부한다고
      명시하면서 non-dict Rule을 `continue`하고, derived example도 non-dict/빈 transport를
      조용히 건너뛴다. 내부 검증 후 transport이므로 잘못된 형식은 명시 오류로 표면화하고
      정상·오류·빈 예시 계약을 회귀했다. non-object Rule/Example/Question과 빈 Question/Example은
      내부 transport 불변식 오류로 즉시 표면화한다. 모델 후보 오류는 기존대로 finding 격리 후 계속한다.
- [x] TA-134 **완료(2026-08-04 22:44) — CodeNode logical_name dead field 제거.**
      Framework와 DBMS 모두 step7에서 함수 `logical_name=None`을 확정하지만 graph/search/component
      입력에는 옛 code logical_name 소비가 남아 있다. 테이블·컬럼·모듈 logical_name과 혼동하지
      말고, 함수 필드가 실제 다른 생산 경로를 갖는지 전수 확인한 뒤 dead면 schema·writer·reader·
      prompt 주석을 함께 제거한다. 전수 검색 결과 생산 경로는 없고 두 step7 경로가 항상 None만
      대입했다. CodeNode schema/writer/search corpus/component 입력에서 제거하고 재분석 시 옛 graph
      속성도 REMOVE한다. 테이블·컬럼·모듈 logical_name은 유지한다.
- [x] TA-135 **구현·전체 회귀 완료(2026-08-04 22:44), cold 재측정 대기 — 출력 구조 기반 Rule microbatch.**
      두 cold의 prompt/usage 1,314~1,316콜을 직접 대조했다. RULE output token과 latency 상관은
      0.96이고, 8행은 평균 97.27초/최대 180.63초였지만 6행은 평균 62.73초/최대 100.86초였다.
      따라서 언어·함수명·값을 해석하지 않고 기본 batch를 8→6행으로 낮췄다. 또한 forced
      `cond`/`then` 배열의 결정론적 `when`+`direct` 참조가 경계를 넘는 후보는 최대 안전 prefix로
      분할한다. 4행·72참조가 169.19초였던 outlier도 같은 범용 기준이 잡는다. 입력 문자
      proxy와 특정 함수 예외는 삭제했다. 전체 **867 tests OK(skipped=5)**, prompt hash 28/28,
      diff-check 통과. deadline 0·16분 이내·의미 정확도는 다음 cache-off cold 전에는 모른다.
- [x] TA-136 **완료(2026-08-04 23:10) — semantic exporter의 폐기 함수 logical_name 제거.**
      생산 CodeNode 계약에서는 제거했지만 `scripts/export_semantic_review_packets.py`가 graph에서
      함수 `logical_name`을 다시 읽고 검토 필드로 만들었다. exporter query/packet field에서 제거해
      current summary-only 계약과 맞췄고 전수 검색으로 scripts 잔재 0을 확인했다.
- [x] TA-137 **완료(2026-08-04 23:10) — output-reference guard의 실제 evidence 연결.**
      `BranchInventory.table_rows()`에는 `when`/`direct`가 없고 이는 `rule_evidence()`가 batch별로
      파생한다. TA-135 테스트의 가짜 inventory만 그 키를 넣어 64-reference 분할이 제품에서는 0으로
      계산되는 결함을 찾았다. planner가 batch별 `rule_evidence()`의 실제 `when`+`direct`를 세도록
      연결했다. 제품처럼 table row에 파생 키가 없는 heavy inventory 반대 테스트로 5행 50참조가
      4+1행으로 갈리는 것을 고정했다. 최종 경계는 아래 TA-139 실측에 따라 48이다.
- [x] TA-138 **실측 반증·폐기(2026-08-04 23:36) — Rule leaf `extra`를 `then`에 통합.**
      확정 계약은 모델 출력 `cond/then/nl`이고 나머지는 코드 파생인데 현재 wire는 매 행
      `extra:[]`를 강제하고 있다. 진행 중 실측 1,767행에서 non-empty extra는 14개(0.79%:
      raise_error 4/update 8/insert 2)뿐이다. 무조건 삭제해 의미를 잃지 말고, 이 14개가 authoritative
      3차 cold 3,961 trusted행에서 non-empty extra는 27개(raise_error 9/update 14/insert 4)였고,
      그중 26개는 권위 table op/명시 오류, 1개는 source의 compound mutation이었다. 의미를 삭제하지
      않고 `then` 배열의 고정 객체 union으로 옮겨 모델 출력 키를 `cond/then/nl`로 단일화했다.
      빈 `extra:[]` 4,100회와 별도 key/schema를 제거하면서 27개 의미는 같은 Python Rule로 정규화된다.
      5행 새 schema 1,950 bytes는 옛 4행 schema 1,984 bytes보다 작았다. 그러나 아래 TA-140의
      전체 cold에서 문자열 E ref와 객체 union이 약한 모델의 선택 경계를 무너뜨려 정확도와 출력량을
      모두 악화시켰다. `then=E ref만`, `extra=희소 객체`의 분리 계약으로 복구한다.
- [x] TA-139 **6행 cold와 5행 고정 후보 모두 반증(2026-08-04 23:36).**
      cache=false/concurrency=64/Neo4j `test` run `20260804_134508_290efc8217aa`는
      1,095.2초(18분 15.2초), 1,403 successful calls, input 5,249,615/output 498,195였다.
      Rule 의무 4,100 = trusted 3,961 + finding 139로 좌표 손실 0이나 Rule 4배치/24행과 summary
      1건이 deadline이라 채택하지 않는다. 행 수별 성공 latency는 5행 avg/p95/max
      59.23/73.06/96.28초(deadline 0), 6행 85.53/126.23/174.73초(deadline 4)다. 기본을 5행으로
      낮추고 실제 response refs>48만 최대 안전 prefix로 분할했다. 전체 **867 tests OK(skipped=5)**,
      prompt hash 28/28, diff-check가 통과했지만 다음 cold가 22분 5.4초였으므로 5행 고정은 폐기한다.
- [x] TA-140 **cold 반증·원인 특정·수정(2026-08-04 23:36) — 5행 고정과 `then` union 회귀.**
      cache=false/concurrency=64/Neo4j `test` run `20260804_141354_47d11b1be864`는 외부 벽시계
      1,325.4초(22분 5.4초), pipeline 1,308.3초, successful usage 1,494콜,
      input 5,388,922/output 600,889였다. Rule은 988시도/927성공, trusted 1,591 + finding 2,509 =
      의무 4,100으로 좌표 손실은 0이나 rejected 1,857/unavailable 261/unverified 391이다. 거절 사유는
      direct return 누락 1,025, 잘못된 union 객체의 `invalid_effect` 693 등이었다. 빈 `extra:[]`를
      아끼려 ref와 객체를 같은 `then` 배열에 합친 최적화가 모델로 하여금 call/return ref 대신
      임의 select/raise_error 객체를 생성하게 한 직접 원인이다. `then=E ref`, `extra=희소 객체`로
      복구하고, 5행 고정 대신 기본 8행+실제 `when/direct` 참조 48 초과만 최대 안전 prefix로 분할한다.
      같은 4,100행을 현재 로그에서 replay하면 Rule 계획은 988→747콜이다. 특정 언어·함수·값 예외는 없다.
      전체 **867 tests OK(skipped=5)**, prompt hash 28/28, diff-check가 통과했다. wall≤16분과
      trusted 품질 복구는 다음 cache-off cold 전에는 모른다.
- [ ] TA-141 **미검증 — 대형 context 축약의 의미 보존.**
      현재 C/framework cold에서 `[CONTEXT_SUMMARY]` 사용은 0건이고, 진행 중인 2026-08-05 cold에서도
      최대 25,679 input-token 호출까지 축약 없이 들어갔다. 이 장치는 전체 prompt 크기를 감시하는
      범용 context compactor가 아니다. 중첩 DBMS만 자식이 있고
      `token_count>batch_max_tokens(기본 1,500)`일 때 자식 원문을
      bottom-up child summary로 치환한다. 누락 summary fail-loud와 좌표 보존 테스트는 있지만 원문 대비
      의미 보존 직접 대조는 없다. 외부 증거는 6,000자 예산에서 chunk를 통째-or-skip하며 이번 run의
      oversize skip은 0건이다. DBMS 중첩 표본에서 원문/compact 결과를 직접 의미 대조하기 전에는
      정확도 열화가 없다고 주장하지 않는다.
- [x] TA-142 **cold 반증·폐기(2026-08-05 00:33) — ref 배열의 폐쇄 CSV wire.**
      run `20260804_144504_260ab2bbc506`은 1,117.8초(18분 37.8초), successful 1,315콜,
      input 5,130,697/output 499,800이었다. Rule은 정확히 747콜, input 2,155,189/output 435,051,
      trusted 4,016 + finding 84 = 의무 4,100, unavailable 0으로 정확도는 복구됐지만 16분에 157.8초
      미달했다. 747개 Rule raw JSON은 960,327자이고 같은 의미의 minified JSON은 576,885자로
      syntactic whitespace가 39.9%였다. 공식 SGLang에는 서버 launch compact flag가 있지만 서버 설정
      변경 금지이므로 사용하지 않는다. prompt compact 지시 제한 호출은 112줄을 그대로 생성해 강제가
      아니므로 폐기했다. 의미 필드를 삭제하지 않고 `cond`/`then` ref 배열만 closed-regex CSV 문자열로
      바꾼다. 실제 4개 제한 호출에서 schema 파싱과 cond/then exact는 전 행 통과했고, 최대 표본은
      1,272→548 output token이었다. positional tuple은 희소 extra 1건을 잃어 폐기하고 named
      `cond/then/extra/nl` 객체를 유지했다. 그러나 cache=false/concurrency=64/Neo4j `test` full cold
      `20260804_151420_52e4849e7026`은 1,090.6초(18분 10.6초), 1,314콜,
      input 5,166,408/output 422,446이었다. Rule output은 435,051→355,640(-18.3%)로 줄었지만
      trusted Rule은 4,016→3,962(-54), finding은 84→138로 악화했고 16분도 넘었다. 토큰과 정확도를
      맞바꾼 결과이므로 CSV wire를 폐기하고 `cond`/`then` 폐쇄 배열 계약으로 즉시 복구했다.
- [x] TA-143 **cold 반증·폐기(2026-08-05 01:01) — Rule sibling의 prefix-family 인접 스케줄.**
      server info 실측은 radix cache enabled, `schedule_policy=fcfs`, max running 64, TP4였다.
      직전 747 Rule 기록에서 다중 배치 함수 144개의 sibling 완료 span은 median 201.5콜,
      mean 248.1콜, max 665콜이고 완전 인접은 1/144뿐이었다. 배치별 `rule_evidence` 크기를 LPT
      priority에 섞어 같은 함수 prefix를 흩뜨린 것이 코드상 원인이다. 요청 내용·수·동시성은 그대로
      두고 target 공통 Rule 입력 크기만 priority로 사용한다. 동일 priority FIFO가 한 함수의 batch를
      인접 제출해 radix LRU locality를 높인다. 다음 cache-off cold에서 wall과 trusted≥4,016을 함께
      판정했다. cache=false/concurrency=64/Neo4j `test` run
      `20260804_154237_9ec4b55e17b1`은 1,090.8초(18분 10.8초), 1,314콜,
      input 5,136,801/output 496,163이었다. Rule은 747콜, input 2,162,964/output 433,722,
      trusted 4,006 + finding 94 = 의무 4,100, unavailable 0이다. 직전 폐쇄 배열 기준보다
      27.0초 빨랐지만 trusted가 4,016→4,006으로 10개 줄어 정확도와 맞바꿨다. 순서만 바꿔도
      표본이 달라지는 서버 실행 변동을 cache 효과로 오인할 수 있으므로 prefix-family priority를
      즉시 제거하고 배치 evidence 크기 LPT로 복구했다.
- [ ] TA-144 **발견·원인 특정 — purpose-only summary를 canonical summary로 오인하는 의미 열화.**
      `summary_contract.md`는 분기·상태코드·CALL/RW/RETURN을 쓰지 말고 코드가 별도 템플릿으로
      렌더링한다고 지시한다. 그러나 실제 생산은 `ctx.summary = model.summary`, bottom-up 전달은
      `_format_callee_summaries`/`batch_prompt_values`가 그 문자열 하나만 복사하고, embedding·BM25도
      `n.summary` 하나만 사용한다. 별도 구조 renderer는 전수 검색 결과 없다. 따라서 중첩 DBMS의
      child→parent와 검색에서 목적 한 문장만 남아 핵심 처리·데이터 영향·하위 위임·반환 역할이
      열화될 수 있다. 현재 C cold는 `[CONTEXT_SUMMARY]` 0건이라 성능 판정과 분리한다. DBMS 표본의
      원문/현 summary/상위 결과를 직접 대조하고, 중복 없이 필요한 구조 사실을 canonical summary에
      보존하는 범용 계약으로 수리한 뒤 semantic 대조 전에는 완료 처리하지 않는다.
