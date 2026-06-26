# Tasks: 테이블·컬럼 분석설명 유실 복구 + 식별자 빈값 정규화

**Input**: [spec.md](spec.md) · [plan.md](plan.md) · [research.md](research.md) · [data-model.md](data-model.md) · [contracts/interfaces.md](contracts/interfaces.md)

**검증 = 측정**(헌법 VI·원칙 §6): 단위테스트 대신 **재분석 실행 후 그래프 직접쿼리**로 검증.

## ★진행 상태 (2026-06-26)
- ✅ **코드 구현 완료 + 스모크검증**: fqn 헬퍼(`shared/neo4j/ids.py`, 자체테스트 통과) → parser.py 5곳·graph_nodes.py(Schema.id+Table.id)·step7 write 2곳·table.py/column.py(nonempty+매칭수 표면화) 적용. import 순환 0, Table.id=`RWIS.RDD01DD_TB`·Schema.id=`RWIS`(선두점 제거) 확인.
- ✅ **블래스트 100% 확정**: analyzer 내부 한정(text2sql=PascalCase `:Table` 별개라 무관, 검증). 외부 수정 0.
- ✅ **baseline 스냅샷 박제**: scratchpad/snap_before.json (노드1090·관계2282·선두점16·analyzed 6/16·61/153).
- ⏭ **재분석 검증(T008/T011)은 025(프롬프트+table 논리명) 후 1회** — 사용자 지시(공유그래프 1회 변경 + 전후 비교).

---

## Phase 1: Setup (기준선·핀포인트)

- [ ] T001 수정 전 현황 박제(회귀 기준): Neo4j 직접쿼리로 TABLE/COLUMN analyzed_description 채움률(6/16·61/153)·선두점 id 수(16)·빈 db/comment 키 존재 기록 (`specs/024-analyzed-description-recovery/quickstart.md` §1 쿼리)
- [ ] T002 insights table_fqn 원천 핀포인트: `analysis_flow/step4_linking/` reads/writes fqn 생성지점 grep로 확정(어디서 `RWIS.X` 2조각이 나오는지) → fqn 헬퍼 적용 대상 확정

## Phase 2: Foundational (모든 US 선행 — 단일 fqn 헬퍼)

- [ ] T003 단일 fqn 헬퍼 작성: `shared/neo4j/props.py`(또는 신규 `shared/ids.py`)에 `def fqn(*parts) -> str` — 비어있지 않은 조각만 "." join(빈 구성요소 생략, 선두/중복 점 0). 결정론·전략무관

---

## Phase 3: User Story 2 — 노드 식별자 단일형식 (P1, 근본)

**Goal**: 생성 시점과 후처리 시점이 동일 식별자를 산출(빈 db 점 안 남김).
**Independent Test**: 빈 db 환경에서 `Table.id`·`Column.id`에 선두점 0, 생성/후처리 식별자 글자단위 동일.

- [ ] T004 [US2] `data_models/graph_nodes.py` Table.id property → `fqn(self.db, self.schema, self.name)` 위임 (직접 f-string 폐기, :78)
- [ ] T005 [US2] Column.id 생성지점(DDL 파싱) → `fqn(db, schema, table, name)` 위임 (graph_nodes.py:91 + 생성 호출부 grep로 확정)
- [ ] T006 [US2] 심볼변경 전수grep(원칙 §5): `{db}.{schema}` 류 id 직접조립 잔재 0 확인 — 모든 조립이 헬퍼 경유

## Phase 4: User Story 1 — 분석설명이 노드에 반드시 반영 (P1)

**Goal**: 생성된 analyzed_description이 유실 없이 해당 테이블·컬럼에 저장.
**Independent Test**: 재분석 후 R/W 테이블·컬럼 analyzed_description 빈 것 0(특히 RDD01DD_TB).

- [ ] T007 [US1] step7 `enrich_table_description_step.py` table write id(:117)·column write id(:124) → `fqn(...)` 헬퍼로(노드 id와 동일). insights table_fqn 원천(T002)도 헬퍼 정규화 → write가 노드에 적중
- [ ] T008 [US1] 재분석 1회 실행(`cli_scripts/analyze_data_dir.py <대상> --strategy dbms`) → 그래프에서 R/W 테이블·컬럼 analyzed_description 빈 것 0 확인(RDD01DD_TB 포함)

## Phase 5: User Story 3 — 빈값미저장 + 조용한 no-op 표면화 (P2)

**Goal**: 빈값 키 미저장 + id-match 갱신 0건 표면화.
**Independent Test**: 빈 comment·빈 db 키 부재 + 매칭 0건 시 경고 로그.

- [ ] T009 [US3] `shared/neo4j/nodes/table.py`·`column.py` MERGE_QUERY 입력에 `nonempty()` 적용(빈 db·comment 키 미저장, coalesce 보존의미 유지)
- [ ] T010 [US3] `update_analyzed_descriptions`가 SET 매칭 건수 반환 → step7이 (매칭수 < 후보수)면 `log_warning`로 표면화(조용한 no-op 차단)

---

## Phase 6: Polish & 검증 (cross-cutting)

- [ ] T011 재분석 후 전수 검증(quickstart §3): ① analyzed_description 유실 0 ② 선두점 id 0 ③ 빈 db/comment 키 미저장 ④ no-op 경고 동작 ⑤ ★기존 채워졌던 6개·코드노드 id 회귀 0 — 그래프 직접쿼리+로그 대조(개수만 X)
- [ ] T012 소비처 회귀 확인: catalog `/robo/graph`·frontend 그래프에서 테이블 노드 정상(불투명 id read) — 깨짐 0
- [ ] T013 데드/잔재 정리(원칙 §3·§4): 옛 id 직접조립·중복 fqn 로직 0, 주석 짧게. measure/todo 기록

---

## Dependencies & 실행 순서
- T003(fqn 헬퍼) = 전 US 선행(Foundational).
- US2(T004~6 id 단일형식) → US1(T007~8 write 적중) 의존: id 헬퍼가 양끝 맞춰야 write가 노드에 닿음.
- US3(T009~10)은 US1과 독립적이나 같은 파일(table.py/column.py) 손대므로 T007 후 권장.
- Phase 6 = 전 US 후 재분석 검증.

## Parallel Opportunities
- T004/T005(graph_nodes 다른 부분) 병행 가능. T009/T010(table/column writer) 병행.
- 검증: 그래프쿼리·로그대조 동시.

## MVP 범위
US2(id 단일형식) + US1(write 적중) = 최소 = 유실 복구. US3는 클린성·안전망 확장.

## Independent Test Criteria
| US | 독립검증 |
|---|---|
| US2 | 선두점 id 0, 생성/후처리 식별자 동일 |
| US1 | R/W 테이블·컬럼 analyzed_description 유실 0(RDD01DD_TB 채움) |
| US3 | 빈값 키 미저장 + 매칭0 경고 |
