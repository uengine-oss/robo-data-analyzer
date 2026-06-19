# Implementation Plan: 그래프 스키마 정합 — DBMS 흐름·구조 복원 + 관계 범용 통합

**Branch**: `018-graph-schema-realign` | **Date**: 2026-06-18 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/018-graph-schema-realign/spec.md`

## Summary

refactor 가 id 통일 과정에서 잃은 DBMS 그래프 핵심(구문 흐름 NEXT, 선언부 노드)을 복원하고, 두 전략(framework/dbms)의 관계·라벨을 **"같은 뜻=같은 이름, 종류 차이는 노드 라벨"** 원칙으로 통합한다. 식별자 id 통일은 유지. 관계명 통합은 **출력 그래프 스키마(contract) 변경**이라 소비처(frontend·catalog·architect) 전수 정합을 동반한다(회귀 0).

기술 접근: ① 관계는 `rel_types.py` 단일 정의처에서 통합/추가(HAS_MEMBER·BELONGS_TO·NEXT 의 구문용도) ② AST reader(`antlr_json_reader._read_dbms`)에서 NEXT 생성·DECLARE 노드 보존·HAS_MEMBER 를 루틴에만 ③ 구조노드 라벨 단일 맵 ④ 룰 생성 단위/통째-우선 ⑤ 소비처 23파일 정합.

## Technical Context

**Language/Version**: Python 3.11 (analyzer·catalog·architect), TypeScript/Vue 3 (frontend 소비처)
**Primary Dependencies**: LangGraph, Neo4j, antlr AST(JSON 입력), Pydantic
**Storage**: Neo4j (매 run `DETACH DELETE` 후 재빌드)
**Testing**: 실제 파이프라인 실행 → 그래프를 원문 소스와 line-by-line 대조 (단위테스트 아님; constitution Cost&Safety + 원칙 §6)
**Target Platform**: 분석 파이프라인(그래프 생산자) + 소비처(frontend NVL·catalog read·architect ingestion)
**Project Type**: 그래프 생산 파이프라인 + 다중 소비처 (멀티서비스 — 변경이 contract 면 ripple)
**Performance Goals**: 결정론 단계는 LLM 0 (NEXT/PARENT_OF/구조노드/링킹). 룰 생성만 LLM
**Constraints**: 회귀 0 / `rel_types.py` 단일 진실 / KV 캐시 byte-identical / 매 run 파괴적 재빌드 / 예산 차단기 유지
**Scale/Scope**: RWIS 기준 ~700 노드·30 라벨. 변경 영향 ~23 파일(4 영역)

## Constitution Check

*GATE: Phase 0 전 통과 필수, Phase 1 후 재확인.*

| 원칙 | 평가 |
|---|---|
| **I. 결정론 우선** | ✅ NEXT·PARENT_OF·구조노드·HAS_MEMBER 전부 결정론(LLM 0). 룰 생성(LLM)은 기존 스텝 유지 |
| **II. 분기 0 전략** | ✅ NEXT 생성은 dbms **추출기**(`_read_dbms`) 책임 — `graph_builder` StateGraph 무분기 유지. 전략 차이는 reader 함수 분리(=언어 추출기), 제어흐름 if 아님 |
| **III. 단일 진실** | ✅✅ 관계명 전부 `rel_types.py` 한 곳에서 통합/정의. 구조노드 라벨도 단일 맵 한 곳. 강화됨 |
| **IV. silent fail 금지** | ✅ DECLARE 노드 복원 = 누락 표면화. 구조노드는 명시적으로 "분석 제외 but 노드 유지" |
| **V. KV 캐시 친화** | ⚠️ 룰 프롬프트(통째/계층) 변경 시 system 본문 byte-identical 유지 — Phase 1 contract 에 명시 |
| **VI. 출력 스키마 절제** | ✅ 룰/예시/질문은 기존 평탄 스키마 유지. 추가 중첩 없음 |
| **VII. submit 도구** | ✅ 변경 없음 |
| **VIII. 응집 합본** | ✅ edges/ 분리 유지. NEXT 엣지 = `edges/next.py`(또는 기존 rule_flow 와 구분) |
| **Cross-Service Contract** | ⚠️**핵심**: ↓ Output 그래프 스키마(노드 라벨·엣지 타입) **변경됨** → architect·catalog·frontend **전수 정합 필수**(아래 blast radius). constitution 이 요구하는 "contract 변경 시 downstream 플래그"를 research.md 에 명문화 |
| **Cost & Safety** | ✅ 예산 차단기·매 run wipe·단일 run 정책 불변 |

**Gate 결과**: 위반 없음(Complexity Tracking 불요). 단 **contract(출력 스키마) 변경**이라 소비처 동시 정합이 필수 조건 — Phase 0/1 에서 전수 처리.

## Project Structure

### Documentation (this feature)

```text
specs/018-graph-schema-realign/
├── plan.md              # 이 파일
├── research.md          # Phase 0 — blast radius 전수조사 + 옛 main 대조 + 결정
├── data-model.md        # Phase 1 — 최종 노드/관계 스키마
├── quickstart.md        # Phase 1 — 검증 시나리오(재분석→원문대조)
├── contracts/
│   └── graph-schema.md  # Phase 1 — 출력 그래프 contract(소비처 약속)
└── tasks.md             # Phase 2 (/speckit-tasks — 본 명령 아님)
```

### Source Code (영향 범위 — blast radius)

```text
# 생산자 (analyzer) — 핵심 변경
robo-data-analyzer/
├── shared/neo4j/rel_types.py                      # 관계 통합/추가(단일 진실)
├── shared/neo4j/edges/{has_function,has_variable,belongs_to_package}.py  # → has_member, belongs_to 로 통합
├── shared/neo4j/edges/next.py                      # (신규/복원) 구문 흐름 NEXT
├── data_models/graph_edges.py · graph_nodes.py     # dataclass 정합
├── analysis_flow/step2_load_ast/antlr_json_reader.py  # NEXT 생성·DECLARE 노드보존·HAS_MEMBER 루틴한정·그릇라벨 정체화
├── analysis_flow/step2_load_ast/load_ast_step.py   # 엣지 영속 정합
├── analysis_flow/step6_analyze_function/*           # 룰 단위/통째-우선(batch_analyze·range_body) + 구조노드 처리
└── shared/lang/nodeClassify 또는 상수               # 구조노드 라벨 단일 맵

# 소비처 (전수 정합 — 회귀 0)
robo-data-frontend/src/                # ~12파일: nodeRelationships·navigator·ModuleCard·PackageCard·FileCard·DatatypeCard·graphDisplay·graphStyles·GraphTab·graphCanvas·MultiLayerOntologyViewer
robo-data-catalog/service/schema_query_service.py   # Cypher 관계명 정합
robo-architect/api/features/ingestion/hybrid/mapper/{rule_context,agent_validator}.py  # 관계명 정합
```

**Structure Decision**: 단일 생산자(analyzer) 변경 + 다중 소비처 정합. 생산자는 `rel_types.py` 단일 진실 + reader 추출기 수정. 소비처는 관계명 변경분만 정합(라벨 필터로 종류 구분 전환). architect 내 analyzer 사본(submodule)은 생산자 변경 propagate 후 서브모듈 bump 로 동기화.

## Complexity Tracking

> 위반 없음 — 비움.
