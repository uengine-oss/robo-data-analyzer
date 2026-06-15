# Feature Specification: Deterministic Code↔Table & Call Linking

**Feature Branch**: `006-deterministic-linking`

**Created**: 2026-06-15

**Status**: Backfilled (reverse-engineered)

**Input**: User description: "pipeline step4 stages 1-2 + DBMS table/call steps — using ScanResult's verified regexes, grep the project to create READS/WRITES edges (code↔table); classify call_sites into internal/external CALLS (drop framework runtime funcs, handle wrappers); DBMS variants link DML ranges to DDL tables by word-boundary search and classify AST CALL nodes. LLM 0."

## User Scenarios & Testing *(mandatory)*

<!-- 우선순위 = 그래프 가치 순. 각 스토리는 독립 실행·검증 가능. -->

### User Story 1 - Framework 코드↔테이블 R/W 링킹 (regex grep) (Priority: P1)

분석가가 레거시 Framework 코드(C/Java/Python)를 지식그래프로 만들 때, step3 Scan 이 **검증을 통과시킨** 정규식(`table_access_patterns`)으로 프로젝트 소스를 grep 해 "어느 함수가 어느 DDL 테이블을 읽고/쓰는가"를 결정론적으로 엣지로 박는다. LLM 호출 0.

**Why this priority**: 코드↔데이터 리니지의 핵심. 이 한 스토리만 돌려도 READS/WRITES 그래프라는 즉시 가치가 나온다.

**Independent Test**: 검증된 패턴 + functions + DDL tables 를 입력하면, 정규식 매치마다 `ReadsWrites` 엣지가 생성되고 미해소 매치는 `unresolved_cases` 큐에 쌓이는지 단독 검증 가능.

**Acceptance Scenarios**:

1. **Given** 검증된 정규식과 매칭 가능한 소스, **When** stage1 grep 실행, **Then** (함수, 테이블, READS/WRITES) 엣지가 생성된다.
2. **Given** entity→DDL 테이블 lookup 실패 또는 caller 함수 미특정, **When** 매치 발생, **Then** 엣지 대신 `unresolved_cases` 항목으로 누적(stage3 잔여 루프가 소비)된다.
3. **Given** `table_access_patterns` 는 있으나 R/W 엣지 0건, **When** grep 종료, **Then** "정규식이 소스와 안 맞음" 경고로 조용한 실패를 표면화한다.

---

### User Story 2 - 호출 결정론 분류 → CALLS 엣지 (Framework & DBMS) (Priority: P1)

함수 간 호출 관계를 코드 목록 대조만으로 분류한다(LLM·Agent 0). Framework 변형(stage2 by_name)은 antlr 가 모은 `call_sites` 를, DBMS 변형(by_ast)은 antlr 의 `CALL` 노드를 출처로 쓴다. 결과는 양쪽 모두 동일한 `Calls` 엣지(scope internal/external).

**Why this priority**: 호출 그래프는 컴포넌트 군집화·영향분석의 기반. "미해결" 개념이 없어 항상 완결된다.

**Independent Test**: call_sites/CALL 노드 + functions 목록을 입력하면 internal/external 분류와 framework·stdlib·자기루프 드롭이 결정론적으로 재현되는지 단독 검증 가능.

**Acceptance Scenarios**:

1. **Given** 콜리 이름이 프로젝트 함수와 일치, **When** 분류, **Then** `CALLS{internal}` (콜리 = 정의 노드).
2. **Given** 콜리가 stdlib 심볼 또는 framework 함수 글로브 패턴 매치, **When** 분류, **Then** 드롭(엣지 없음).
3. **Given** 콜리가 호출 wrapper, **When** 라인 본문에서 인자 추출, **Then** 모듈 entry 매치면 internal, 아니면 인자 이름으로 external.
4. **Given** 그 외 실 함수명, **When** 분류, **Then** `CALLS{external}` (분석 범위 밖 — 호출 사실만 기록).

---

### User Story 3 - DBMS DML range → DDL 테이블 단어경계 매칭 (Priority: P2)

DBMS 는 SQL 이 코드에 그대로 있으므로, 각 노드의 **자기 줄**(자기 범위 − 직속 자식 줄범위) 본문에서 DDL 테이블 이름을 단어경계 문자열검색해 `ReadsWrites` 엣지를 만든다. AST 파싱·LLM 0.

**Why this priority**: DBMS 계열의 R/W 리니지. Framework regex grep 의 DBMS 대응물로, DDL 테이블이 이미 추출됐을 때만 의미가 있어 P2.

