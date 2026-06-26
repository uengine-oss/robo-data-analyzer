# Tasks: 컬럼 논리명(logical_name) 생성

**Feature**: 028-column-logical-name | **Input**: [spec.md](spec.md)·[plan.md](plan.md)·[research.md](research.md)

**제약**: 두 전략 동일(분기0) · 빈값 미저장 · 기존 컬럼속성 무회귀 · 프롬프트 byte-identity 유지.

## Phase 1: 응답형식 (스키마)

- [ ] T001 `data_models/code_analysis_output.py` `ColumnInsight`에 `logical_name: str = Field("", description="컬럼 도메인 표시명 한 줄 (코드 식별자·DDL 타입 X / 동어반복 X)")` 추가. (그 외 필드 0 — Constitution VI.)

## Phase 2: 프롬프트 (두 전략 동일) — ★정정: 컬럼 의미 생성처 = code_table_insights*(table_description 아님)

- [ ] T002 [P] `prompts/dbms/code_table_insights_batch.yaml` — 컬럼 출력 항목에 **논리명(logical_name, 짧은 업무명)** 요청 + 예시 추가(batch=dbms 경로).
- [ ] T003 [P] `prompts/framework/code_table_insights.yaml` — T002와 **동일 문구·동일 위치**(전략 불변, non-batch=framework 경로). 두 파일 변경부 동일.
- (참고: 나머지 2개 조합 dbms/code_table_insights.yaml·framework/code_table_insights_batch.yaml 은 **존재하지 않음** — dbms=batch / framework=non-batch 단일 경로.)

## Phase 3: 영속 + 운반

- [ ] T004 `shared/neo4j/nodes/column.py` — COLUMN 영속에 `logical_name` 추가. **빈값 미저장**(테이블 `SET_ANALYZED` 패턴: 비어있지 않을 때만 SET). 기존 속성 SET 불변.
- [ ] T005 `analysis_flow/step7_post_processing/enrich_table_description_step.py` — 컬럼 합성 시 `logical_name` 운반(현재 description/analyzed_description만). 여러 관점이면 단일값 수렴.
- [ ] T006 데이터모델↔영속 흐름 점검: ColumnInsight.logical_name → enrich → column writer item → COLUMN.logical_name 키 일관(이름 동일).

## Phase 4: 검증 (실제 분석)

- [ ] T007 **dbms** 전략 분석 1회 → COLUMN.logical_name 채움률·빈값0·코드식별자아님 확인(SC-002/SC-003).
- [ ] T008 **framework** 전략 분석 1회 → 동일 확인(전략 불변 FR-002/SC-002).
- [ ] T009 회귀: 기존 컬럼속성(name/description/analyzed_description/타입) 불변(SC-004) + 프롬프트 byte-identity(baseline/check) 무회귀.

## Dependencies
- T001(스키마) → T002/T003(프롬프트, 스키마 필드명 일치) → T004/T005(영속·운반) → T006(점검) → T007~T009(검증).
- T002·T003 [P] 가능하나 **동일 내용** 보장(diff 대조).

## MVP
T001~T006 = 컬럼 논리명 생성·저장 완성. T007~T009 = 라이브 검증.
