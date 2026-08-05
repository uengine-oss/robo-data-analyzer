# Tasks: 데이터소스 테이블 발견 + 샘플 수집 통합

체크 = 증거 확보 시점 (코드 작성 ≠ 완료).

## Phase 1: catalog — 발견 엔드포인트 (Cross-Service 선행)

- [x] T101 catalog `integrations/data_fabric.py`에 fabric REST 프록시(`list_tables`·`table_schema`) 추가
- [x] T102 catalog `samples/discovery.py` — 열거→테이블별 schema+sample 병렬(semaphore)·
      부분실패 degraded·max_tables 절단 로그
- [x] T103 catalog `api/table_samples.py` `GET /tables/discovery` 라우트 + 404/502/422 계약
- [x] T104 catalog 계약 테스트 (fabric 게이트웨이 모킹): 정상·부분실패·404·절단 **증거**: pytest
- [x] T105 catalog spec 문서 (contracts/discovery-contract.md 참조 링크)

## Phase 2: analyzer — 수집 step

- [x] T106 `integrations/catalog.py` `get_table_discovery()` (404=None·non-200 raise)
- [x] T107 `discover_datasource_step.py` — 가드 1벌·병합(대소문자 무시·DDL 우선)·
      신규 Table/Column/HAS_COLUMN 업서트·table_sample_data 생산·404 경고 계속
- [x] T108 단위 테스트: US1(DDL 0→단어장 N)·US2(합집합·중복 0·DDL 우선)·US3(형태 불변)·
      skip 가드·404·부분 degraded **증거**: pytest

## Phase 3: 흡수·배선

- [x] T109 strategies.py 두 전략: discovery 삽입 + fetch_table_samples 제거
- [x] T110 `fetch_table_samples_step.py` 삭제 + 관련 테스트 정리 — 옛 참조 grep 0 (SC-003)

## Phase 4: 검증

- [x] T111 analyzer 전량 pytest (기준선: 사전 존재 실패 1건 외 green) + catalog 전량 pytest
- [x] T112 종료 감사 — 양 저장소 git diff 재독·WIP(README) 미접촉·옛 심볼 0·
      실기동(E2E fabric·Neo4j) 미가용 시 미검증 명시


## 완료 증거 (2026-07-30)

- catalog: tests/unit/test_table_discovery.py **4 passed** (정상·degraded·truncated·404).
  전량 54+4 passed / 실패 3건 = test_analysis_graph_visibility — stash 역실험으로 **사전 존재** 증명.
- analyzer: tests/pipeline/step1_parse_ddl/test_discover_datasource_step.py **7 passed**
  (US1 DDL0→단어장 / US2 합집합·DDL우선·대소문자무시·중복0 / US3 형태불변 / 가드 2종 / 404 경고).
  전량 **618 passed·5 skipped** / 실패 1건 = test_wipe_graph (스펙 106에서 사전 존재 증명 완료).
- superstep 계약 테스트 갱신: 병렬 3멤버 + state 키 서로소 근거 주석 (spec 107 D3 의도 변경).
- 옛 참조: fetch_table_samples_step import·배선·호출 **0** (남은 1건은 신설 step 주석의 역사 기록).
- 종료 감사: 양 저장소 git status 재독 — 의도 파일만 변경, 사용자 WIP(README.md 양쪽) 미접촉.
- **미검증 (정직 기록)**: 실 fabric·MindsDB·Neo4j E2E — 세션 내 런타임 미가용.
  실기동 시: /analyze(datasource 지정) → step1 데이터소스 발견 로그 → Neo4j
  `MATCH (t:TABLE) WHERE t.description_source='catalog'` 확인 → step7 컨텍스트 샘플 유입 확인.
