# Feature Specification: Residual Pattern Linking Agent

**Feature Branch**: `007-residual-pattern-linking`

**Created**: 2026-06-15

**Status**: Backfilled (reverse-engineered)

**Input**: Existing feature — pipeline step4 stage3 (`link_residual_patterns_step` / `residual_pattern_loop`). Framework 전용. stage1 결정론 R/W 링킹이 못 푼 테이블 접근 케이스(`unresolved_cases`)를, 케이스마다 LLM 을 부르지 않고 잔여 샘플에서 **공통 패턴**을 발견해 코드가 일괄 적용하는 bounded agent loop. 정규식 검증을 통과한 결과만 READS/WRITES 엣지가 된다.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 잔여 미해결의 공통 빈틈을 패턴으로 메운다 (Priority: P1)

stage1(정규식 grep) 이 호출 지점을 매칭은 했지만 테이블을 못 찾아 `unresolved_cases` 로 남긴 잔여가 있다. 이는 보통 기존 정규식이 못 잡는 한 종류의 wrapper/매크로/표기 빈틈에서 무더기로 생긴다. stage3 는 잔여 샘플의 공통점을 찾아 새 정규식 한두 개로 다수 케이스를 한꺼번에 해소한다.

**Why this priority**: 이 step 의 존재 이유. 잔여 대부분은 소수의 공통 패턴에서 나오므로, 패턴 1개 발견이 케이스 수십 건을 푼다 — 케이스별 에이전트 호출보다 훨씬 적은 비용으로 가장 큰 회복.

**Independent Test**: 알려진 wrapper 빈틈을 가진 코드베이스에 stage1 을 돌려 `unresolved_cases` 를 만든 뒤 stage3 만 실행 → 발견된 패턴으로 잔여가 줄고 신규 READS/WRITES 엣지가 추가됨을 확인.

**Acceptance Scenarios**:

1. **Given** `function_id` 가 있는 잔여 케이스 다수, **When** 에이전트가 검증 통과 공통 패턴을 찾음, **Then** 해당 케이스가 해소되고 순-신규 R/W 엣지만 `reads_writes_edges` 에 추가, 남은 잔여는 `unresolved_cases` 로 환원.
2. **Given** 라운드가 잔여를 줄임, **When** 케이스 집합이 더는 변하지 않음, **Then** "진전 없음"으로 루프 종료하고 남은 잔여를 정직하게 미해결로 기록.

### User Story 2 - 검증 안 된 추측은 절대 그래프에 안 들어간다 (Priority: P1)

에이전트가 내놓은 정규식은 실제 소스에 적용해 작동을 입증해야만 채택된다. 환각·과적합 정규식이 가짜 엣지를 만들면 지식 그래프 신뢰가 무너지므로, 코드 검증(`verify_table_patterns`)을 통과하지 못한 패턴은 버린다.

**Why this priority**: 데이터 정합성 보호. 잘못된 엣지 1건이 다운스트림 분석 전체를 오염시키므로, 회복량보다 정확성이 우선.

**Independent Test**: 매칭은 되나 entity 가 SQL_KEY 통째이거나 op_token 이 변수명인 (틀린) 정규식을 강제로 반환시키고 → 검증 `ok=False` 로 채택 거부 + 루프 종료를 확인.

**Acceptance Scenarios**:

1. **Given** 에이전트가 패턴을 반환, **When** `verify_table_patterns` 가 `ok=False` (매치 0 / 엣지 0 / op_token 인식률 미달), **Then** 그 패턴 채택 안 함, 루프 종료, 잔여 유지.
2. **Given** 에이전트가 공통 패턴을 못 찾음, **When** `submit_residual` 가 빈 `table_access_patterns` + `no_pattern_reason` 으로 옴, **Then** 라운드 중단하고 사유를 로그에 남김 (조용한 실패 금지).

### User Story 3 - 정규식으로 못 고칠 케이스는 즉시 격리한다 (Priority: P2)

caller(`function_id`) 가 없는 케이스(AST 공백)는 정규식으로 풀 수 없다. 이런 케이스는 루프에 넣지 않고 처음부터 미해결로 분리해 비용·라운드를 낭비하지 않는다.

**Why this priority**: 비용 절감 + 정직한 회계. 풀 수 없는 케이스를 에이전트에 던지면 토큰만 쓰고 못 푼다.

