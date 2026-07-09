# spec 039 핸드오프 — code_rules_examples 응답구조 최소화

> 새 세션이 이 문서 + [[principles]]·[[speckit]]·[[manual]]·[[todo]] 정독하면 100% 이어서 진행 가능.
> 작성 2026-07-01. 현재진실 = 코드 + spec 039 문서 + 이 파일.

---

## 0. 한 줄

analyzer 코드분석 3분할 중 **분할③ `code_rules_examples`** 의 LLM 응답구조를 최소화한다: LLM은 **의미만**(rule statement + 그 하위 given/when/then) 출력하고, 구조(룰 흐름 flow_id/NEXT/BRANCH)·접착제 id(rule_id)·중복(description)은 응답·그래프에서 뺀다. examples를 rules **하위로 중첩**한다.

---

## 1. 왜 (배경·동기 — 이걸 알아야 판단이 됨)

**큰 목표**(이 spec의 상위 맥락): 레거시 코드 분석결과(analyzer 그래프) → 요구사항(UserStory) 도출 → architect Proposal에 연결. 그 준비로 분할③ 출력을 정리한다.

**분할③이 왜 문제였나**:
- 037에서 **출력 폭주(무한응답) 실사고 4·5차** 기록 — 자유키 `dict[str,str]`·`list[KeyValueItem]` 중첩이 xgrammar value-start 실패로 공백 무한 폭주. 그래서 given/then을 자연어 단일 문자열로 이미 평탄화했음.
- `rule_id`(R1/R2) 접착제: LLM이 rules[]와 examples[] 두 평면 배열을 id로 상호참조 → 없는 id 참조 오류 + 토큰 낭비 + 헤맴.
- `flow_id`(1/1-1/1-1-1): LLM이 계층 번호를 매기느라 헤맴. 순서는 배열순서로 충분, 분기 의미는 statement 텍스트에 이미 있음.
- `description`: given/when/then·statement와 중복 요약.

**핵심 결정과 근거**(사용자와 장시간 확정):
- **유지**: `statement` · `coupled_domains`(rule) · 중첩 `examples{given,when,then,affected_tables}` · `questions`.
  - `affected_tables`(→AFFECTS_TABLE 엣지): **라이브 로직에 깊이 쓰임** — rule_classifier가 op(INSERT/UPDATE/DELETE)→Aggregate 생성/변경 분류, commands/events/aggregates phase가 Command·Event·Aggregate 이름·전제조건 도출에 사용. 다수 테이블일 때 규칙↔테이블 대응 필요. → **절대 유지.**
  - `questions`(→QUESTION 노드): PRD 생성이 "Open Decisions" 섹션으로 **읽기만**(LLM 아님, 표시만). 저빈도("0이 최고"). flow_id와 달리 LLM 부담 없음 → **유지.**
  - `coupled_domains`: bpm_context_builder가 cross-BC 힌트로 LLM 컨텍스트에 넣음(라이브) → **유지.**
- **제거**: `rule_id` · `flow_id` · `description` + **룰 레벨** `NEXT`/`BRANCH` 엣지.
- **★두 전략(framework/dbms) 다 적용.** framework=per-node 3분할(`three_split_llm_fanout`), dbms=배치(`batch_analyze`). 프롬프트도 `framework/code_rules_examples.yaml` + `dbms/code_rules_examples_batch.yaml` 양쪽.
- **★NEXT 공유심볼 함정**: `NEXT` rel 타입은 **구문(statement) NEXT**(dbms 실행흐름, `StatementNext`)·**BpmTask.NEXT**(architect)도 쓴다. 룰→룰 NEXT/BRANCH만 제거, **구문 NEXT는 절대 유지.**

**최종 응답 스키마**:
```
RulesExamplesOutput { rules: [RuleItem], questions: [QuestionItem] }
RuleItem   { statement, coupled_domains, examples: [ExampleItem] }
ExampleItem{ given, when, then, affected_tables: [{table, op}] }
```

---

## 2. 현재 상태 — ✅ 완료·검증 (analyzer 생산자만)

