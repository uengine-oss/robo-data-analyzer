# Tasks: MCP 레거시 검색 결과 정예화

**Input**: Design documents from `/specs/057-cluster-result-refinement/`

**Prerequisites**: plan.md, spec.md, research.md(D1~D6), data-model.md(V1~V7),
contracts/mcp-cluster-retrieve.md, quickstart.md

**Tests**: 스펙이 검증 게이트를 명시(SC-001~006)하므로 테스트 태스크 포함.
커밋 태스크 없음 — 사용자 지시 전 커밋 금지(워킹트리 유지).

**Organization**: US1(유관한 것의 전문, P1) / US2(한도 내 도달, P1) / US3(프론트 무회귀, P2).

## Format: `[ID] [P?] [Story] Description`

## Phase 1: Setup

- [ ] T001 실측 기준선 고정: 검증 질의 현행 응답을 `specs/057-cluster-result-refinement/baseline/`
      에 저장(REST 결과 JSON + 크기·노드 22·노이즈 14 목록) — 전후 대조의 원본.
      (도구: 세션 scratchpad `payload_breakdown.py`·`inclusion_paths.py` 재사용)

## Phase 2: Foundational (선정 게이트 — 두 스토리가 공유하는 코어)

- [ ] T002 `search/config.py` 에 게이트 상수 추가: `cluster_anchor_gap`(env
      `ROBO_SEARCH_ANCHOR_GAP`, 스윕 후 확정값), `cluster_support_policy`(env, 기본=채택 정책).
      단일 진실 — retriever 에 숫자 리터럴 금지 (헌법 III).
- [ ] T003 `search/retriever.py::_retrieve_global_anchors` 앵커 선발을 상대 격차 게이트로
      변경(상한 3·하한 1, research.md D1) + support root/직행 자격을 정책 상수 연동(D2 —
      P0/P1/P2 를 정책 값으로 선택 가능하게, 분기 아닌 자격 산정 함수 1개). 코어 반환
      형태 불변.
- [ ] T004 `search/test_global_anchor.py` 게이트 단위 케이스 추가: 절벽(앵커 1개로 축소),
      촘촘(복수 유지·상한 3), 플랫 저점(하한 1), 정책별 root 자격. mock 코퍼스 기존 관례.
- [ ] T005 골드 전수 A/B 스윕 실행: Δ ∈ {0.05,0.08,0.10,0.12,0.15} × 정책 {P0,P1,P2}
      (`search/eval/anchor_ab_eval.py` 재사용, 스윕 러너는 scratchpad). 채택 기준 =
      recall 기준선 이상 중 결과 크기 최소. 결과 표·채택값을 `research.md` 말미에 기록,
      확정값을 T002 상수에 반영. **recall 하락 시 채택 금지(SC-001) — 정책 재조정 후 재스윕.**

**Checkpoint**: 선정 게이트 확정(골드 심판 통과) — 이후 US1/US2 병행 가능.

## Phase 3: User Story 1 — 유관한 것의 전문만 전달 (P1) 🎯 MVP

**Goal**: MCP 기본 응답 = 계층화 프로파일(앵커 전문·evidence 요약+코드·TABLE 컬럼).

**Independent Test**: 검증 질의 → 무관 도메인 함수 0 + V1~V7 전항 통과.

- [ ] T006 [US1] `api/mcp_app.py` 에 TABLE 컬럼 배치 조회 추가:
      `MATCH (t:TABLE)-[:HAS_COLUMN]->(c)` (rel_types.HAS_COLUMN 사용) →
      Column {name,dtype,is_primary_key,logical_name,description(analyzed>ddl>comment 순)}.
      쿼리 실패는 로그+오류 표면화(빈 [] 위장 금지), 컬럼 미수집 테이블만 정상 [].
- [ ] T007 [US1] `api/mcp_app.py` 에 `_tiered_clusters(clusters, corpus)` 변환기 구현
      (data-model.md 형태): 앵커=rules 전량+code_text, evidence=rules `[]`+code_text+
      evidence 근거, TABLE=columns 부착, search_text/element_id 제거, 동일 statement rule
      병합(examples 합집합·순서 보존). code_text 는 corpus by_id 재사용(신규 쿼리 0).
