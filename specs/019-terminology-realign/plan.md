# Implementation Plan: 용어 정체화 — framework 중심 노드/타입/로그 용어 제거

**Branch**: `019-terminology-realign` | **Date**: 2026-06-18 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/019-terminology-realign/spec.md`

## Summary

framework 용어를 ① 그릇 노드 라벨(MODULE→실제 종류 FILE/CLASS, MODULE은 역할만) ② 분석단위 코드 용어(`Function`→`CodeNode`) ③ 로그/스트림 표시(고정 "FUNCTION"→실제 종류)에서 걷어낸다. 017(식별자)·018(관계명)에 이은 framework 부채 3물결. 종류 출처 = 기존 온톨로지(단일 진실). 라벨·용어 변경은 출력 contract 변경이라 소비처(frontend·catalog·architect) **원자 정합**(회귀 0). 018 US4(MODULE→FILE/CLASS) 흡수.

기술 접근(3 갈래, 독립 검증):
- **A. 그릇 라벨**(US1): `nodes/module.py` 라벨 = 실제 종류 **단일**(`sub_label or FILE`, MODULE 폐기) / `schema_constraints` 타입별 uniq / frontend 가 그릇을 **타입집합으로 식별**(nodeClassify·templateRouter·navigator·graphStyles) 동시.
- **B. 로그/스트림**(US2): `analyze_function_step` node_event·flow label 하드코딩 "FUNCTION" → 실제 노드 종류. `embed_step`·`function.py` 표시 정합.
- **C. 코드 용어**(US3): `Function`→`CodeNode` 외 `function_*`/`functions`/`analyze_function`/`upsert_functions` 종류중립 개명(대규모, ★실제 루틴 의미 `:FUNCTION`/`:PROCEDURE` 라벨·루틴판별은 유지).

## Technical Context

**Language/Version**: Python 3.11 (analyzer·catalog·architect), TypeScript/Vue 3 (frontend)
**Primary Dependencies**: LangGraph, Neo4j, antlr AST(JSON), Pydantic / Vue·NVL
**Storage**: Neo4j (매 run `DETACH DELETE` 후 재빌드)
**Testing**: 실제 파이프라인 실행 → 그래프·로그를 원문과 대조 (단위테스트 아님; constitution Cost&Safety + 원칙 §6)
**Target Platform**: 분석 파이프라인(생산자) + 소비처(frontend NVL·catalog read·architect ingestion)
**Project Type**: 그래프 생산 파이프라인 + 다중 소비처 (라벨 변경 = contract ripple)
**Performance Goals**: 결정론 단계 LLM 0(라벨·종류·로그). 분석 LLM 단계 불변
**Constraints**: 회귀 0 / 종류 출처 = 온톨로지 단일진실 / 표시-영속 일치 / KV 캐시 byte-identical / 매 run 파괴적 재빌드
**Scale/Scope**: 코드 용어 개명 = analyzer 광범위(Function/function_* 다수 파일) + frontend 라벨처리 ~8곳 + catalog/architect 라벨 grep. RWIS ~700노드·30라벨

## Constitution Check

*GATE: Phase 0 전 통과, Phase 1 후 재확인.*

| 원칙 | 평가 |
|---|---|
| **I. 결정론 우선** | ✅ 라벨·종류·로그 표시 전부 결정론(LLM 0). 분석 LLM 스텝 불변 |
| **II. 분기 0 전략** | ✅ 종류 라벨은 온톨로지 도출(데이터). reader 추출기 분리 유지 — 제어흐름 if 아님. rename 은 분기 무추가 |
| **III. 단일 진실** | ✅✅ 종류 출처 = `ontology_roles.yaml`(이미 단일). MODULE 라벨 작성 단일점(`nodes/module.py`). 로그 label 도 노드 실제종류 1곳서 도출하도록 강화 |
| **IV. silent fail 금지** | ✅ 표시-영속 불일치 제거(누수 표면화). 종류 미상은 명시(외부 stub) |
| **V. KV 캐시 친화** | ⚠️ 프롬프트 system 본문에 "function/함수" 단어가 있으면 byte-identical 유지 필요 — research 에서 프롬프트 텍스트 변경 여부 확정(코드 식별자 개명은 프롬프트 무영향이어야) |
| **VI. 출력 스키마 절제** | ✅ 노드 라벨 의미만 정체화, 새 속성·중첩 없음 |
| **VII. submit 도구** | ✅ 변경 없음 |
| **VIII. 응집 합본** | ✅ 노드/엣지 저장 분리 유지. 개명은 구조 불변 |
| **Cross-Service Contract** | ⚠️**핵심**: ↓ Output 그래프 라벨(MODULE→FILE/CLASS)·스트림 이벤트 종류 **변경** → frontend·catalog·architect **전수 정합**(아래 blast). research 에 명문화 |
| **Cost & Safety** | ✅ 예산 차단기·매 run wipe·단일 run 정책 불변 |

**Gate 결과**: 위반 없음(Complexity Tracking 불요). 단 **contract(라벨·로그 종류) 변경** + **KV 캐시(프롬프트 텍스트)** 가 조건 — Phase 0/1 에서 확정·전수 처리.

## Project Structure

### Documentation (this feature)

```text
specs/019-terminology-realign/
├── plan.md              # 이 파일
├── research.md          # Phase 0 — blast radius 전수 + 결정(CodeNode·MODULE역할·로그·프롬프트)
├── data-model.md        # Phase 1 — 라벨/용어 before→after 매핑
├── quickstart.md        # Phase 1 — 검증(재분석→그래프·로그 원문대조)
├── contracts/
│   └── output-delta.md  # Phase 1 — 출력 라벨·스트림 종류 contract 변경(소비처 약속)
└── tasks.md             # Phase 2 (/speckit-tasks)
```

### Source Code (blast radius)

```text
# 생산자 analyzer
shared/lang/ontology_roles.yaml · ontology.py        # 종류 도출 단일 진실 (role→label "function"→"FUNCTION" 등) — 검토
shared/neo4j/nodes/module.py                          # 그릇 라벨 = 실제 종류 단일(:FILE/:CLASS, MODULE 폐기)
shared/neo4j/nodes/function.py                         # 라벨 작성(primary_label) — 개명 영향
shared/neo4j/schema_constraints.py                     # uniq_module → 종류 라벨 정합
analysis_flow/step2_load_ast/antlr_json_reader.py      # reader: DBMS sub=FILE, primary_label 기본값, _ROUTINE_LABELS
data_models/graph_nodes.py                             # Function dataclass → CodeNode (★대규모 파급)
shared/workflow/state.py · artifact_writer.py · logger.py  # state 키 functions→code_nodes, 카테고리·로그 맵
analysis_flow/step6_analyze_function/analyze_function_step.py  # ★로그 node_event/flow label 하드코딩 "FUNCTION" → 실제 종류
analysis_flow/step7_post_processing/embed_step.py·summarize_module_step.py  # label 표기·정합
analysis_flow/**(analyze_function step·function_* 식별자 전반)        # 종류중립 개명

# 소비처 (전수 정합 — 회귀 0)
robo-data-frontend/src/**: nodeClassify(isContainerNode 타입집합·badge)·templateRouter(FILE/CLASS 라우팅)·navigator(타입집합으로 그릇 식별·트리)·graphStyles(색/크기/PRIMARY_LABELS)·analyzer.ts(PRIMARY_LABELS)·graphDisplay
robo-data-catalog/service/schema_query_service.py     # 라벨 참조(MODULE 등) grep
robo-architect/api/features/ingestion/**              # 라벨 참조 grep
```

**Structure Decision**: 생산자 종류 출처(온톨로지) 단일점 + 라벨 작성부(nodes/module·function) + 로그(analyze_function_step) 정합 + `Function`→`CodeNode` 전역 개명. 역할 라벨(FUNCTION/MODULE) 저장 폐기 → 소비처는 **타입에서 역할 파생**(navigator·nodeClassify·templateRouter·embed Cypher 전수). architect 내 analyzer 사본은 생산자 변경 후 서브모듈 bump.

## Complexity Tracking

> 위반 없음 — 비움. (코드 용어 개명은 광범위하나 기계적 rename + 회귀검증, 아키텍처 복잡도 추가 아님.)
