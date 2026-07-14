# Research: opaque graph id 전수 감사

## 판정 원칙

- Graph node id는 opaque key이며 구조적으로 자르지 않는다.
- 이름은 `name`, 부모는 `owner_id` 또는 관계, AST 종류는 `label`을 사용한다.
- SQL FQN, 파일 경로/확장자, URL처럼 id가 아닌 별도 도메인 값의 파싱은 계약을 확인해 별도 분류한다.

## 확인된 결함

| 위치 | 결함 | 교체 기준 |
|---|---|---|
| `shared/lang/call_classifier.py` | 함수 id에서 이름 추출 | `CodeNode.name`, `owner_id` |
| `data_models/graph_nodes.py::own_ast_type` | id suffix에서 AST 종류 추출 | `CodeNode.label` |
| `agent_tools/code_inspection.py` | id에서 표시 이름 추출 | `CodeNode.name` |
| `analysis_flow/step7_post_processing/embed_step.py` | Column id에서 table 이름 추출 | 명시 소속/name |
| `shared/neo4j/edges/calls.py` | external id를 점 연결 문자열로 생성 | 중앙 `code:external:` helper |
| `search/retriever.py` | 표시명 fallback을 code id 조각으로 생성 | 명시 `name`/`label` |
| frontend `PackageCard.vue`, `graphDisplay.ts` | 표시명을 graph id 마지막 조각으로 생성 | 명시 `properties.name`/종류 |

## 제외 판정

| 위치 | 판정 | 근거 |
|---|---|---|
| analyzer `reference_linker.py` | 유지 | source include/import의 파일 경로를 파싱하며 graph id가 아니다. |
| analyzer DBMS `call_linker.py` | 유지 | SQL/PLSQL call source text에서 schema/procedure 토큰을 파싱한다. |
| analyzer `rw_table_filter.py` | 유지 | LLM table FQN과 READS/WRITES table FQN을 비교하는 도메인 정규화다. |
| catalog `sample_context_service.py` | 유지 | API 계약의 `table_fqn`과 datasource table name을 정규화한다. graph node id가 아니다. |
| catalog `lineage_analyzer.py` | 유지 | SQL table reference를 schema/name으로 파싱한다. |
| architect `rule_extractor.py`, `agentic_retriever.py` | 수정 불필요 확인 | production 코드는 이미 `owner_id`/`source_module`을 사용하며 검색 hit는 과거 결함 설명 주석뿐이다. |
| analyzer `embed_material.py`의 `TYPE:name` split | 유지 | 함수 내부에서 만든 관계 material wire format이며 graph id가 아니다. |

## 실제 shopmall artifact 검증

- 최종 AST artifact: Package `code:mall` 1, Module 12, CodeNode 124.
- code/DB 전체 597 id 중 중복 0.
- 3,174 call site 재분류: internal 1,871, external 85, stdlib drop 1,218.
- workspace 정의 이름이 external로 분류된 건수 0.
- `apply_point_balance`, `insert_price_history`, `cart_clear`는 모두 실제 `code:mall/...` 내부 id로 연결됨.