- [ ] T008 [US1] `cluster_retrieve` 의 `detail` 계약 갱신: `'tiered'` 기본,
      `'full'`/`'summary'` 존치(contracts/mcp-cluster-retrieve.md). docstring 에 응답=JSON
      텍스트 명시.
- [ ] T009 [P] [US1] `api/test_mcp_tiered.py` 신규: V1~V7 검증(고정 입력 dict → 변환기
      직접 호출, Neo4j 불필요 부분은 컬럼 조회 주입/모킹). unittest 관례.
- [ ] T010 [US1] 실API 확인: analyzer 재시작 후 검증 질의 → 무관 도메인 함수 0(SC-002),
      전 FUNCTION code_text·전 TABLE columns(SC-003). 결과를 baseline 과 diff 로 기록.

## Phase 4: User Story 2 — 한도 내 도달·끝까지 (P1)

**Goal**: CLI 도달 크기 ≤ 원내용 1.1배, 파일치환 없이 아키텍트 칩까지.

**Independent Test**: verify_052 e2e 에서 legacy_ref nodes>0(유관만)·파일폴백 미발동.

- [ ] T011 [US2] `api/mcp_app.py::cluster_retrieve` 반환형 `dict`→`str`:
      `json.dumps(payload, ensure_ascii=False, separators=(",",":"))`. 오류도 동일 형태
      `{"error":...}` 텍스트. 타입 힌트 `str` 로 변경(outputSchema 미생성 — research D3 주의).
- [ ] T012 [US2] 크기 실측: 검증 질의 tiered 응답의 CLI 도달 텍스트 크기 ≤ 원내용 1.1배
      (SC-004) — 직렬화 전/후 바이트 기록.
- [ ] T013 [US2] 아키텍트 e2e(계약 무변경 실증): analyzer→architect-api 순차 재시작(락
      주의·1회씩) → `verify_052.py` 실행 → SSE legacy_ref nodes>0·전부 유관·파일폴백
      미발동(architect 로그 provenance.file_fallback 무발생) 확인. 아키텍트 코드 변경 0 확인.
- [ ] T014 [US2] architect 웹(:15173) 최신 PRO 상세 `.lref-chip` 칩+팝오버 Playwright
      스크린샷 → 직접 눈으로 확인(SC-005). 산출물 `goal-verify/output/evidence/052-chip-*.png`.

## Phase 5: User Story 3 — Analyzer 검색 UI 무회귀 (P2)

- [ ] T015 [US3] Analyzer UI(:3000) 그래프 탭 의미검색 실행(동일 질의) — 결과 뭉치 표시·
      캔버스 매칭·콘솔 오류 0 실물 확인(SC-006). REST 이벤트 필드 diff 로 계약 불변 실증.

## Phase 6: Polish

- [ ] T016 회귀 일괄: `search/` 기존 unittest 전부 + `api/test_mcp_tiered.py` green,
      골드 A/B 채택값 재확인 1회. 임시 스윕 산출물은 scratchpad 에만(저장소 오염 금지).
- [ ] T017 문서 정합: research.md(스윕 표), contracts(확정값 각주), 이 tasks.md 체크·증거
      링크 갱신. memory todo 동기화.

## Dependencies

- T001 → T005·T010·T012 (기준선 대조)
- T002→T003→(T004, T005) ; T005 통과가 Phase 3/4 진입 게이트
- T006→T007→(T008, T009)→T010 ; T011→T012→T013→T014 (T011 은 T007 과 병행 가능 [P])
- T015 는 T003 배포(재시작) 후 언제든. T016·T017 마지막.

## Implementation Strategy

MVP = Phase 2 + Phase 3 (게이트+계층화 — 노이즈·전문성 해결). US2 직렬화는 독립 파일
경로라 T007 과 병행. e2e(T013·T014)가 최종 판정 — 표본 통과를 전체로 일반화하지 않고
골드 전수(T005)와 실물 UI(T014·T015)를 모두 증거로 남긴다.
