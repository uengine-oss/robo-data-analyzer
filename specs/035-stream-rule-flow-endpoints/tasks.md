# Tasks: 라이브 스트림 ↔ neo4j 그래프 정합

**Tests**: 재분석 후 스트림 vs neo4j 전수 비교(노드·관계·끝점)로 대체.

## Phase 1: 룰 흐름 (NEXT/BRANCH)

- [X] T001 `analyze_code_step.py _emit_code_graph` — `rule_by_local = {hr.local_rule_id: hr.target_id}` 매핑으로 NEXT/BRANCH 를 룰→룰(룰노드 id) 발신. 미해소 시 생략. owner_id+rule_id 형식 제거.

## Phase 2: 외부 호출 스텁

- [X] T002 `shared/neo4j/edges/calls.py` — 외부 해소 로직을 `_split_calls()` 로 추출(저장·발신 공유). `upsert_calls` 가 사용하도록 리팩터(동작 불변).
- [X] T003 `calls.py emit_calls()` 신규 — internal CALLS + external 스텁 노드(FUNCTION/MODULE:EXTERNAL)+HAS_MEMBER(+PACKAGE/BELONGS_TO)+CALLS 발신. 저장과 동일 id.
- [X] T004 `link_calls_by_name_step.py`(framework)·`link_calls_by_ast_step.py`(dbms) — `emit_edges(stream, edges)` → `emit_calls(stream, edges)`. 미사용 emit_edges import 제거.

## Phase 3: 검증

- [X] T005 py_compile 4파일 OK.
- [X] T006 zapamcom framework 재분석 + 스트림 vs neo4j 전수 비교 → **완전일치**(노드 306=306, 관계 14타입 전부 OK, self-loop 0, 허공선 0, 끝점불일치 0).
- [ ] T007 데스크톱 analyzer 재기동(dev-desktop -SkipBuild)으로 배포 — 앱 라이브 분석 시 self-loop·외부호출누락 없음 확인.

## 변경 경계 (회귀 방지)

- 저장(upsert_calls/EXTERNAL_QUERY/rule_flow.py) 불변. 범용 emit_edges(타 관계) 불변. 프론트 무변경.