**Independent Test**: functions + DDL tables + parent_of_edges 입력 시, 첫 DML 키워드로 READS/WRITES 가 정해지고 누락 0·중복 0(테이블마다 자기 줄이 속한 노드에서 정확히 1번)인지 단독 검증 가능.

**Acceptance Scenarios**:

1. **Given** 노드 자기 줄에 `INSERT INTO TBL`, **When** 스캔, **Then** TBL 은 WRITES, 첫 DML 키워드(INSERT)로 dml_type 결정.
2. **Given** `MERGE INTO …USING(SELECT…)` 처럼 자식을 가진 부모, **When** 부모는 자기 줄만, 자식 서브쿼리는 자식 노드가 따로 스캔, **Then** 각 테이블이 정확히 1번 — 누락·중복 없음.
3. **Given** `EXECUTE IMMEDIATE` 안 리터럴 SQL, **When** 주석만 제거하고 문자열은 보존해 스캔, **Then** 리터럴 내 테이블도 포착된다.

---

### Edge Cases

- 정규식 컴파일 실패: 해당 패턴만 `log_warning` 후 건너뜀(조용한 실패 금지).
- 절대/상대 경로 차이로 caller 함수 미특정: suffix 매칭 시도, 실패 시 unresolved.
- op_token 미인식: 보수적으로 READS(틀린 정규식은 verify 단계에서 탈락).
- call_text / caller 누락(AST 불완전): 분류 불가 → 드롭하되 카운트로 표면화.
- wrapper 매치했으나 본문에서 인자 추출 실패: `wrapper_unresolved` 카운트.
- DBMS 빌트인(`DBMS_`/`UTL_`/`SYS_`/`ORA_`/`OWA_`/`STANDARD` prefix) 콜리: 드롭.
- caller == callee 자기루프(internal): 드롭.
- 같은 (함수, 테이블, relation) / (caller, callee) 중복: Neo4j MERGE 가 자동 dedup.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: 시스템은 step3 Scan 이 검증 통과시킨 `table_access_patterns` 정규식으로 프로젝트 소스를 grep 해야 한다 (파일당 1회 read, `finditer`). LLM 0회.
- **FR-002**: 정규식 매치에서 entity·op_token 을 추출하고, `ddl_transform`(as_is / camel_to_snake_upper / snake_to_camel / fallback prefix) 으로 entity 를 DDL 테이블 fqn 으로 해소해야 한다.
- **FR-003**: 시스템은 op_token → 표준 SQL op(SELECT/INSERT/UPDATE/DELETE/MERGE) 매핑으로 relation 을 정한다 — SELECT=READS, 그 외=WRITES. token 미인식 시 READS.
- **FR-004**: 테이블 lookup 실패 또는 caller 함수 미특정 매치는 엣지가 아니라 `unresolved_cases` 큐(case_id·file·line·call_text·attempted_patterns 포함)에 넣어 step4 stage3 잔여 패턴 루프로 넘긴다.
- **FR-005**: 패턴이 있는데 R/W 엣지 0건이면 "정규식이 소스와 안 맞음" 경고로 표면화해야 한다.
- **FR-006**: 검증(`verify_table_patterns`)은 링킹과 **동일 엔진**(`RegexTableLinker`)을 한 번 돌려 판정한다 — 검증된 정규식 == 링킹이 실제 작동하는 정규식(drift 차단). ok = 매치≥1 · 엣지≥1 · op_token 인식률 ≥ 임계.
- **FR-007**: Framework 호출 분류는 `call_sites` 의 call_text 를 (a) 프로젝트 함수 → internal(먼저 검사, 동명 stdlib 가림 방지) (b) stdlib 심볼 → 드롭 (c) framework_functions 글로브 패턴 → 드롭 (d) 호출 wrapper → 인자 추출 후 internal/external (e) 그 외 → external 로 분류한다. LLM 0회.
- **FR-008**: 동명 함수 다수면 호출자와 같은 모듈 후보를 우선해 internal 콜리를 정한다(C `static` 파일 스코프).
- **FR-009**: wrapper 는 `callee_from`(first_arg_string / first_arg_identifier) 별 정규식으로 라인 본문에서 인자를 추출하고, 인자가 모듈 entry/형제와 매치되면 internal, 아니면 인자 이름 그대로 external.
- **FR-010**: DBMS 호출 분류는 antlr `CALL` 노드만 대상으로(자기 타입 — 자손 오염 X), 빌트인 prefix 콜리는 드롭, 콜리 단순명이 정의 노드(루트)와 일치하면 internal(대상=정의 노드), 그 외는 external `:FUNCTION:EXTERNAL` stub(스키마=호출 수식어 또는 호출자 자기 스키마).
- **FR-011**: DBMS·Framework 호출은 caller=CALL 을 감싼 프로시저 루트(parent_id 체인 최상위)로 잡고, caller==callee 자기루프는 드롭한다.
- **FR-012**: DBMS 테이블 링킹은 각 노드의 자기 줄(자기 범위 − 직속 자식 줄범위)에서 DDL 테이블명을 단어경계로 검색하되, 주석(`--`, `/* */`)은 제거하고 문자열은 보존한다. 긴 이름 우선(부분매칭 방지).
- **FR-013**: DBMS 에서 본문 첫 DML 키워드(SELECT/WITH=READS · INSERT/UPDATE/DELETE/MERGE=WRITES)가 dml_type 을 정하고, write 대상(INSERT INTO / UPDATE / DELETE FROM / MERGE INTO 직후 테이블)만 WRITES, 그 외 같은 본문 테이블은 READS 로 한다. DML 키워드 없으면 그 노드는 건너뜀.
- **FR-014**: internal 엣지는 기존 노드에 연결, external 엣지는 `upsert_calls`/`upsert_reads_writes` 가 stub 노드(모듈·스키마)를 생성해 연결한다. 모든 엣지 dedup 은 Neo4j MERGE(정체성 키)가 책임진다.
- **FR-015**: 전 스테이지에 걸쳐 LLM·Agent 호출은 0회여야 한다.