**변경 파일(robo-data-analyzer, 전부 ROOT 체크아웃 `d:\work\robo\project\robo-data-analyzer`):**
- `data_models/code_analysis_output.py` — RuleItem(rule_id/flow_id 제거, examples 중첩), ExampleItem(rule_id/description 제거), RulesExamplesOutput(top-level examples 제거), BatchRulesItem 동일.
- `data_models/graph_edges.py` — HasRule(local_rule_id/flow_id 제거, coupled_domains만). **Next·Branch dataclass 삭제.** StatementNext 도크스트링 흔적 제거.
- `data_models/graph_nodes.py` — Example(description 제거). Rule 도크스트링 흔적 제거.
- `shared/example_mapping/builder.py` — flow_id/NEXT/BRANCH 도출 로직 삭제, 중첩 examples 소비.
- `shared/example_mapping/graph_writer.py` — upsert_next/branch import·호출 삭제.
- `shared/neo4j/edges/rule_flow.py` — **파일 삭제**(룰 NEXT/BRANCH writer).
- `shared/neo4j/edges/has_rule.py` — MERGE에서 local_rule_id 키·flow_id SET 제거(coupled_domains만).
- `shared/neo4j/edges/affects_table.py` — ★후속 근본수정: TABLE MATCH를 마지막세그먼트(스키마접두사 무시) 비교로 통일(dbms `RWIS.표` ↔ bare TABLE 링크). framework no-op.
- `shared/neo4j/nodes/example.py` — description SET 제거.
- `shared/neo4j/nodes/rule.py`·`edges/next.py` — 도크스트링 흔적 제거(코드 무변경, next.py=구문 NEXT 유지).
- `shared/neo4j/rel_types.py` — **BRANCH 상수 삭제**, NEXT 주석 정리(구문만).
- `shared/workflow/state.py` — next_edges/branch_edges 필드 + Next/Branch import 삭제.
- `shared/workflow/artifact_writer.py` — next_edges/branch_edges 키 삭제.
- `analysis_flow/step6_analyze_code/analyze_code_step.py` — emit dict next/branch 키 삭제, HAS_RULE 스트림 props(local_rule_id/flow_id) 삭제, 룰 NEXT/BRANCH 스트림 emit 블록 삭제, EXAMPLE props(description→given/when/then).
- `analysis_flow/step6_analyze_code/batch_analyze.py` — emit next/branch 삭제, fan-in `extra["examples"]` 삭제(중첩).
- `analysis_flow/step6_analyze_code/three_split_llm_fanout.py` — fan-in `extra["examples"]` 삭제(중첩).
- `analysis_flow/step6_analyze_code/rw_table_filter.py` — `rules_out.rules[].examples[].affected_tables` 중첩 순회.
- `analysis_flow/step6_analyze_code/test_rw_table_filter.py` — 중첩 구조로 테스트 갱신.
- `prompts/framework/code_rules_examples.yaml` + `prompts/dbms/code_rules_examples_batch.yaml` — 스키마·필드지침·flow_id예시·footer 정합, 중첩. **system(_common.yaml) byte-identical 유지(§8).**

**★데드 완전제거 확인(흔적 0, 주석조차)**: rule_flow.py 파일 · Next/Branch dataclass · state next/branch · rel_types.BRANCH · has_rule flow_id/local_rule_id · example description · 유령 `[]`·도크스트링 흔적 5곳(graph_nodes Rule·rel_types·state·rule.py·next.py) 전부 제거.

**검증(실행)**: 전체 analyzer `compileall` exit 0 · 제거심볼 전수grep 잔재 0 · import 체인 · builder 실동작(중첩→그래프) · 단위테스트 8/8. **단 full 재분석은 미실행.**

---

## 3. 남은 작업 (정확히 — 이게 새 세션의 할 일)

