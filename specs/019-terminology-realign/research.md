# Phase 0 Research: 용어 정체화

## A. Blast Radius 전수조사 (코드/로그 grep 확정 2026-06-18)

### A-1. 그릇 라벨 (MODULE)
- 작성 단일점: `shared/neo4j/nodes/module.py:55` `label = f"MODULE:{sub}" if sub else "MODULE"`.
- 종류 출처: `shared/lang/ontology.py`(`role_of`/`sub_label_of`) + `ontology_roles.yaml`. Java class→sub "CLASS", C/Python file→sub "FILE", DBMS file→sub "" (∴ :MODULE 만 = 타입 소실).
- 제약: `schema_constraints.py:29` `FOR (m:MODULE) REQUIRE m.id IS UNIQUE`.
- 외부 stub: `edges/calls.py:40` `MERGE (m:MODULE:EXTERNAL …)` — **의도적 유지**(종류미상).
- 소비처(frontend, MODULE 가정): `nodeClassify.ts:43`(container 판정)·`navigator.ts`(buildPkgTree 269·flat 307·ROLE 45/80 — 트리 채움)·`templateRouter.ts:64`(MODULE→file/module, 68 FILE→file 이미)·`graphStyles.ts`(색104·크기239·PRIMARY263)·`analyzer.ts:43`(PRIMARY_LABELS).

### A-2. 로그/스트림 "FUNCTION" 하드코딩 (표시-영속 불일치 누수)
- `analysis_flow/step6_analyze_function/analyze_function_step.py:377` node_event `"label":"FUNCTION"`(DBMS SELECT/IF도 FUNCTION) + `:407/420/426/427/432/433` rule-flow from/to label "FUNCTION".
- `step7_post_processing/embed_step.py:68` `("FUNCTION", function_updates)`.
- 실제 영속 라벨은 `nodes/function.py:95` `primary_label`(DBMS=:SELECT 등)이라 **로그 표기(FUNCTION)와 영속 라벨(SELECT)이 불일치** = 누수.

### A-3. 분석단위 용어 "function/Function"
- `data_models/graph_nodes.py` `Function` dataclass (★전역 import 다수 파일).
- state `functions`(state.py)·`analyze_function`(step6)·`upsert_functions`/`update_summaries`(nodes/function.py)·`function_*` 식별자 광범위.
- ★구분: **실제 루틴 의미**(`:FUNCTION`/`:PROCEDURE` 라벨, `_ROUTINE_LABELS`, ontology "function"→"FUNCTION" role 매핑)는 **그대로** — 그건 진짜 함수라 옳음. 개명 대상 = "분석단위 전체를 function 으로 부르는" 곳(dataclass·state·step·변수).

### A-4. 소비처 catalog/architect
- 라벨 참조(`:MODULE` 등) 전수 grep 후 정합 필요(018 에서 관계명은 이미 정합, 라벨은 본 스펙).

## B. 결정 (Decision / Rationale / Alternatives)

### D1. 분석단위 중립 용어 = `CodeNode` (사용자 확정)
- **Decision**: `Function` dataclass → `CodeNode`, state `functions`→`code_nodes`, step `analyze_function`→`analyze_node`(동급), `upsert_functions`→`upsert_code_nodes`, `function_*`→종류중립. 실제 루틴 라벨·판별은 유지.
- **Rationale**: 그래프 "노드"와 자연스럽고 프로시저·구문·구조 모두 포괄. (Routine 은 구문·구조 배제라 부적합.)
- **Alternatives**: CodeUnit/Unit(Unit 모호)·AnalysisUnit(길어 변수명 비대) → 기각.

