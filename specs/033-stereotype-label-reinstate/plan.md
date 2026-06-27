# Implementation Plan: 역할(stereotype) 노드 라벨 재부여 + 위치기반 소비 계약

**Branch**: `refactor` | **Date**: 2026-06-27 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/033-stereotype-label-reinstate/spec.md`

## Summary

stereotype(역할)을 노드 라벨로 부여하는 공용 헬퍼 `shared/neo4j/nodes/stereotype_label.py::apply_stereotype_labels((id,stereotype)…)`를 **복원**(철회된 `d61f240`와 동일) — 값별로 묶어 `MATCH (n {id}) SET n:`<값>``(검증풀 통제값+백틱 방어, APOC 불요). `code_node.update_summaries`(step6 analyze_code 결과)·`module.upsert_modules`(step2 적재+step7 summarize 결과)에서 호출. 속성은 유지(무중단·회귀 0). 재결정의 핵심은 **소비 계약이 위치기반**(`labels[0]`=antlr 타입, `labels[1:]`=stereotype)이라는 것 — 하드코딩 역할집합 불필요. spec 032 supersede.

## Technical Context

**Language/Version**: Python 3.11 (async)

**Primary Dependencies**: Neo4j(드라이버 `Neo4jClient.run_batch_unwind`), LangGraph 파이프라인

**Storage**: Neo4j 그래프(노드 라벨 + 속성)

**Testing**: `py_compile` + 실데이터 검증(framework 재분석 또는 현재 그래프 마이그레이션 → `labels(n)` Neo4j 직접 쿼리)

**Project Type**: 분석 백엔드(single project)

**Constraints**: 동적 라벨이라 Cypher 파라미터 라벨 불가 → 값별 배치. 라벨 부여는 타입 라벨 등록(step2) **이후**(step6/step7)라 `labels[0]`=타입.

**Scale/Scope**: 변경 파일 3개(신규 1 + 호출부 2). 코드 ~10줄(d61f240 복원).

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **III. Single Source of Truth**: 라벨 부여 헬퍼 1곳(`stereotype_label.py`). stereotype 값 단일 출처=`coerce_stereotype`/`ontology_roles.yaml`. ✅
- **IV. No Silent Failure**: 빈 stereotype은 의도적 미부여(누락 아님, 명시적 조건). 배치 실행 실패는 `run_batch_unwind`가 raise. ✅
- **I. Determinism-First**: 라벨 부여는 LLM 0(순수 그래프 쓰기). stereotype 값 자체는 step6 LLM 산출(기존, 불변). ✅
- **VIII. Cohesion Over Convention**: 신규 `stereotype_label.py`는 라벨 쓰기 한 책임 — nodes/ 디렉토리 관례 부합. ✅
- 위반 없음 → Complexity Tracking 비움.

## Project Structure

### Documentation (this feature)

```text
specs/033-stereotype-label-reinstate/
├── spec.md              # 완료
├── plan.md              # 이 파일
├── research.md          # Phase 0 (라벨순서 근거·032 철회사유 무력화)
├── data-model.md        # Phase 1 (라벨 부여 동작·마이그레이션)
├── quickstart.md        # Phase 1 (검증 절차)
├── contracts/
│   └── stereotype-label-contract.md   # 소비처 위치기반 계약
├── checklists/requirements.md   # 완료
└── tasks.md             # /speckit-tasks 산출
```

### Source Code (repository root)

```text
shared/neo4j/nodes/
├── stereotype_label.py   # 신규(복원): apply_stereotype_labels((id,stereotype)…)
├── code_node.py          # update_summaries: import + apply_stereotype_labels 호출
└── module.py             # upsert_modules: import + apply_stereotype_labels 호출

analysis_flow/
├── step2_load_ast/load_ast_step.py        # (무변경) upsert_code_nodes/upsert_modules → 타입 라벨 먼저
├── step6_analyze_code/analyze_code_step.py # (무변경) update_summaries 호출
└── step7_post_processing/summarize_module_step.py # (무변경) upsert_modules 호출
```

**Structure Decision**: 단일 프로젝트. 생산자측 라벨 부여만(소비처 위치기반 이관은 후속 frontend spec). 라벨 부여 시점이 타입 라벨 등록 이후임은 기존 step 순서(step2→step6/7)로 이미 보장 — step 코드 변경 0.

## 위치기반 소비 계약 (contract 요지)

- `labels[0]` = 노드의 antlr 타입(종류). `labels[1:]` = stereotype(역할). 소비처는 **위치로** 분리(하드코딩 stereotype 집합 금지).
- 근거: 타입 라벨은 step2(load_ast)에서 등록, stereotype 라벨은 step6/step7에서 등록 → Neo4j `labels()` 토큰 생성순 = 타입 먼저.
- caveat: Neo4j 라벨순서 계약 비보장(토큰 id 순). 단 이 파이프라인은 안 깨짐. 최악=패널 1회 swap(경미).

## Complexity Tracking

> 위반 없음 — 비움.
