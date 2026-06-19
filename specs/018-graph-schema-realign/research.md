# Phase 0 Research: 그래프 스키마 정합

## A. 옛 main 대조 (의도 기준 — git 확정)

`origin/main` 의 DBMS 그래프 로직(`analyzer/strategy/dbms/...`)을 git 으로 대조한 사실:

| 항목 | 옛 main (기준) | 현 refactor | 판정 |
|---|---|---|---|
| 구문 흐름 | `build_next_queries`: `(prev)→[:NEXT]→(curr)` | NEXT 를 Rule 전용으로 전용, 구문 흐름 0 | ❌ 소실 |
| 선언부 | `StatementCollector` 가 노드 생성, `NON_ANALYSIS_TYPES`(DECLARE 포함)로 LLM만 제외 | reader `_walk_dbms` 가 DECLARE 노드 통째 스킵 | ❌ 누락(자기 스펙009 위반) |
| 그릇→루틴 | `HAS_PROCEDURE` = 파일→프로시저만 | `HAS_FUNCTION` = 모듈→모든 구문(456) | ⚠️ 남발 |
| 구문 정체 | StatementNode(자기 타입) | 전부 "Function" | ⚠️ 변질 |
| 식별자 | (구버전) | **id 통일(우수)** | ✅ 유지 |

**결정**: 옛 main 의 흐름(NEXT)·구조노드 유지 방식을 **기준**으로 복원하되, refactor 의 id 통일 위에 얹는다. main 코드를 그대로 되돌리지 않음(중복/구조 회귀).

## B. Blast Radius 전수조사 (소비처 — contract 변경 영향)

관계명 통합(`HAS_FUNCTION`/`HAS_VARIABLE`→`HAS_MEMBER`, `BELONGS_TO_PACKAGE`→`BELONGS_TO`)은 출력 그래프 스키마(constitution Cross-Service Contract) 변경 → 전수 grep 결과:

**생산자 analyzer (실라인 전수재확인 2026-06-18 — 12파일, 옛 견적 8에서 +4 보강)**:
- `shared/neo4j/rel_types.py` — 관계 상수 단일 진실.
- `data_models/graph_edges.py` — dataclass(HasFunction/HasVariable/BelongsToPackage 흡수, 구문 NEXT 신규 dataclass).
- `shared/neo4j/edges/{has_function,has_variable,belongs_to_package}.py` — has_member/belongs_to 로 흡수·삭제. **`has_member.py`·`HasMember`·`upsert_has_member` 는 이미 존재**(DataType→Variable 멤버, `MATCH (d:DATA_TYPE)`) → "신설" 아니라 **그릇→루틴까지 포함하도록 확장**(MATCH 라벨 일반화).
- `shared/neo4j/edges/calls.py` — ★external stub QUERY 가 `MERGE (m:MODULE:EXTERNAL)-[:HAS_FUNCTION]->(callee)` + `(m)-[:BELONGS_TO_PACKAGE]->(p)` 사용 → 관계명+MODULE 라벨 동시 정합.
- `shared/neo4j/edges/next.py` — **신규**(구문 흐름 NEXT, statement→statement). 기존 `edges/rule_flow.py` 의 NEXT(Rule→Rule, owner+local_rule_id 매칭)와 **별개 파일·다른 MATCH**. rel 타입 문자열만 `rel_types.NEXT` 공유.
- `analysis_flow/step2_load_ast/antlr_json_reader.py` — NEXT 생성·DECLARE 노드 보존·HAS_MEMBER 루틴한정·그릇 라벨 정체화. `ParseResult` 에 구문 NEXT 필드 추가.
- `analysis_flow/step2_load_ast/load_ast_step.py` — 엣지 영속 호출·반환 state 키 정합.
- `data_models/graph_nodes.py` — Module dataclass 에 컨테이너 primary_label(FILE/CLASS) 도입.
- `shared/neo4j/nodes/module.py` — ★`label = f"MODULE:{sub}"` (line 54) → 컨테이너 종류대로(FILE/CLASS) 라벨 작성.
- 🆕 `shared/workflow/state.py` — **옛 견적 누락**. `WorkflowState` 가 `has_function_edges`/`has_variable_edges`/`belongs_to_package_edges` 키 + `HasFunction`/`HasVariable`/`BelongsToPackage` import 보유 → 키·import 정합 + 구문 NEXT용 **신규 state 키**(기존 `next_edges` 는 Rule-flow 전용이라 재사용 금지=덮어쓰기 회귀).
- 🆕 `shared/workflow/artifact_writer.py` — **옛 견적 누락**. `_CATEGORY` 맵이 `has_function_edges`/`has_variable_edges`/`belongs_to_package_edges` 를 "ast" 로 명시 → state 키 rename 시 매핑 정합(미정합 시 graph_extras 로 떨어지며 warning=품질저하).