**Independent Test**: `function_id` 없는 케이스만 입력 → 에이전트 호출 0회, 전부 `unresolved_cases` 로 반환됨을 확인.

**Acceptance Scenarios**:

1. **Given** `function_id` 없는 잔여, **When** stage3 실행, **Then** 해당 케이스는 fixable 에서 제외되어 곧장 최종 잔여로 격리.

---

### Edge Cases

- `unresolved_cases` 가 비었거나 `project/run/cost_budget` 누락 → step skip, 빈 결과 반환(아무것도 덮지 않음).
- `scan_result` 파싱 실패 → 잔여 루프 건너뛰고 전체를 미해결로 유지(가짜 해소 0), 경고 로그.
- fixable 케이스가 하나도 없음 → 에이전트 호출 없이 truly_unresolved 만 반환.
- 검증 통과 패턴인데 재적용 결과 케이스 집합 변화 0(해소·신규 동률 포함) → 진전 없음으로 종료. 진전은 카운트가 아니라 `case_id` 집합 차이로 측정.
- 라운드 상한(3) 도달까지 잔여가 남음 → 남은 잔여를 미해결로 정직 환원.
- 에이전트 호출 한도(15) 초과 시도 → call_limit 미들웨어가 차단.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST stage1 이 남긴 `unresolved_cases` 중 `function_id` 가 있는 케이스만 패턴 루프 대상(fixable)으로 삼고, `function_id` 없는 케이스는 즉시 미해결로 격리해야 한다.
- **FR-002**: System MUST 케이스마다 에이전트를 호출하지 않고, 잔여 샘플(`ROBO_PIPELINE_RESIDUAL_SAMPLE_SIZE`, 기본 30건)에서 **공통 패턴**을 발견하게 한 뒤 코드가 잔여 파일에 일괄 재적용해야 한다.
- **FR-003**: System MUST 루프를 최대 `ROBO_PIPELINE_RESIDUAL_MAX_ROUNDS`(기본 3) 라운드로 제한하고, 라운드당 에이전트 호출을 `ROBO_AGENT_RESIDUAL_MAX_CALLS`(기본 15)로 제한해야 한다.
- **FR-004**: 에이전트는 재탐색·skill 매칭을 다시 하지 않고, 이미 파악된 scan 맥락(matched_skill, codebase_notes, framework_functions, ddl_tables, confirmed_patterns)을 user 메시지로 받아 그 빈틈을 메우는 새 정규식만 찾아야 한다.
- **FR-005**: 에이전트는 결과를 `submit_residual` 도구를 정확히 한 번 호출해 `ResidualPatternResult`(table_access_patterns / ddl_transform / no_pattern_reason / notes) 양식으로 제출해야 한다.
- **FR-006**: System MUST 제출된 정규식을 채택 전 `verify_table_patterns` 로 실제 소스에 검증해야 하며(잔여 파일 한정), `ok` 판정(매치≥1 · 엣지≥1 · op_token 인식률 ≥ `ROBO_AGENT_SCAN_REGEX_MIN_OP_RATIO`)을 통과한 패턴만 적용해야 한다. 검증 실패 시 그 패턴 폐기 + 루프 종료.
- **FR-007**: System MUST 재적용 결과에서 **순-신규** 엣지만(잔여 파일 기준선 엣지를 뺀 차집합) `reads_writes_edges` 로 내보내, stage1 이 이미 만든 엣지의 중복을 막아야 한다.
- **FR-008**: 진전 여부 MUST 케이스 `case_id` 집합 변화(resolved/new)로 판정해야 하며, 변화가 0이면 종료한다(단순 카운트 비교 금지).
- **FR-009**: 공통 패턴을 못 찾으면 System MUST `no_pattern_reason` 을 로그에 남기고 잔여를 그대로 미해결로 둬야 한다 — 빈 결과를 "해소"로 위장하는 **조용한 실패 금지**(잔여 큐가 비워진 사고의 재발 방지).
- **FR-010**: 끝내 못 푼 잔여는 System MUST `unresolved_cases` 키로 환원해야 한다. 이 키는 reducer 없는 평키이므로 stage1 이 쓴 raw 값을 stage3 최종 잔여가 덮어쓴다(다운스트림 artifact 가 이 최종값을 덤프).
- **FR-011**: 에이전트 도구는 코드 열람(filesystem) + `verify_table_regex` 검증만이며, skill·graph 도구는 잔여 모드에 노출하지 않는다.
- **FR-012**: op 매핑은 LLM 이 표준 SQL 4종(SELECT/INSERT/UPDATE/DELETE)만 결정하고, READS/WRITES 매핑은 코드가 결정해야 한다(SELECT=READS, 나머지=WRITES — 환각 차단). 참고: `MERGE` 는 LLM 이 고를 수 있는 op 가 아니며, DBMS 경로(spec 006)에서만 결정론적으로 발생해 AFFECTS_TABLE op enum(spec 009)에 나타난다 — 4-vs-5 모순 아님.