### T8-12. architect 소비자 정합 (robo-architect, 별 레포 — robo-spec 체계, speckit 아님)
그래프 계약이 바뀜(HAS_RULE.flow_id/local_rule_id·룰 NEXT/BRANCH·EXAMPLE.description 소멸). **비크래시(Cypher `coalesce`로 graceful NULL)지만 §4 데드정리 필요.** 파일:
- `api/features/ingestion/hybrid/contracts.py` — `RuleDTO`의 `local_id·flow_id·guard_rule_id·branch_from·next_rule_local_ids·branch_rule_local_ids`(214~225) 제거. `ExampleDTO.description`(178) 제거.
- `api/features/ingestion/hybrid/bpm_context_builder.py` — 룰 NEXT/BRANCH에서 guard/branch **도출 서브쿼리**(287-288 `guard_derived`/`branch_derived`)·`hr.flow_id`·`hr.local_rule_id`(297-301) 읽기 제거. LLM 컨텍스트의 guard_rule_id/branch_from 라인(398-401) 제거. **statement·affected_tables·coupled_domains·GWT 컨텍스트는 유지.**
- `api/features/ingestion/hybrid/code_to_rules/dbms_rule_linearizer.py` — `hr.flow_id` 정렬키·guard/branch(flow 유래) 제거. **순서=구문 line(AST), 분기=AST ELSIF/ELSE로 이미 도출(`_BRANCH_LABELS`)** 하므로 그건 유지.
- `api/features/ingestion/hybrid/code_to_rules/rule_extractor.py` — flow_id/guard/branch/description SELECT·매핑 제거.
- `api/features/ingestion/hybrid/event_storming_bridge/{rule_classifier,decomposer,persistence}.py` — **미배선(데드)** 이나 flow/branch 참조 정합(§4).
- **frontend**(robo-data-frontend) — 룰 레벨 NEXT/BRANCH 렌더 있으면 제거(그래프 스토어·엣지타입). 없으면 해당없음(확인 대상).
→ 착수 전 §5 전수grep: `flow_id`·`guard_rule_id`·`branch_from`·`next_rule_local_ids`·`branch_rule_local_ids`·룰 `NEXT`/`BRANCH`·`ExampleDTO.description`.

### T13-15. 재분석·검증 — ✅ 완료 (2026-07-01, frentis)
**두 전략 다 재분석·검증 완료. 무회귀 확인.**
- **dbms**(RWIS, batch경로, →test DB): RULE 244·EXAMPLE 360·중첩 360·flow_id 0·룰NEXT/BRANCH 0·desc 0·구문NEXT 유지·599콜 무오류. **AFFECTS_TABLE 0→180**(아래 fix로 링크됨).
- **framework**(zapamcom, 3분할경로, antlr파싱→분석 →neo4j DB): RULE 71·EXAMPLE 131·중첩 131·flow_id 0(옛81)·룰NEXT/BRANCH 0(옛38)·desc 0·132콜 무오류. AFFECTS_TABLE 53(LLM 런변동, fix 무영향).
- **★AFFECTS_TABLE 접두사버그 근본수정**(`shared/neo4j/edges/affects_table.py`): dbms LLM이 `RWIS.테이블`로 emit하나 TABLE 노드는 bare → upsert MATCH를 `last(split(x,'.'))` 양쪽 마지막세그먼트 비교로 통일. framework(점 없음) no-op 검증(옛=새 매칭쌍 23=23). dbms만 접두사 벗겨 링크.
- 재분석 LLM = **frentis(qwen SGLang)**. gpt-5.4-mini(reasoning none)는 "아티팩트"(임베딩) 단계만 — 재분석 아님.

--- (아래는 히스토리: 원래 미실행 상태의 턴키 절차. 재실행 필요 시 참고) ---
새 재분석 절차(참고):

**① 준비 — ★LLM 분담 주의(중요)**
- **analyzer 재분석 = GPU 서버 frentis(qwen SGLang) 모델로.** 즉 `.env` `ROBO_LLM_CONFIG=qwen36_sglang_local` **그대로 유지**(변경 X). 재분석은 이 config로 돈다.
- **gpt-5.4-mini(reasoning off)는 "아티팩트" 생성 단계만.** 아티팩트가 임베딩을 쓰는데 frentis 임베딩이 한국어 변별을 못 하므로 그 단계만 OpenAI(`gpt54_mini_openai`, reasoning_effort none). = [[gpu-embedding-matching]]. **재분석 LLM과 혼동 금지.**
- framework 데이터: `D:\다운로드\apascrt00010t02_20260406` → `ROBO_DATA_DIR/source/apascrt00010t02_20260406/` 로 세팅(+ 필요 ddl). dbms = **현재 data**(그대로).
- **antlr-parser**(형제 레포): 테스트파일의 **파싱 부분만** 실행. **업로드 테스트 금지.** antlr 다운이면 `data/analysis` 기존 AST 재사용(결정론).