**소비처 frontend (12)**: `shared/utils/nodeRelationships.ts`(getRelationshipGroups·getAttachedByType=핵심), `shared/stores/navigator.ts`(moduleTree), `node-detail/{ModuleCard,PackageCard,FileCard,DatatypeCard}.vue`, `shared/utils/graphDisplay.ts`, `shared/config/graphStyles.ts`, `components/graph/GraphTab.vue`, `graphCanvas.ts`, `features/domain/.../MultiLayerOntologyViewer.vue`, `node-detail/TableCard.vue`.

**소비처 catalog (1)**: `robo-data-catalog/service/schema_query_service.py` (Cypher 관계명).

**소비처 architect ingestion (2)**: `api/features/ingestion/hybrid/mapper/{rule_context,agent_validator}.py`.

**전파 주의**: architect 안 `robo-analyzer/robo-data-analyzer/...`(서브모듈)는 생산자와 동일 파일 — 생산자 변경 후 서브모듈 포인터 bump 로 동기화([[manual]] §7).

**결정**: 소비처는 관계명 변경분 정합 + "종류 구분"을 **노드 라벨 필터로 전환**(예: `HAS_FUNCTION` 으로 함수만 뽑던 곳 → `HAS_MEMBER` + 라벨이 PROCEDURE/FUNCTION 인 것). 옛 이름 잔재 0(전수 grep 재확인).

## C. 설계 결정 (Decision / Rationale / Alternatives)

### D1. 관계 통합 — HAS_MEMBER · BELONGS_TO
- **Decision**: `HAS_FUNCTION+HAS_VARIABLE+HAS_MEMBER`→**`HAS_MEMBER`**, `BELONGS_TO_PACKAGE+BELONGS_TO`→**`BELONGS_TO`**. 종류는 노드 라벨.
- **Rationale**: 같은 뜻(그릇이 멤버를 가짐 / 네임스페이스 소속)을 한 이름으로 → 소비처가 단일 관계로 조회. 업계표준(CPG·Kythe: 범용 엣지 + 노드 라벨이 종류).
- **Alternatives**: 전략별 이름(HAS_PROCEDURE/HAS_FUNCTION) → 소비처가 OR 분기 늘어 기각. 현행 분산 이름 → 의미 중복·드리프트.

### D2. NEXT — 구문 흐름 복원 (DBMS 전용)
- **Decision**: `_read_dbms` 가 같은 부모 형제 구문을 등장순 `(prev)→[:NEXT]→(curr)` 연결. framework 는 생성 안 함.
- **Rationale**: 절차적 로직 흐름은 DBMS 분석의 핵심 가치(옛 main 존재). 결정론(LLM 0). framework 는 구문 단위 흐름 개념 없음 → 전략 전용이 맞음.
- **Alternatives**: 라인순서만(엣지 없음) → 흐름 질의 불가, 기각. NEXT 를 framework 에도 강제 → 대응 개념 없어 의미 왜곡, 기각.

### D3. DECLARE 노드 복원 (구조노드 = 노드 유지 + LLM 스킵)
- **Decision**: reader 가 DECLARE 를 **노드로 생성**(다른 구문처럼), `batch_planner._SKIP_ANALYSIS_TYPES` 가 LLM 분석만 제외(이미 TRY/TRIGGER_BLOCK 처리하는 방식과 동일).
- **Rationale**: spec009·batch_planner 가 이미 "그래프 유지+LLM 제외"라 명시 → 코드를 스펙에 맞춤. 누락 표면화(constitution IV). "AST 다 그리고 LLM만 스킵" 원칙.
- **Alternatives**: 현행(노드 삭제) → 누락 오인·자기 스펙 위반, 기각.

### D4. HAS_MEMBER 범위 — 루틴만
- **Decision**: 그릇→**프로시저/함수(루틴)** 에만 HAS_MEMBER. 루틴 내부 구문은 PARENT_OF(중첩)+NEXT(흐름)으로만.
- **Rationale**: 구문은 멤버가 아니라 루틴 본문의 일부. 옛 main HAS_PROCEDURE 의미. "함수 11" 오표기 자동 정상화.
- **Alternatives**: 현행(모든 구문 HAS_MEMBER) → 456 남발·의미 왜곡, 기각.

### D5. 그릇 라벨 — 정체대로
- **Decision**: DBMS=.sql→**FILE**(패키지면 PACKAGE), Java=**CLASS**, C/Python=**FILE**. 파일은 "진짜 그릇일 때만" 노드(Java 는 클래스가 그릇→파일 생략).
- **Rationale**: "MODULE"은 framework 용어 → DBMS 부적합. 라벨=실제 종류(CPG·Kythe). 정보 손실 0(파일 경로는 속성).
- **Alternatives**: 단일 MODULE 강제 → DBMS 의미 안 맞음, 기각.

