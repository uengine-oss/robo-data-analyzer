# Tasks: 테이블 논리명 부여 + 설명·요약 비즈니스 품질

**Input**: [spec.md](spec.md) · [plan.md](plan.md) · [research.md](research.md) · [data-model.md](data-model.md)

**검증 = 측정**(헌법 VI): 재분석(024와 1회 묶음) 후 그래프 직접쿼리 + 요약 샘플 원문대조 + 검색 재측정.

## ★진행 상태 (2026-06-26)
- ✅ **코드 완성 + 스모크검증**: TableDescription·Table에 logical_name / table_description.yaml(dbms+framework) 논리명 지침 / step7·table.py(SET 빈값가드) / dbms code_summary 업무이벤트 지침+예시 / frontend TableCard 논리명 표시(rem·CSS변수, 매직px 0). import OK, Table.id 정규화(024) 유지 확인.
- ✅ **KV캐시 안전**: 공통 `_common.yaml` system 미수정(분할고유 user_template_specific·자체 system만) → byte-identical 유지.
- ⏭ **framework code_summary.yaml**(non-batch, 별파일) 요약 지침 = framework 검증 시 병행(DBMS가 테스트 도메인).
- ⏭ **재분석 검증(T010~T016)**: 024+025 코드 다 됐으니 **재분석 1회 = 마지막 게이트**(공유그래프 변경·전후 diff·검색 재측정·프론트 스크린샷).

---

## Phase 1: Setup
- [ ] T001 logical_name 산출 근거 확인: code_summary가 코드노드 logical_name 만드는 방식 재확인(동일 패턴 차용)

## Phase 2: Foundational (데이터 모델 — US 선행)
- [ ] T002 `data_models/code_analysis_output.py` TableDescription 에 `logical_name: str = ""` 추가
- [ ] T003 `data_models/graph_nodes.py` Table 에 `logical_name: str = ""` 필드 추가

---

## Phase 3: User Story 1 — 테이블 논리명 (P1)
- [ ] T004 [US1] `prompts/dbms/table_description.yaml` + `prompts/framework/table_description.yaml` — 짧은 논리명 산출 지침 + 출력필드(logical_name). 물리명 그대로 X·근거없으면 빈값
- [ ] T005 [US1] `analysis_flow/step7_post_processing/enrich_table_description_step.py` table_updates 에 `logical_name: r.logical_name` 포함
- [ ] T006 [US1] `shared/neo4j/nodes/table.py` to_item + MERGE_QUERY 에 logical_name SET(nonempty 적용됨 → 빈값 미저장)
- [ ] T007 [US1] frontend `robo-data-frontend` TableCard.vue — 테이블 논리명 표시(물리명 병기). 논리명 없으면 물리명만(폴백)

## Phase 4: User Story 2 — 요약·설명 비즈니스화 (P2)
- [ ] T008 [US2] `prompts/dbms/code_summary_batch.yaml` + framework — user_template_specific 예시를 업무이벤트형으로 손질 + "행/컬럼 기계어보다 업무이벤트 우선, 단 값·코드 보존·날조금지" 지침 1줄. ★공통 `_common.yaml` system 불변(byte-identical)
- [ ] T009 [US2] table_description.yaml 설명 문체도 동일 원칙 점검(값/코드 보존·날조금지 유지)

---

## Phase 5: 검증 (024와 1회 재분석 묶음)
- [ ] T010 ★재분석 1회(024+025) → snap_after 박제
- [ ] T011 그래프 검증: 테이블 logical_name 채움(빈값저장0)·논리명≠물리명(샘플)·024분(analyzed 16/16·선두점0) 동시 확인
- [ ] T012 요약 샘플 원문대조: 값·코드(M/Q/S/D/P) 보존100%·없는내용 추가0
- [ ] T013 KV캐시 검증: 공통 system byte-identical(check_byte_identical.py/wc -c)
- [ ] T014 ★검색 회귀: module_test/임베딩서칭 flow_eval — 요약변경이 검색 정확도 의미있게 안 떨어뜨림(하락 시 문체 재고)
- [ ] T015 프론트 검증: 헤드리스 스크린샷 Read로 TableCard 논리명 표시 확인([[manual]]§3 하니스)
- [ ] T016 스냅샷 diff(snap_before vs after): 의도변화(논리명·analyzed·id) + 회귀0

---

## Dependencies
- T002/T003(모델) → US1/US2 선행. T004→T005→T006(논리명 생성→저장). T007/T008/T009 병행 가능.
- ★T010 재분석은 024 코드 완료 전제(이미 완료) + 025 코드 완료 후 1회.

## MVP
US1(테이블 논리명) = 핵심. US2(요약 문체)는 품질 향상(검색 영향 모니터).

## Independent Test
| US | 검증 |
|---|---|
| US1 | 테이블 논리명 채움·표시, 빈값0, 논리명≠물리명 |
| US2 | 값·코드 보존100%·날조0, 검색 회귀0 |
