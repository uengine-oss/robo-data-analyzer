# Feature Specification: Function Analysis — Bottom-up DAG + 3-split / batch + Example Mapping

**Feature Branch**: `009-function-analysis`

**Created**: 2026-06-15

**Status**: Backfilled (reverse-engineered)

**Input**: 기존 코드(`analysis_flow/step6_analyze_function/*` + `shared/example_mapping/*`)에서 역설계. 파이프라인 step6 — 호출 그래프를 leaf→root 로 타고 올라가며 함수마다 의미(요약·역할·표시명)·비즈니스 규칙·테이블 인사이트를 LLM 으로 추출하고, 결정론 빌더가 그 결과를 Example Mapping 그래프(Rule/Example/Question + 6 엣지)로 변환해 Neo4j 에 즉시 적재한다.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 함수별 의미 요약·역할·표시명 (Priority: P1)

분석가가 레거시 코드를 올리면, 시스템은 호출 그래프상 **자식(피호출) 함수를 먼저** 분석하고 그 요약을 부모 분석의 입력으로 합류시켜, 함수마다 `[핵심] 한 줄 + 내용` 형식의 요약, 역할 라벨(stereotype), 형제와 구별되는 도메인 표시명(logical_name)을 Neo4j Function 노드에 채운다.

**Why this priority**: 모든 후속 산출물(규칙·인사이트·모듈 요약·부품)의 1차 근거. 이것만 있어도 "각 함수가 무엇을 하는가" 라는 핵심 가치가 성립한다.

**Independent Test**: 작은 호출 트리를 입력해, leaf 함수가 root 보다 먼저 분석되고 모든 Function 노드에 summary/stereotype/logical_name 이 채워지는지, 부모 요약이 자식 요약을 맥락으로 반영하는지로 검증.

**Acceptance Scenarios**:

1. **Given** callee 가 없는 leaf 함수, **When** 분석을 실행하면, **Then** 그 함수가 Round 1 에서 분석되어 summary(`[핵심]` 마커 포함)·stereotype·logical_name 이 채워진다.
2. **Given** 이미 분석된 callee 를 가진 caller, **When** 그 caller 가 분석되면, **Then** callee 의 요약이 입력 `callee_summaries` 로 주입되어 부모 요약에 흐름이 반영된다.
3. **Given** 함수 분석 1건 완료, **When** 결과가 나오면, **Then** 라운드·step 끝까지 묶지 않고 **즉시** Neo4j upsert + NDJSON node/relationship 이벤트로 emit 된다.

---

### User Story 2 - 비즈니스 규칙 Example Mapping (Priority: P1)

각 함수에서 추출한 비즈니스 규칙(rules)·given/when/then 시나리오(examples)·모호점(questions)을 결정론 빌더가 Rule/Example/Question 노드와 HAS_RULE/HAS_EXAMPLE/HAS_QUESTION/NEXT/BRANCH/AFFECTS_TABLE 엣지로 변환해 지식 그래프를 구성한다.

**Why this priority**: 레거시 코드의 "암묵적 업무 규칙"을 사람이 읽을 수 있는 Example Mapping 으로 표면화하는 것이 이 기능의 본질적 가치.

**Independent Test**: 분기가 있는 함수 하나를 분석해 Rule 노드가 statement 별로 생기고, 같은 statement 가 함수 간 단일 Rule 로 공유되며, flow_id 계층에서 NEXT/BRANCH 가 도출되는지로 검증.

**Acceptance Scenarios**:

1. **Given** 두 함수가 의미상 동일한 규칙 statement 를 가짐, **When** EM 빌더가 돈다, **Then** `rule_hash` 가 같아 **1 개의 Rule 노드**로 공유되고 함수별 정보(local_rule_id/flow_id/coupled_domains)는 HAS_RULE 엣지 속성에 담긴다.
2. **Given** flow_id 가 `1`,`2`(메인) 와 `1-1`(분기)인 규칙들, **When** 빌더가 흐름 엣지를 도출한다, **Then** 메인 체인은 NEXT, `-` 계층 분기는 부모 flow 의 Rule 에서 BRANCH 엣지로 연결된다.
3. **Given** 존재하지 않는 rule_id 를 참조하는 example, **When** 빌더가 돈다, **Then** 그 example 은 조용히 드롭된다(고아 엣지 방지).

---

### User Story 3 - 테이블 인사이트 (Priority: P2)

