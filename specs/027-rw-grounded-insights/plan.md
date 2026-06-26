# Implementation Plan: 027-rw-grounded-insights

**Spec**: [spec.md](./spec.md) · **Branch**: `refactor` · **Created**: 2026-06-26

## Summary
step6 컨텍스트를 결정론 R/W 사실에 그라운드: ① R/W 테이블의 **컬럼 목록을 컨텍스트에 주입**(+프롬프트로 빠짐없이 서술), ② 테이블 귀속을 **`ctx.reads∪writes`로 제한**(callee 함수명 오귀속 차단, +프롬프트로 callee≠table). dbms·framework 공통. 분류/매칭 코드·스키마 불변.

## Clarifications (자율 결정, 2026-06-26)
- 컬럼 주입 형식: 테이블 컨텍스트 문자열에 `| 컬럼: c1, c2, …` 추가(저토큰, 렌더러 threading 불변).
- 제약 시행: per-함수 `em.affects_table`/`table_insights` 의 table 이 `reads∪writes`(bare name) 밖이면 **드롭+경고**. 적용 위치 = three_split/batch 의 per-함수 결과 조립부(ctx 가용).
- 컬럼 권위 출처: `state["columns"]`(DDL 파싱) → `{table_fqn: [col_name]}`.

## Technical Context
- Python/LangGraph. 변경 = (a) 컨텍스트 구성, (b) 프롬프트 4종, (c) per-함수 출력 필터. LLM 호출 구조·스키마 불변.
- 검증 = 재분석 1회 + Neo4j 대조(컬럼 채움·가짜 테이블 0) + 분류기 회귀.

## Constitution Check
| 원칙 | 판정 | 근거 |
|---|---|---|
| I. Determinism-First | ✅ **강화** | 테이블 귀속을 결정론 reads/writes로 제한, 컬럼도 DDL(결정론) 주입 |
| II. Zero-Branch | ✅ | 전략 분기 추가 없음(공통 렌더러·공통 필터) |
| III. Single Source of Truth | ✅ **강화** | 테이블·컬럼 권위 = 결정론 R/W·DDL (LLM 자유텍스트 아님) |
| IV. No Silent Failure | ✅ **강화** | reads/writes 밖 테이블 드롭 시 경고(FR-005) |
| V. KV-Cache | ✅(주의) | 컬럼 목록=user_values 가변부(이미 per-call). 시스템 본문 지시 추가=1회성 리셋만. 공유 prefix 바이트동일 유지 확인(Phase0) |
| VI. Schema Restraint | ✅ | 출력 스키마 필드 추가 0 |
| VII. Submit-Tool Wrap | ✅ | 기존 submit 구조 유지 |
**결과: PASS** (위반 0).

## 영향도 (Impact / Blast Radius)
**변경 파일(생산 측):**
- `analysis_flow/step6_analyze_code/analyze_code_step.py` — `_build_table_contexts`(+컬럼 목록), 호출부에 `state["columns"]` 전달.
- `analysis_flow/step6_analyze_code/three_split_llm_fanout.py` — per-함수 affects_table/table_insights 를 reads∪writes로 필터(framework).
- `analysis_flow/step6_analyze_code/batch_analyze.py` — 동 필터(dbms batch).
- 프롬프트: `prompts/framework/code_table_insights.yaml`·`prompts/dbms/code_table_insights_batch.yaml`(컬럼 서술 지시) / `prompts/framework/code_rules_examples.yaml`·`prompts/dbms/code_rules_examples_batch.yaml`(callee≠table·AFFECTS_TABLE 직접 R/W만).

**불변(영향 없음):** `build_context_step`(reads/writes/callees 이미 생성), `shared/lang/call_classifier`, `shared/neo4j/edges/affects_table`(MATCH 기반 upsert), fqn, Neo4j 스키마/라벨/관계.

**다운스트림 소비처:** catalog `/robo/graph`·architect ingestion 이 AFFECTS_TABLE·COLUMN.analyzed_description 소비 → **개선만**(가짜 엣지↓·컬럼 채움↑), 계약/형태 불변 = breaking 없음.

**운영:** 변경 반영엔 **재인제스천 1회**(logs/ 초기화 전체 재빌드, 헌법). spec 025 WIP와 파일 비충돌.

**리스크:** 컬럼 많은 테이블의 컨텍스트 비대(토큰↑) → 필요 시 상한. 필터가 정당한 R/W를 과하게 자르지 않도록 bare-name 매칭 기준 검증.

## Phase 0 Research (research.md)
- 컨텍스트 현황: reads/writes=테이블명만, table_descriptions=prose(+샘플), 컬럼 목록 부재(확정).
- affects_table 누적 경로: per-함수 em.affects_table → state → upsert(MATCH). 필터는 per-함수 조립부.
- KV-cache: 컬럼은 가변부, 시스템 지시 변경은 1회 리셋.

## Phase 1 Design
- data-model: 신규 노드/스키마 0. 컨텍스트 문자열에 컬럼 목록 텍스트만 추가.
- quickstart: 재분석 1회 → COLUMN.analyzed_description 채움 + 가짜 AFFECTS_TABLE 0 + 분류기 회귀 0.

## Complexity Tracking
해당 없음 (위반 0).