### D2. 노드 라벨 = 실제 타입 하나, 역할 라벨(FUNCTION/MODULE) 저장 폐기 (★최종확정 2026-06-19)
- **Decision**: 모든 노드 = **자기 antlr 타입 단일 라벨**(`:FILE`/`:CLASS`/`:METHOD`/`:SELECT`…). 역할 라벨 FUNCTION/MODULE 은 **Neo4j 에 저장 안 함**(속성으로도 안 둠). 역할(호출가능/그릇)은 소비처가 **타입에서 파생**(호출가능={METHOD,FUNCTION,PROCEDURE,TRIGGER}, 그릇={FILE,CLASS,INTERFACE,RECORD}). ★`:FUNCTION`/`:PROCEDURE` 가 **DBMS 실제 타입**인 경우는 유지(역할 아님). 외부 stub 만 `:FUNCTION:EXTERNAL`/`:MODULE:EXTERNAL`(타입 미상 fallback).
- **Rationale**: 사용자 원칙 "각 노드 = 자기 타입, DBMS 서브라벨 0". 역할은 타입에서 100% 파생 가능 → 저장 불필요(=비표준·군더더기). DBMS 가 이미 이 방식(`:SELECT`) — framework 도 동일화 = 완전 일관. constitution III(타입=온톨로지 단일진실).
- **Alternatives**: ① MODULE 역할 병기 유지 → 군더더기·"각 노드 진짜 라벨" 위배, 기각(이 세션 초기 thrash). ② 역할을 노드 속성으로 → 소비처가 라벨 기반이라 전부 깨짐·비표준, 기각.
- **소비처 영향(역할 라벨 읽던 곳을 타입파생으로)**: frontend `nodeClassify`(그릇·호출가능 판정)·`templateRouter`(카드)·`navigator`(그릇 트리)·`graphStyles`(PRIMARY)·백엔드 `embed_step.py:68 MATCH(:FUNCTION)`(→무라벨 id 또는 타입집합)·catalog/architect 라벨 MATCH. **생산자+소비처 원자 정합**(반응형 금지=thrash 회귀).
- **★주의**: Neo4j 라벨 집합이라 표시 종류 선택(specificLabel)·templateRouter 는 라벨 순서 무관하게(타입 집합 우선) 정합.

### D3. 로그/스트림 종류 = 노드 실제 종류 (표시-영속 일치)
- **Decision**: `analyze_function_step` 등의 하드코딩 "FUNCTION" → 그 노드의 실제 종류(primary_label)로. 단일 도출 헬퍼로(노드→표시 종류) DRY.
- **Rationale**: 표시-영속 일치(FR-005). 로그 신뢰(사용자 지적).
- **Alternatives**: 현행 고정 FUNCTION → 누수, 기각.

### D4. 종류 출처 = 온톨로지 단일 진실 (신규 매핑 없음)
- **Decision**: 종류는 `ontology_roles.yaml`/`sub_label_of` 가 이미 도출 — 그대로 사용, DBMS 빈값만 FILE 기본.
- **Rationale**: constitution III. enum=DATA_TYPE(그릇 아님) 확인 → 그릇 종류 모호성 0.

### D5. KV 캐시 — 프롬프트 텍스트 불변 확인
- **Decision**: 코드 식별자 개명(`Function`→`CodeNode`)은 **프롬프트 system 본문 텍스트를 바꾸지 않아야** 함. 프롬프트 yaml 에 "function/함수" 단어가 분석단위 지칭으로 있으면 변경 보류(또는 byte-identical 유지). plan/tasks 에서 `prompts/**` "function" 등장 확인.
- **Rationale**: constitution V — prefix 캐시 byte-identical. 개명은 코드 내부라 프롬프트와 분리 가능.

### D6. 단계화 (독립 검증 — 위험 분리)
- **Decision**: B(로그) → A(그릇 라벨, 생산자+프론트 원자) → C(코드 개명, 대규모 기계적). B 는 저위험·즉효(사용자 로그 불만 해소), A 는 contract+프론트, C 는 광범위하나 동작 불변(rename).
- **Rationale**: 로그 누수(B)는 작고 독립 → 먼저. 라벨(A)은 원자. 개명(C)은 회귀검증 위주. 각 독립 검증.

## C. 미해결 (NEEDS CLARIFICATION) — 없음
중립 용어(CodeNode) 사용자 확정. 그릇 라벨·로그·온톨로지 설계 합의. KV 프롬프트 확인은 tasks 의 작업 항목(설계 모호 아님).