### D6. 구조노드 라벨 — 단일 맵
- **Decision**: `{DECLARE:"변수 선언부", TRY:"예외 처리", TRIGGER_BLOCK:"트리거 본문"}` 단일 정의처 + "구조" 표시.
- **Rationale**: 고정 소수 타입 → 한 곳 하드코딩 = 단일 진실(III), 중복 아님. 이미 `nodeTypeLabel` 메커니즘 존재.
- **Alternatives**: 노드별/여러 곳 박기 → 중복, 기각. 빈칸 → 누락 오인, 기각.

### D7. 룰 생성 단위 — 통째 우선 / 계층은 정확룰 상승+추상화+dedup
- **Decision**: 룰/예시/질문은 단위(프로시저/함수) 기준. 한 콜에 들어가면 통째 1회(중복0·열화0). 너무 크면 바텀업 — 자식의 **정확한 룰**(요약 아님)을 부모로 올려 부모가 복사 아닌 추상화로 재생성, 각 룰은 한 주인 1회 저장 + 최종 의미 dedup.
- **Rationale**: 통째=중복/열화 원천 차단. 계층 시 "정확룰 상승"으로 요약 열화 방지, "복사금지+추상화"로 중복 방지(map-reduce 표준). 100% 의미중복 0 은 LLM 특성상 불가 → dedup 안전망 + 허용.
- **Alternatives**: 요약본으로 부모 룰 생성 → 열화, 기각. 모든 노드가 룰 생성 → 중복, 기각.

### D8. 소비처 마이그레이션 — 라벨 필터 전환
- **Decision**: 종류별 관계로 뽑던 소비처(getAttachedByType('HAS_FUNCTION') 등) → `HAS_MEMBER` + 노드 라벨 필터.
- **Rationale**: 통합 관계의 자연스러운 소비. 회귀 0(같은 결과, 경로만 라벨필터).
- **Alternatives**: 소비처 그대로 두고 옛 관계 별칭 유지 → 옛 이름 잔재·드리프트, 기각.

### D9. 구문 NEXT 표현 — Rule-flow NEXT 와 분리 (충돌 차단)
- **Decision**: 구문 흐름은 **신규 dataclass `StatementNext(prev_id, next_id)`** + **신규 state 키 `statement_next_edges`** + 신규 `edges/next.py::upsert_statement_next`(노드 id 직접 MATCH·MERGE `[:NEXT]`). 기존 `Next`(owner_id/from_rule_id/to_rule_id)·state 키 `next_edges`·`edges/rule_flow.py` 는 **그대로 유지**(Rule→Rule 흐름).
- **Rationale**: 관계 타입 문자열 `NEXT` 는 "같은 뜻=같은 이름"으로 공유하되(끝점 라벨로 구분: 구문↔구문 vs RULE↔RULE), **데이터 표현·생성 시점·MATCH 가 전혀 달라** dataclass·state 키를 재사용하면 step2(구문)와 step6(룰)가 같은 `next_edges` 키를 default-replace 로 **서로 덮어쓰는 silent 회귀**가 난다.
- **Alternatives**: `next_edges`/`Next` 재사용 → 키 충돌·덮어쓰기, 기각. NEXT 타입명을 둘로 쪼갬(STATEMENT_NEXT 등) → "같은 뜻 같은 이름" 원칙·소비처 단순성 위배, 기각.

### D10. 컨테이너 라벨 정체화(MODULE→FILE/CLASS) — blast 정확화
- **Decision**: Module **dataclass 는 유지**(노드 구조 동일)하되 **Neo4j primary 라벨만** 종류대로: DBMS/C/Python=`:FILE`, Java=`:CLASS`(현 sub_label "CLASS" 를 primary 로 승격). 작성처 `nodes/module.py` 한 곳. `:MODULE` 을 MATCH 하던 모든 곳(`edges/has_function·belongs_to_package`, `calls.py` external stub `:MODULE:EXTERNAL`, catalog `(m:MODULE)`, frontend ModuleCard/navigator)을 동시 정합. 관계 통합(HAS_MEMBER)으로 has_function/has_variable MATCH 는 어차피 재작성되므로 라벨 제약은 **제거(무라벨 MATCH by id)** 가 회귀안전.
- **Rationale**: FR-008 "정체대로". frontend 에 `FileCard.vue`·`ModuleCard.vue` 둘 다 존재 → 라벨→카드 라우팅 대응됨. owner_id/`summarize_module`(step7) 는 노드 속성·id 기반이라 라벨 변경 무영향.
- **Alternatives**: Module dataclass 자체 rename(Container 등) → step2~7·소비처 전역 심볼 변경으로 회귀면 폭증, 과도 → 기각(라벨만 변경이 최소·충분). external stub 까지 FILE 강제 → 외부 콜리는 파일 미상이라 의미 안 맞음 → external 컨테이너는 종류미상 중립 처리(라벨 정책 별도 메모).

## D. 미해결 (NEEDS CLARIFICATION) — 없음
설계 전부 사용자와 사전 합의. 잔여 모호점 0. (D9·D10 은 구현 착수 전 코드 실라인 전수조사로 추가 확정된 정밀화 — 설계 방향 불변, 누락 보강.)