함수가 읽거나 쓰는 테이블이 있으면, 시스템은 그 테이블별 역할(READ-only / WRITE-then-READ 등)과 본문에 등장한 컬럼의 비즈니스 의미를 추출해 Function 에 부착하고, 시나리오의 영향 테이블은 AFFECTS_TABLE 엣지로 연결한다.

**Why this priority**: 데이터 흐름 이해를 강화하지만, R/W 테이블이 없는 함수에는 해당 분할이 아예 호출되지 않으므로 P1 핵심 요약보다 부차적.

**Independent Test**: reads/writes 가 있는 함수와 없는 함수를 각각 분석해, 전자만 table_insights 분할이 호출되고 컬럼 의미가 채워지는지로 검증.

**Acceptance Scenarios**:

1. **Given** reads 또는 writes 가 비어있지 않은 함수, **When** 분석되면, **Then** 테이블 인사이트 분할이 호출되어 테이블 역할 + 본문 등장 컬럼의 의미가 채워진다.
2. **Given** reads/writes 가 모두 빈 함수, **When** 분석되면, **Then** 인사이트 분할은 **호출되지 않고**(skip) 토큰을 낭비하지 않는다.

---

### Edge Cases

- **순환 호출(cycle)**: Tarjan SCC 로 식별된 cycle 내부 함수는 callee 요약 없이 분석되고(`is_in_cycle=True`), SCC 전체가 한 Round 에 묶인다. 위상 정렬에서 누락된 그룹이 생기면(cycle 식별 누락 의심) 조용히 빠뜨리지 않고 경고 로그로 표면화한다.
- **부분 실패**: 한 함수의 #2(rules) 또는 #3(insights) 한 조각이 실패해도 함수 전체를 폐기하지 않고 빈 결과로 진행하며 `partial` 마크를 남긴다. summary(#1) 실패만 함수를 None 으로 폐기한다.
- **예산 초과**: `BudgetExceededError` 는 회로 차단기로 즉시 전파되며, in-flight LLM 호출 task 는 cancel 해 orphan 호출을 막는다.
- **일시정지/중단**: 일시정지 시 in-flight task 는 즉시 cancel 되어 `remaining` 에 남고 재개 시 재투입된다. Neo4j MERGE 라 재실행이 안전(멱등).
- **DBMS 배치 누락 노드**: 배치 응답에서 node_key 매칭이 안 된 노드는 1 회 미니 재요청으로 보강하고, 그래도 누락이면 그래프 노드는 유지(요약만 빈 채) + 경고.
- **DDL/괄호 노드(DECLARE/TRY/TRIGGER_BLOCK)**: 분석 제외하되 그래프엔 유지 — 컨텍스트에서 투명 처리(자식이 가장 가까운 non-skip 조상으로 bubble).
- **외부 callee**: 분석 영역 밖 함수(프레임워크/런타임)는 의존성으로 세지 않아 위상 정렬이 막히지 않는다.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: 시스템은 함수 분석 전, 모듈마다 LLM 1 회로 헤더 주석·어노테이션·시그니처에서 변경이력·작성자 노이즈를 제거한 짧은 **모듈 의도(intent, 1~3 문장)** 를 산출해야 한다(Framework 전용 — 해당 strategy 에 `module_pre_analysis.yaml` 이 있을 때만, DBMS 는 자연 skip). intent 는 함수 분석의 공통 context prefix 로만 쓰이고 영구 그래프 속성으로 저장하지 않는다.
- **FR-002**: 시스템은 CALLS + PARENT_OF 엣지로 호출 그래프를 만들고 **Tarjan SCC(iterative)** 로 순환을 식별해야 한다. 크기 ≥2 또는 자기 호출 1-cycle 만 cycle 로 취급하며, cycle 내부 함수는 `is_in_cycle` 메타가 박힌다.
- **FR-003**: 시스템은 호출 그래프를 **bottom-up(leaf→root) Round** 로 위상 정렬(Kahn)해야 하며, cycle SCC 는 한 가상 그룹으로 묶어 같은 Round 에 배치하고 결과는 결정적(sorted) 순서여야 한다.
- **FR-004**: 같은 Round 의 함수들은 `ROBO_PIPELINE_MAX_CONCURRENCY`(기본 30) Semaphore 로 묶인 병렬 분석을 받아야 하며, `asyncio.as_completed` 로 **끝나는 순서대로** 즉시 영속화·emit 되어야 한다(라운드는 누적 묶음이 아니라 내부 동시성 제어 단위). Round 간은 순차다.
- **FR-005**: 각 Round 는 첫 함수의 #1 summary 만 단독 선호출(warmup)해 LLM 서버 prefix 캐시에 공통 지침을 올린 뒤 나머지 함수를 풀어야 하며, 첫 함수는 그 결과를 `precomputed_summary` 로 재사용해 #1 중복 호출을 막아야 한다.
- **FR-006**: Framework 경로의 함수 1 개 분석은 **3 분할 fan-out** 이어야 한다 — #1 `function_summary`(항상) → 그 다음 `gather(#2 function_rules_examples 항상, #3 function_table_insights 는 reads/writes 가 있을 때만)`. #1 을 먼저 await 해 자기 함수 입력값을 캐시에 올린 뒤 #2/#3 가 그 prefix 에 적중한다.
- **FR-007**: 3 분할의 공통 context(본문 + module_intent + codebase_notes + callee_summaries + reads/writes + 참조심볼 + 조상 골격)는 **바이트 단위로 동일한 prefix** 여야 하며(RadixAttention/KV 캐시 적중), #3 전용 입력(자식 range 의 규칙·질문)은 규칙 분할에만 append 된다.
- **FR-008**: DBMS strategy 는 per-node 3 분할 대신 **형제 배치 분석** 경로를 타야 한다 — skip-투명 유효 트리를 만들고, 같은 유효 부모 형제를 `ROBO_PIPELINE_BATCH_MAX_TOKENS`(기본 1500) 토큰버짓까지 greedy 병합해 배치당 1 LLM 콜로 분석하며, 응답은 줄범위 `node_key` 로 입력 노드와 1:1 매칭한다. 루트(유효부모 없음)는 각자 solo 배치다.
- **FR-009**: 부모 range 본문은 자식 구간을 raw 코드 대신 **자식 요약으로 치환한 compact body** 로 LLM 에 줘야 하며(자식이 먼저 분석돼 요약이 채워진 뒤 부모 합류), 조상 골격은 skeleton(자식 `....`)으로, 골격이 토큰 임계 초과 시 조상 one_liner 로 대체한다. Framework 평면 함수는 raw 본문 그대로다.
- **FR-010**: 모든 LLM 호출은 Pydantic `response_format` 으로 출력을 강제해 자유 텍스트 응답을 차단해야 한다. summary 는 `[핵심]` 마커가 없으면 validator 가 LLM 재시도를 유도하고, stereotype 은 ontology 허용 풀(15 개)로 검증해 미허용 값은 빈 값으로 강제한다.
- **FR-011**: 함수 분석 결과 1 건이 끝날 때마다 **즉시** `update_summaries`(Function 노드 upsert) + `write_example_mapping`(EM 노드·엣지) 를 Neo4j 에 적용하고, Function + EM 노드/엣지를 묶어 NDJSON node/relationship 이벤트로 emit 해야 한다(메모리 누적 0, 그래프 실시간 반영).
- **FR-012**: Example Mapping 빌더는 **결정론(LLM 0 회)** 이어야 한다 — 함수의 `extra["rules"/"examples"/"questions"]` 로부터 Rule(공유)·Example·Question 노드와 HAS_RULE/HAS_EXAMPLE/HAS_QUESTION/NEXT/BRANCH/AFFECTS_TABLE 엣지를 생성한다.
- **FR-013**: Rule 노드는 정규화된 statement 의 `rule_hash`(MD5[:12]) 로 정체성을 가져 같은 의미 문장은 함수 간 **단일 노드로 공유**해야 하고, 함수별 정보(local_rule_id/flow_id/coupled_domains)는 HAS_RULE 엣지 속성에 담긴다.
- **FR-014**: 빌더는 flow_id 계층으로 흐름 엣지를 도출해야 한다 — `-` 없는 flow_id 는 메인 체인으로 정렬해 NEXT, `-` 있는 flow_id 는 상위 flow 의 Rule 에서 BRANCH. Example 은 given/when/then 중 하나라도 있고 유효한 rule_id 를 참조할 때만 생성하며, 영향 테이블은 op enum(READ/INSERT/UPDATE/DELETE/MERGE) 별 AFFECTS_TABLE 엣지가 된다.
- **FR-015**: 함수 분석 결과(summary/rules/questions)는 `AnalysisContext` 에도 부착해 bottom-up 으로 상위(caller·부모)가 라이브 조회할 수 있어야 한다(자식 규칙은 부모 #3 분할 입력, 자식 요약은 부모 compact body 입력).

### Key Entities *(include if feature involves data)*

- **SummaryOutput** (#1): `summary`(2 섹션 `[핵심] 한 줄 + 빈 줄 + 내용`, `[핵심]` 마커 필수) + `stereotype`(허용 풀에서 선택) + `logical_name`(형제와 구별되는 도메인 한 줄).
- **TableInsightsOutput** (#2): `table_insights[]` — 각 항목은 테이블 fqn + 역할(role) + 본문 등장 컬럼의 의미(name/description/usage).
- **RulesExamplesOutput** (#3): `rules[]`(rule_id, statement `<조건>이면 <결과>`, flow_id, coupled_domains) + `examples[]` + `questions[]`(text, reason).
- **ExampleItem**: rule_id 참조 + description + **given/when/then 각각이 자연어 단일 문자열**(2026-05-30 schema-blowup 사고로 자유 키 dict / list[KeyValueItem] 폐기 후 단일 문자열로 통합) + affected_tables[](table, op enum).
- **Rule 노드**: `rule_hash`(정체성·공유 키) + `statement`. 함수 간 공유.
- **Example 노드**: `example_id`(`{function_id}::{rule_id}::{idx}`) + description/given/when/then.
- **Question 노드**: `question_id`(`{function_id}::Q{idx}`) + text + reason.
- **엣지 6 종**: HAS_RULE(Function→Rule, 속성 local_rule_id/flow_id/coupled_domains), HAS_EXAMPLE(Rule→Example), HAS_QUESTION(Function→Question), NEXT(메인 흐름 Rule→Rule), BRANCH(분기 Rule→Rule), AFFECTS_TABLE(Example→Table, 속성 op).
- **ExampleMappingResult**: 노드 3 종 + 엣지 6 종 누적 컨테이너(dataclass 의 list 필드가 단일 진실 — 누적은 필드 자동 extend).
- **BatchAnalysisOutput / NodeAnalysis** (DBMS): 배치 응답을 노드별 평탄 1 항목(summary+stereotype+logical_name+rules+examples+questions+table_insights)으로 합본, `node_key` 줄범위로 식별.
- **ModulePreAnalysisOutput**: `intent` — 임시 통신값(영구 속성 아님).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 분석 대상 모든(non-skip) 함수가 정확히 한 Round 에 배치되어 분석된다 — 위상 정렬 누락 0(누락 시 경고 로그로 표면화).
- **SC-002**: 호출 그래프에서 모든 callee 함수는 자신을 호출하는 caller 보다 먼저(같거나 더 이른 Round 에) 분석된다(cycle 내부 제외).
- **SC-003**: 같은 의미 statement 를 가진 규칙은 함수 수와 무관하게 단일 Rule 노드로 합쳐진다(rule_hash 기준 중복 0).
- **SC-004**: 한 함수 분석이 끝나면 step/round 종료를 기다리지 않고 그 함수의 Function 노드 + EM 노드/엣지가 Neo4j 와 NDJSON 스트림에 반영된다(함수 단위 즉시성).
- **SC-005**: reads/writes 가 없는 함수에는 테이블 인사이트 LLM 호출이 0 회 발생한다.
- **SC-006**: 한 조각(rules/insights) 실패가 함수 폐기로 이어지지 않으며, 같은 입력 재실행 시 그래프 결과가 멱등(Neo4j MERGE)이다.

## Assumptions

- 선행 step(파서·링커·context 빌드·테이블 샘플 fetch)이 `functions` / `analysis_contexts` / `calls_edges` / `parent_of_edges` / `tables` / `scan_result.codebase_notes` / `table_sample_data` 를 state 에 채워둔 상태로 진입한다.
- LLM 서버는 prefix(KV) 캐시를 지원해 바이트 동일 공통 prefix 의 재사용이 토큰을 절감한다(워밍·3 분할 설계의 전제).
- strategy 객체는 `name`("framework"/"dbms")과 `prompts_dir` 을 제공하며, 프롬프트 yaml 의 존재 여부가 Framework/DBMS 경로 분기와 module pre-analysis on/off 를 결정한다.
- given/when/then 은 사람용 narrative 단일 문자열이라는 합의가 유지된다(자유 키 dict 로의 회귀 = grammar 폭주 재발 위험).
- 동시성·배치·임계 파라미터는 환경변수(`ROBO_PIPELINE_MAX_CONCURRENCY`=30, `ROBO_PIPELINE_BATCH_MAX_TOKENS`=1500, `ROBO_PIPELINE_BIG_CONTEXT_THRESHOLD_TOKENS`=1000)로 조정 가능하다.
- Neo4j MERGE 의 정체성 키(rule_hash/example_id/question_id/function_id)가 중복 제거를 자동 처리하므로 state list 는 raw extend 로 둔다.
