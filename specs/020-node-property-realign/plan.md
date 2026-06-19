# Implementation Plan: 노드 속성 정체화

**Branch**: `020-node-property-realign` | **Date**: 2026-06-19 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/020-node-property-realign/spec.md`

## Summary

코드노드가 단일 dataclass + 일괄 SET로 같은 속성 bag을 받아 생기는 빈값·중복·잡속성을 제거하고, **노드가 자기 종류에 의미있는 속성만** 갖게 한다. 핵심 = **"빈값 미저장"(`SET n += {비어있지 않은 키}`)** 단일 규칙 + 파생/중복 속성 폐기. signature→params·return_type, annotations+comment→`notes`, loc·god_flag·table_coupling·fan_in·fan_out 폐기(위험계산은 메모리 유지·`risk_level`만 저장), table_insights_json→컬럼 `analyzed_description`+R/W 엣지로 분배 후 폐기. 017·018·019 결과 위에 올린다.

## Technical Context

**Language/Version**: Python 3.11 (analyzer), TypeScript/Vue 3 (frontend)
**Primary Dependencies**: Neo4j, LangGraph, antlr AST(JSON) / Vue·NVL
**Storage**: Neo4j (매 run `DETACH DELETE` 후 재빌드)
**Testing**: 실제 파이프라인 재분석 → 노드 속성을 원문과 대조 (단위테스트 아님; 원칙 §6)
**Target Platform**: 분석 파이프라인(생산자) + 소비처(frontend 노드패널·그래프)
**Project Type**: 그래프 생산 파이프라인 + 다중 소비처 (속성 = 출력 contract)
**Performance Goals**: 결정론 단계 LLM 0(속성 저장·health·분배). 분석 LLM 단계 불변
**Constraints**: 빈값 미저장 단일규칙(분기 최소) / 그래프=진실(파생·중복 미저장) / 위험도 품질 불변 / 매 run 파괴적 재빌드 / 017·018·019 결과 유지
**Scale/Scope**: 생산자 nodes/*.py·health_metrics·table_insights 분배 + frontend 2파일(NodeDetailBase·NvlGraph). RWIS ~700노드

## Constitution Check

*GATE: Phase 0 전 통과, Phase 1 후 재확인.*

| 원칙 | 평가 |
|---|---|
| **I. 결정론 우선** | ✅ 속성 저장·health 계산·table_insights 분배 전부 결정론(LLM 0). 분석 LLM 단계 불변 |
| **II. 분기 0** | ✅✅ "빈값 미저장" **단일 규칙**으로 종류별 분기 제거(예외: health=루틴만 1곳). 오히려 분기 감소 |
| **III. 단일 진실** | ✅✅ 분석결과를 제자리(컬럼 `analyzed_description`·R/W 엣지)에 저장, 노드 중복 블롭 폐기. 저장 단일점(nodes/*.py `to_props`) |
| **IV. silent fail 금지** | ✅ table_insights 분배 매칭 실패 = 명시 로깅(유실 표면화). 빈값 미저장은 "없음"을 정직히 표현 |
| **V. KV 캐시 친화** | ✅ 프롬프트 system 본문 불변(저장/표시만 변경). 영향 없음 |
| **VI. 출력 스키마 절제** | ✅✅ 정확히 이 원칙 — 잡속성·중복 블롭 제거 = 스키마 절제 강화 |
| **VII. submit 도구** | ✅ 변경 없음 |
| **VIII. 응집 합본** | ✅ 노드/엣지 분리 유지. 저장 맵 빌더는 각 nodes/*.py 내 |
| **Cross-Service Contract** | ⚠️**핵심**: ↓ Output 노드 **속성** 변경(필드 제거·rename·분배) → frontend(NodeDetailBase·NvlGraph) 전수 정합. catalog는 `labels(n)`·`n{.*}` 무관통과(점검) |
| **Cost & Safety** | ✅ 예산 차단기·매 run wipe 불변 |

**Gate 결과**: 위반 없음. 단 **속성 contract 변경** → 소비처 전수 정합(아래 blast).

## Project Structure

### Documentation (this feature)

```text
specs/020-node-property-realign/
├── plan.md              # 이 파일
├── research.md          # Phase 0 — 결정(빈값미저장·signature·notes·health·table_insights 분배)
├── data-model.md        # Phase 1 — 종류별 속성 before→after
├── quickstart.md        # Phase 1 — 재분석 후 속성 원문대조 검증
├── contracts/
│   └── property-delta.md # Phase 1 — 노드 속성 contract 변경(소비처 약속)
└── tasks.md             # Phase 2 (/speckit-tasks)
```

### Source Code (blast radius)

```text
# 생산자 analyzer
data_models/graph_nodes.py                       # Function: signature 제거 / annotations+comment→notes / params·return_type 유지
shared/neo4j/nodes/function.py                   # to_item→to_props(비어있지않은 키만) + SET n += map. summary 적재도 동일
shared/neo4j/nodes/{variable,constant,data_type,module}.py  # 동일 "빈값 미저장" 패턴 적용
analysis_flow/step2_load_ast/antlr_json_reader.py # _make_function: signature 폴백 제거, notes 매핑
analysis_flow/step6_analyze_function/analyze_function_step.py # signature 의존 제거(있으면)
analysis_flow/step7_post_processing/health_metrics_step.py   # loc·god_flag·table_coupling·fan_in·fan_out SET 폐기, risk_level(+in_call_cycle)만, 루틴만. 계산은 유지
analysis_flow/step6_analyze_function/*(table_insights 적재) + shared/neo4j/nodes/{column,table}·edges/reads_writes  # tableInsights→컬럼 analyzed_description + R/W 엣지 분배, 코드노드 table_insights_json 폐기

# 소비처
robo-data-frontend/src/.../node-detail/NodeDetailBase.vue  # loc/god/table_coupling/fan_in/fan_out 표시 제거·signature→params/return·comment→notes·risk_level/in_call_cycle
robo-data-frontend/src/.../graph/NvlGraph.vue              # risk→risk_level
robo-data-catalog (점검)                                    # labels(n)·n{.*} 무관통과 가정, :속성 명시 참조 grep
```

**Structure Decision**: 생산자 저장 단일점(각 nodes/*.py의 속성맵 빌더)을 "빈값 미저장 `SET +=`"로 통일 + dataclass(graph_nodes) 필드 정리 + health/table_insights 적재 정합 + 소비처(frontend 2파일) 전수 정합. catalog는 라벨 무관 통과 점검.

## Complexity Tracking

> 위반 없음 — 비움. ("빈값 미저장" 단일 규칙은 분기를 늘리지 않고 줄인다.)