**② 백업** (재분석 전 필수)
- 현 그래프 백업: `neo4j`(framework), `test`(dbms) DB를 덤프(또는 apoc.export/스크립트). 전후 비교용.

**③ 재분석**
- `PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe cli_scripts/analyze_data_dir.py apascrt00010t02_20260406` (framework). dbms는 해당 subdir 지정. (strategy=확장자 자동: .sql/.pkb=dbms, 그 외=framework.)

**④ 검증** (§6 그래프 원문 대조 — Cypher)
- `MATCH ()-[r:HAS_RULE]->() WHERE r.flow_id IS NOT NULL RETURN count(r)` = **0**
- `MATCH (:RULE)-[r:NEXT|BRANCH]->(:RULE) RETURN count(r)` = **0** (룰 흐름 소멸)
- `MATCH (e:EXAMPLE) WHERE e.description IS NOT NULL RETURN count(e)` = **0**
- `AFFECTS_TABLE`·`QUESTION`·구문 NEXT(구문노드끼리)·RULE·EXAMPLE 개수 = **전후 동등**(무회귀)
- EXAMPLE이 소속 RULE 하위에 HAS_EXAMPLE로 붙음 / RULE.statement·coupled_domains 정상.
- 오류 로그 0(`logs/`). 두 전략(framework·dbms) 다 확인.
- (선택) architect 하이브리드 인제스천 재실행 → UserStory/Aggregate/Command/Event 무에러(단 architect T8-12 정리 후가 이상적).

---

## 4. 영향도 (blast radius) 한눈

```
[analyzer 생산자 ✅완료]  LLM응답 → builder → Neo4j(RULE/EXAMPLE/HAS_RULE/AFFECTS_TABLE/HAS_QUESTION)
     계약변화: HAS_RULE.flow_id·룰NEXT/BRANCH·EXAMPLE.description 소멸 / RULE·EXAMPLE·AFFECTS_TABLE·QUESTION·구문NEXT 불변
        ↓ (그래프 = 서비스간 contract)
[architect 소비자 ⏳남음]  contracts.RuleDTO/ExampleDTO → bpm_context_builder·rule_extractor·dbms_rule_linearizer
     → 하이브리드 인제스천(UserStory/Aggregate/Command/Event 생성). 비크래시 graceful, 데드정리 대상.
        ↓
[frontend ⏳확인]  룰흐름 렌더(있으면 제거)
```

---

## 5. 핵심 사실·함정 (놓치면 사고)
- **analyzer 이중 체크아웃**: speckit·정식작업은 **루트** `d:\work\robo\project\robo-data-analyzer`. 서브모듈 `robo-architect\robo-analyzer\robo-data-analyzer`엔 `.claude` 없음 → 거기 만지면 speckit surface 안 됨. (본 작업 전부 루트에서 함.)
- **KV캐시(§8)**: 프롬프트는 `user_template_specific`만 수정, `system`(`_common.yaml`)은 byte-identical. 3분할 공통이라 한 글자만 달라도 캐시 미스.
- **NEXT/BRANCH 구분**: 룰→룰만 제거. **구문 NEXT(StatementNext, dbms)·BpmTask.NEXT(architect) 유지.** rel_types.NEXT 상수는 구문용으로 살아있음.
- **재분석 LLM**: 반드시 gpt-5.4-mini reasoning off (frentis 임베딩이 한국어 변별 못 함 = 별개 미해결이슈 [[gpu-embedding-matching]], 재분석 임베딩은 openai로).
- **원칙 상시**: 데드 흔적0(주석조차)·두 전략 다·SDD·추측금지 전수조사·회귀금지·심볼변경=전수grep.

---

## 6. SDD 문서 위치
`d:\work\robo\project\robo-data-analyzer\specs\039-rules-examples-response-slim\` : spec.md · plan.md · tasks.md(Phase1 ✅체크) · 이 HANDOFF.md.
