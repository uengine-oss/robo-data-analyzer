# Feature Specification: 클러스터 검색의 규칙/예시/테이블 전달 + AFFECTS_TABLE 적재 결함 수정

**Feature Branch**: `056-cluster-rules-affects-table-delivery`

**Created**: 2026-07-15

**Status**: Implemented (US1 완료·라이브검증, US2 수정+재분석 검증)

**Input**: 사용자 목표(shopmall 재분석→그래프 원문 일치→example·rule 아키텍트 전달). 상위 계약:
`search/service.py`(REST·MCP 공유코어), `shared/example_mapping/builder.py`, `shared/neo4j/edges/affects_table.py`.

## 동기와 인과

```
① 목표: 아키텍트(cluster_retrieve)가 함수별 rules[{statement, examples[{given,when,then,tables[{name,op}]}]}] 를 받아야 한다
② 현실 A: 검색 코어(_node_row)는 reads/writes/calls 만 반환하고 rule/example 을 트래버스하지 않았다 → 아키텍트가 규칙을 못 받음
③ 현실 B: 재분석해도 AFFECTS_TABLE 엣지가 0 → example 의 tables 가 항상 비어 있었다(그래프 무결성 결함)
④ 원인(B): constrain_tables_to_rw 가 example.affected_tables 를 TABLE **id**(`db:order_chk_hst`)로 치환하는데,
   AFFECTS_TABLE upsert 는 `last(split(name,'.'))` 로 **점(.)만** 분리해 `db:` 콜론 접두사를 못 벗겨 TABLE
   (`name=order_chk_hst`)과 매칭 실패 → 엣지 0.
```

## 배경과 현재 사실 (조사 완료, 라이브 증거)

- **검색 코어 단일 진실**: `search/service.run_search` 를 REST(`/robo/search/semantic`)와 MCP(`cluster_retrieve`)가
  공유한다(사본 0). 따라서 `Retriever._node_row` 반환에 `rules` 를 additive 로 추가하면 두 소비자 모두 즉시 받는다.
- **RULE 노드 공유**: `builder.rule_hash` 로 같은 statement 는 함수 간 1 노드 공유. 단순 `(fn)-[:HAS_RULE]->(r)
  -[:HAS_EXAMPLE]->(e)` 는 타 함수 Example 을 끌어온다. Example id=`{fid}::{rh}::{idx}` 라서
  `e.id STARTS WITH '<fid>::'` 로 함수 소유 Example 만 스코프해야 오염 0.
- **EXAMPLE 속성**: Cypher 예약어 회피로 `given`/`when_`/`then_`(dataclass 는 `when`/`then`).
- **AFFECTS_TABLE 결함(라이브 확정)**: 재분석 스트림에 AFFECTS_TABLE relationship_event 269건이 있으나
  그래프 엣지 0. TABLE `id='db:order_chk_hst'`·`name='order_chk_hst'`, affects item.table_name='db:order_chk_hst'.
  현재 매칭 쿼리=0건, 콜론 접두사 스트립 추가 시 1건 매칭(검증됨). 구 그래프도 0 — 내 작업 이전부터의 결함.

## User Scenarios & Testing

### User Story 1 - 클러스터 검색이 함수별 규칙/예시/영향테이블을 전달한다 (P1) — 구현완료

`Retriever._node_row` 가 각 코드노드에 대해 `HAS_RULE→HAS_EXAMPLE→AFFECTS_TABLE` 를 트래버스해
`rules[{statement, examples[{given, when, then, tables[{name, op}]}]}]` 를 반환한다.

**Independent Test**: fake session 단위테스트(그룹핑·prefix 스코프·빈규칙·null테이블·무규칙) + 라이브 Cypher.

**Acceptance**:
1. 공유 RULE 노드로 인한 타 함수 Example 오염이 `e.id STARTS WITH` 스코프로 0.
2. table 은 `{name, op}` 로 AFFECTS_TABLE 의 op(READ/INSERT/UPDATE/DELETE/MERGE)를 보존.
3. REST/MCP 공유코어에 additive → 기존 노드행 소비자 회귀 0. TABLE 행에는 `rules: []`(스키마 일관).

### User Story 2 - example 의 AFFECTS_TABLE 엣지가 실제로 적재된다 (P1) — 결함 수정

`example.affected_tables` 가 TABLE id(`db:` 접두)든 bare 이름이든 AFFECTS_TABLE 엣지가 생성된다.

**Independent Test**: 재분석 후 그래프 AFFECTS_TABLE 엣지 수 > 0, 스트림 이벤트 수와 근사 일치.
표본: record_check_hst example → order_chk_hst INSERT 엣지 존재.

**Acceptance**:
1. **Given** affected_tables.table='db:order_chk_hst'(constrain 산출), **When** upsert, **Then** TABLE
   `name=order_chk_hst` 와 매칭돼 AFFECTS_TABLE 엣지 생성.
2. **Given** affected_tables.table='orders'(LLM bare), **When** upsert, **Then** 동일하게 매칭(회귀 0).
3. **Given** 존재하지 않는 테이블명, **When** upsert, **Then** 엣지 미생성(무근거 엣지 금지).

## Requirements

- **FR-001 (US1)**: `Retriever._rules_for(cid)` 신규 — `e.id STARTS WITH '<cid>::'` 스코프, 규칙 순서 보존,
  example 별 `{given, when, then, tables[{name, op}]}`. `_node_row` 반환에 `rules` 추가, `_table_row` 에 `rules: []`.
- **FR-002 (US1)**: 예약어(when/then) 맵키 회피 — flat row 반환 후 Python 그룹핑(테스트 가능).
- **FR-003 (US2)**: `affects_table.py` upsert 의 TABLE 매칭이 `db:` **콜론 접두사**를 벗긴다 —
  `last(split(last(split(item.table_name, ':')), '.'))`. 점 기반 스키마 접두사도 계속 흡수.
- **FR-004**: 두 변경 모두 결정론. LLM 미사용. 옛 심볼·중복 경로 0.

## Success Criteria

- **SC-001**: `search/test_rules_traversal.py` 5개 통과 + 라이브 Cypher 가 실 함수의 규칙/예시 중첩 반환.
- **SC-002**: 재분석 후 그래프 AFFECTS_TABLE 엣지 > 0(0 → 다수), record_check_hst→order_chk_hst 확인.
- **SC-003**: cluster_retrieve/REST 검색 결과의 코드노드에 rules 채워지고 tables 비지 않음(테이블 접점 함수).

## Assumptions

- TABLE 노드 id 는 `db:<name>`, name 은 bare. constrain_tables_to_rw 는 example.table 을 TABLE id 로 치환.
- 의미 중복·환각은 이 스펙 범위 밖(spec 054 폴백 담당). 여기는 전달·적재 정합만.