### Key Entities *(include if feature involves data)*

- **ReadsWrites 엣지**: Function → Table. 속성 relation(READS/WRITES) + dml_type(SELECT/INSERT/UPDATE/DELETE/MERGE — relation 보다 세분) + evidence_file/line(부속). 정체성 = (function_id, table_fqn, relation). stage1 regex grep 과 DBMS DML 매칭이 생성.
- **Calls 엣지**: Function → Function. 속성 scope(internal/external) + external 분해 메타 callee_module_id/package_id/name. 정체성 = (caller, callee). Framework by_name·DBMS by_ast 양쪽이 동일 dataclass 생성.
- **unresolved_cases 큐**: stage1 의 미해소 매치 목록(case_id·file·line·call_text·function_id·attempted_patterns). step4 stage3 잔여 패턴 루프(Framework 전용)가 소비. DBMS·호출 분류는 "미해결" 개념이 없어 비어 있음.
- **TableAccessPattern (입력)**: step3 ScanResult 가 검증 통과시킨 정규식 + op_mappings(token→표준 op) + ddl_transform.
- **framework_functions / call_wrappers (입력)**: ScanResult 가 제공하는 드롭 대상 글로브 패턴 / wrapper 정의(skill 출처).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 검증을 통과한 정규식 입력 시 매칭 소스에서 ReadsWrites 엣지가 1건 이상 생성되고, 해소 실패는 100% unresolved_cases 큐로 분류된다(엣지로 잘못 박히지 않음).
- **SC-002**: 호출 분류는 internal/external/드롭(stdlib·framework·incomplete) 합이 입력 call_sites 수와 일치 — 누락 없이 모든 site 가 한 경로로 귀결된다.
- **SC-003**: DBMS 테이블 링킹에서 각 DDL 테이블은 자기 줄이 속한 노드에서 정확히 1번 매칭(누락 0·중복 0), 빌트인·자기루프는 0건 통과.
- **SC-004**: step4 stages 1-2 및 DBMS 스텝 전체 실행에서 LLM/Agent 호출 0회.
- **SC-005**: 동일 엔진 검증으로 인해 "검증 통과한 정규식"과 "링킹이 실제 엣지를 만드는 정규식"의 불일치(drift)가 발생하지 않는다.

## Assumptions

- step3 Scan 이 `table_access_patterns` 를 이미 검증(매치·엣지·op_token 인식률 임계)해 넘긴다 — stage1 은 검증된 정규식만 받는다.
- functions·tables·call_sites·parent_of_edges 는 step2(load_ast)가 antlr JSON 에서 수집해 state 로 제공한다.
- DDL 테이블 추출(step3 이전)이 완료되어 있어야 DBMS·Framework 테이블 해소가 가능하다(tables 없으면 DBMS 스텝 skip).
- stdlib 목록은 `shared/lang/stdlib/{c,java,python}_stdlib.json` 에 존재하며 언어별로 로딩된다.
- framework_functions·call_wrappers 는 skill 이 제공한 신뢰 입력으로 간주한다.
- Neo4j MERGE 가 엣지 dedup 의 단일 진실 — state list 의 raw 중복은 그래프에서 자동 합쳐진다.