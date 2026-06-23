# Quickstart: 정정 검증 가이드

목적: "CALL→대상 저장 + proc/모듈 파생 + 무회귀"를 실제 실행으로 검증(원칙 §6 — 코드리뷰가 아니라 데이터를 돌려 본다).

## 전제

- antlr-code-parser가 DBMS 샘플을 파싱해 `data/analysis/`에 AST JSON 산출 완료.
- Neo4j 기동, analyzer env 설정(`shared/config/env_catalog.py`).
- 비교용 baseline: 정정 **이전** 커밋으로 동일 샘플 1회 분석해 그래프/리포트 보관.

## 실행

1. analyzer 풀 분석 실행(진입점 `main.py` / `cli_scripts/`). 매 실행은 그래프 DETACH DELETE 후 전량 재빌드.
2. 산출물: Neo4j 그래프 + `logs/.../health_report.json` + 컴포넌트(`component_id` 속성).

## 검증 시나리오 (SC 매핑)

### V1 — CALL→대상 저장 (US1 / SC-001·002)
- DBMS 그래프 덤프에서 `CALLS` 엣지를 원문과 대조:
  - 모든 internal `CALLS`의 출발 노드가 `CALL` 종류인가? (SC-002 = 100%)
  - 출발이 프로시저 루트인 `CALLS`가 0건인가? (SC-001)
  - 알려진 호출(예: `PRC_X`가 `PRC_Y` 호출)이 `PRC_X의 CALL 노드 → PRC_Y`로 보이는가?
- 재귀: 자기 호출이 `CALL 노드 → 자기 프로시저`로 1건 존재(자기루프 누락 아님, FR-008).

### V2 — 빌트인 제외 (US3 / SC-005)
- `DBMS_`/`UTL_`/`SYS_`/`ORA_`/`OWA_`/`STANDARD` 접두 콜리에 대한 `CALLS`·대상 노드 0건.

### V3 — 컴포넌트 parity (US2 / SC-003)
- baseline vs 정정 후: 컴포넌트 개수·멤버 구성 동등. 특히 컴포넌트가 0개로 붕괴하지 않음.

### V4 — 건강 parity (US2 / SC-004)
- baseline vs 정정 후: `health_report.json`의 risk 분포·in_cycle 수 동등. 표본 프로시저의 fan_in/fan_out 동일.

### V5 — FRAMEWORK 무변경 (US3 / SC-006)
- FRAMEWORK 샘플 분석: `CALLS` 엣지 집합이 baseline과 완전 동일(함수↔함수 그대로).

### V6 — 프론트 모듈간 보기 무회귀 (SC-007)
- 프론트 모듈간 상호작용 보기에서 모듈↔모듈 호출 엣지가 baseline과 동등(`resolveModule` 해소로 끝점 의미 변경에 강건).
- ★프론트 UI는 내가 직접 자가검증(풀스택 기동 + Playwright 헤드리스 스크린샷 Read).

## 합격 기준

V1~V6 전부 통과 + 그래프 line-by-line 원문 대조 에러 0. 하나라도 불일치면 AST·LOGS·그래프 3단 추적 후 근본 수정.
