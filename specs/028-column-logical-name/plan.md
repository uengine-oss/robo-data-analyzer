# Implementation Plan: 컬럼 논리명(logical_name) 생성

**Branch**: `028-column-logical-name` (작업 브랜치 = refactor) | **Date**: 2026-06-26 | **Spec**: [spec.md](spec.md)

## Summary

테이블 논리명(spec 025)을 **컬럼에 미러링** — 분석기가 컬럼별 짧은 업무 논리명(`logical_name`)을 LLM으로 생성해 COLUMN 노드에 저장. 변경 3지점: ① **응답형식** `ColumnInsight`에 `logical_name` 필드 추가, ② **프롬프트** `prompts/{dbms,framework}/table_description.yaml` 둘 다 컬럼 논리명 요청(KV-캐시 byte-identity 유지), ③ **영속** `shared/neo4j/nodes/column.py`에 `logical_name` SET + `enrich_table_description_step`이 컬럼 논리명 운반. framework·dbms 동일(분기 0). 빈 논리명 미저장(025 규칙). 기존 컬럼속성 무회귀.

## Technical Context

**Language/Version**: Python 3.12
**Primary Dependencies**: Pydantic(출력 스키마), LangGraph 파이프라인, Neo4j, yaml 프롬프트(`yaml_llm_runner`)
**Storage**: Neo4j COLUMN 노드 (`shared/neo4j/nodes/column.py`)
**Testing**: 실제 분석 1회(framework·dbms 각) → COLUMN.logical_name 채움 확인(spec FR-006/SC-002). 기존 baseline/byte-identical 회귀.
**Project Type**: analysis pipeline (생산자)
**Constraints**: ① 두 전략 동일(분기 0) ② 빈 논리명 미저장 ③ 기존 컬럼속성 무회귀 ④ 프롬프트 시스템 prefix byte-identical 유지(KV 캐시) ⑤ 출력 스키마 절제(논리명만 추가, 불필요필드 X).
**Scale/Scope**: 소형 — 데이터모델 1필드 + 프롬프트 2파일 + 영속 1~2지점.

## Constitution Check

*GATE: Phase 0 전 통과. (분석기 실제 `.specify/memory/constitution.md` 기준.)*

| 원칙 | 평가 | 판정 |
|---|---|---|
| **I. Determinism-First** | 논리명=의미 산출 → LLM 적정(결정론으로 불가). 테이블 논리명과 동급. | **PASS** |
| **II. Zero-Branch Strategy** | 모델·영속은 **단일 선언 1곳**(ColumnInsight·column.py) — 전략 control-flow 분기 0. 프롬프트는 기존 전략별 파일 패턴(선언) — 둘 다 **동일 내용**. | **PASS** |
| **III. Single Source of Truth** | 컬럼 논리명 단일 출처 = ColumnInsight→enrich→COLUMN. | **PASS** |
| **IV. No Silent Failure** | 논리명 생성 실패는 "빈값 미저장"(설명 부재와 동일) — 단계 실패가 아니라 정상 부재. 단계 오류는 기존대로 raise. | **PASS** |
| **V. KV-Cache-Friendly Prompts** | table_description 프롬프트 system prefix **byte-identical 유지** 필수 → 논리명 요청을 **두 프롬프트에 동일 위치·동일 문구**로 추가. | **PASS(주의항목)** |
| **VI. Schema Restraint** | `usage`(소비0)는 뺐던 전례 — `logical_name`은 **표시로 소비됨**(테이블 논리명과 동일) → 추가 정당. 그 외 필드 추가 0. | **PASS** |

**결과: PASS.** 신규 라벨/관계 0, 분기 0, 스키마 최소 1필드. Complexity Tracking 불필요.

## Project Structure

### Documentation
```text
specs/028-column-logical-name/
├── spec.md · plan.md · research.md · tasks.md
└── checklists/requirements.md
```

### Source Code — 변경 대상
```text
data_models/code_analysis_output.py        # [핵심] ColumnInsight 에 logical_name 필드 추가
prompts/framework/code_table_insights.yaml       # [정정] 컬럼 의미 생성처(non-batch=framework). 컬럼 logical_name 요청
prompts/dbms/code_table_insights_batch.yaml      # [정정] 컬럼 의미 생성처(batch=dbms). 동일 문구(전략 불변)
# (옛 가정 table_description.yaml 은 테이블 레벨이라 오류 — 실제 컬럼 의미는 code_table_insights*)
shared/neo4j/nodes/column.py               # COLUMN MERGE/SET 에 logical_name (빈값 미저장)
analysis_flow/step7_post_processing/enrich_table_description_step.py  # 컬럼 logical_name 운반(현재 description/analyzed_description 만 분배)
```
**Structure Decision**: 테이블 logical_name(025) 경로를 그대로 미러 — 동일 enrich 스텝·동일 writer 패턴. 신규 모듈 0.

## Phase 0 / Phase 1
- Phase 0: [research.md](research.md) — 결정(미러 패턴·byte-identity·빈값규칙). NEEDS CLARIFICATION 0.
- Phase 1: data-model = ColumnInsight(+logical_name)·COLUMN(+logical_name). contracts/quickstart는 내부 파이프라인이라 quickstart(검증 시나리오)만.

## Complexity Tracking
> 분기 0·신규스키마 1필드·신규모듈 0 → 비움.
