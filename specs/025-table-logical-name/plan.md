# Implementation Plan: 테이블 논리명 부여 + 설명·요약 비즈니스 품질

**Branch**: `refactor` (spec dir `025-table-logical-name`) | **Date**: 2026-06-26 | **Spec**: [spec.md](spec.md)

## Summary

테이블에 logical_name(짧은 도메인 표시명) 부여 — 코드노드 패턴을 테이블로 확장. 테이블 설명 생성기가 논리명+문단을 함께 산출·저장, 프론트가 논리명 표시(물리명 병기). 요약/설명 프롬프트는 예시를 비즈니스-이벤트형으로 손질 + "기계어보다 업무이벤트 우선" 지침 — 단 규칙(값보존·날조금지)은 불변, 공통 system 바이트동일 유지(KV 캐시).

## Technical Context

**Language/Version**: Python(analyzer, refactor) + Vue3(frontend) · LLM frentis-ai-model · Neo4j
**Constraints**: 전략무관 · KV캐시(공통 system diff0) · 빈값미저장 · 추측금지 규칙 유지
**Scale**: RWIS 16테이블. ★요약 변경 = 임베딩검색(별도) 입력 → 재측정 동반.

## Constitution Check

| 원칙 | 준수 | 근거 |
|---|---|---|
| I. 결정론 | ✅ | 논리명/요약은 기존 LLM 단계 내 산출(신규 LLM 호출 없음 — table_description·code_summary에 필드/지침만). |
| II. 분기0 | ✅ | dbms·framework 프롬프트 동일 패턴(전략 if 없음). |
| III. id 단일 | ✅ | id 불변(이 작업은 logical_name 속성 추가만). |
| IV. 빈값미저장 | ✅ | 빈 logical_name 미저장(spec 024 nonempty 재사용). |
| VIII. KV캐시 | ✅ | 공통 `_common.yaml` system 불변, 분할고유 user_template_specific만 수정 → byte-identical 유지. |

**게이트 통과**: 위반 0. ★요약 문체 변경의 검색 영향은 SC-005 재측정으로 관리.

## 블래스트 (수정 지점 — 사실)
- `data_models/code_analysis_output.py:243` TableDescription → `logical_name` 필드 추가
- `data_models/graph_nodes.py` Table → `logical_name` 필드 추가
- `prompts/dbms|framework/table_description.yaml` → 논리명 산출 지침 + 출력필드
- `prompts/dbms|framework/code_summary_batch.yaml` user_template_specific → 예시 비즈니스화 + 지침 1줄(공통 system 불변)
- `analysis_flow/step7_post_processing/enrich_table_description_step.py` → table_updates에 logical_name 포함
- `shared/neo4j/nodes/table.py` to_item + MERGE_QUERY → logical_name SET(nonempty 적용됨)
- `robo-data-frontend` TableCard.vue → 논리명 표시(물리명 병기)

## Project Structure
```
specs/025-table-logical-name/  plan·research·data-model·quickstart·contracts·tasks
[수정] analyzer(Pydantic·graph_nodes·프롬프트·step7·table.py) + frontend(TableCard.vue)
```
**Structure Decision**: 기존 구조. logical_name = 코드노드 logical_name과 동일 패턴. 새 단계/LLM 호출 없음.

## Complexity Tracking
> 위반 없음 — 비움.