### Key Entities *(include if feature involves data)*

- **unresolved_cases (잔여 큐)**: stage1 이 매칭은 했으나 테이블 미해소로 남긴 케이스 목록. 각 케이스: `file`·`line`·`call_text`·`function_id`·`case_id`·`attempted_patterns`. reducer 없는 평키 — 입력(stage1 raw)이자 출력(stage3 최종 잔여)으로 같은 키를 덮어쓴다.
- **residual round (잔여 라운드)**: 1회 = 샘플 패턴 발견 → 검증 → 잔여 파일 재적용 → 잔여·엣지 갱신. 종료 조건 = 잔여 0 / 패턴 못 찾음 / 검증 실패 / 진전 없음 / 라운드 상한.
- **submit_residual / ResidualPatternResult**: 에이전트 출력 양식. `table_access_patterns`(검증 통과 새 정규식, 명명 그룹 `<entity>`+`<op_token>`), `ddl_transform`(entity→DDL 변환 교정), `no_pattern_reason`, `notes`.
- **verified patterns → READS/WRITES**: 검증 통과 정규식을 `RegexTableLinker` 가 적용해 만든 `ReadsWrites` 엣지(function_id → table_fqn, relation READS/WRITES, dml_type). 기준선 차집합으로 순-신규만 추출 후 Neo4j `upsert_reads_writes`.
- **ResidualOutcome**: 루프 결과 — `edges`(순-신규 R/W), `residual`(최종 미해결), `rounds_used`, `patterns_found`.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: stage1 잔여 중 공통 패턴이 존재하는 케이스 집합이 stage3 후 줄어들고, 줄어든 만큼의 검증된 READS/WRITES 엣지가 그래프에 추가된다.
- **SC-002**: 그래프에 추가된 잔여 엣지는 100% `verify_table_patterns.ok=True` 통과 패턴에서만 나온다(검증 미통과 패턴 유래 엣지 0건).
- **SC-003**: 한 번의 stage3 실행에서 에이전트 호출 횟수는 잔여 케이스 수가 아니라 라운드 수에 비례하며, 라운드 ≤ 3·라운드당 호출 ≤ 15 를 넘지 않는다.
- **SC-004**: `function_id` 없는 케이스는 에이전트 호출 없이 전량 최종 잔여로 격리된다.
- **SC-005**: 다운스트림 artifact 의 `unresolved_cases` 는 stage1 raw 가 아닌 stage3 최종 잔여를 반영한다(잔여 회계 정직성).
- **SC-006**: 공통 패턴 부재 시 잔여가 "빈 채로 해소된 것처럼" 사라지지 않고 사유와 함께 미해결로 남는다.

## Assumptions

- Framework(매크로/wrapper 기반 DB 접근) 코드베이스 전용. DBMS(SQL 직접) 경로는 별도 stage(`link_dbms_tables_step`)가 담당하며 본 step 과 무관.
- stage1(`regex_table_linker`)이 먼저 실행되어 `unresolved_cases`·`scan_result`(table_access_patterns·ddl_transform·matched_skill 등)를 state 에 채워 둔 상태를 전제로 한다.
- 잔여 대부분은 소수의 공통 빈틈에서 발생한다는 가정 — 그래서 per-case 추적 대신 패턴 일괄 적용이 유효하다.
- 검증·재적용은 `RegexTableLinker`/`verify_table_patterns` 를 잔여 파일로 한정해 재사용한다(전체 재스캔 회피, scan stage 와 동일 검증 로직 공유).
- `project`·`run`·`cost_budget`·`neo_client` 가 step config 로 주입된다. 누락 시 step 은 안전하게 skip 한다.
- 시스템 프롬프트는 `prompts/agents/residual_pattern_system.md` 단일 소스이며, KV 캐시 적중을 위해 user 메시지는 라운드 불변 영역을 앞, 변동 영역을 뒤에 배치한다.