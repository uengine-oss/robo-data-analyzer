# Implementation Plan: 실시간 분석 진행 스트림 + 전략무관 일관 보기모드

**Branch**: `refactor` (analyzer) + `main` (frontend) | **Date**: 2026-06-19 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/022-realtime-stream-views/spec.md`

## Summary

분석 중 비-LLM 시스템 step(DDL·AST·링킹)이 끝나는 즉시 그 산출물(노드·관계)을 화면 스트림으로 내보내고, 콘솔에 진행 message를 실시간 흘리며, 분석 후 보기모드(영역/건강/모듈간)가 전략 무관하게 대량 그래프를 제대로 배치해 보이게 한다.

**근본원인(전수조사 확정)**: ① `node_event`/`relationship_event`는 step6(LLM) `_emit_code_graph`만 emit — 골격 step1~5는 Neo4j 저장만 → 골격은 분석 후 한 방에만 등장. ② analyzer step이 `message`를 안 쏨 → 콘솔 멈춤. ③ `GraphTab.vue:graphStats`가 `graphEvents`(이벤트피드) 개수를 셈 → 실수와 불일치. ④ `NvlGraph.vue` NVL 레이아웃 `physics.enabled=false`(+`updateLayoutOnChange:false`)가 대량 그래프 주입 시 자동배치를 막아 보기모드가 빈 화면/뭉침.

**접근**: step6의 emit 형식을 **공통 헬퍼**로 추출해 각 골격 step이 완료 시 자기 델타를 emit(DRY·전략 무관·분기 0). step별 message emit. 프론트는 카운트 기준 교체 + NVL 레이아웃을 프로토타입(`doc/영역보기-NVL위에-D3오버레이-reference.html`)처럼 정상 forceDirected로 통일.

## Technical Context

**Language/Version**: Python 3.11 (analyzer) · TypeScript 5 / Vue 3 (frontend)

**Primary Dependencies**: analyzer = FastAPI · LangGraph · Neo4j driver · `NDJSONStream`. frontend = Pinia · `@neo4j-nvl/base` · D3(오버레이는 자체 canvas)

**Storage**: Neo4j (분석 그래프 — 변경 없음, 표시/스트림만 다룸)

**Testing**: analyzer = 재분석 CLI(`cli_scripts/analyze_data_dir.py`) + 그래프 쿼리 대조. frontend = Playwright 헤드리스 스샷(자가검증 하니스).

**Target Platform**: 데스크톱(Electron 임베드) + 브라우저(dev 3000). 단일 입구 gateway 9000.

**Project Type**: web (analyzer 백엔드 + frontend) — cross-service capability, 계약 = NDJSON 이벤트 스트림.

**Performance Goals**: 첫 시스템 step 완료(수 초) 내 화면 채워짐 시작. 라이브 emit이 분석 총시간을 유의하게 늘리지 않음(emit = 큐 put, 즉시 반환).

**Constraints**: NDJSON 이벤트 타입 **불변**(새 타입 추가 0) — 기존 타입을 더 이른 step에서 더 많이 emit할 뿐. Neo4j 스키마·노드 라벨/속성 모델(017/019/020) 불변.

**Scale/Scope**: RWIS(dbms) 9파일 ~1100노드/2300관계 기준. framework 샘플 1회 교차검증.

## Constitution Check

*GATE: Must pass before Phase 0. Re-check after Phase 1.*

| 원칙 | 준수 여부 |
|---|---|
| **I. Determinism-First** | ✅ 골격 emit은 결정론(LLM 0). emit은 이미 만들어진 노드/관계를 내보낼 뿐. |
| **II. Zero-Branch Strategy** | ✅ emit은 각 step 내부 또는 공통 헬퍼 호출 — `graph_builder`에 strategy `if` 추가 0. step 구성이 전략별로 다른 건 기존 선언(`analysis_pipelines.py`)에 이미 반영, 헬퍼는 전략 무관. |
| **III. Single Source of Truth** | ✅ rel_type은 `rel_types.py` 사용. emit 형식은 **단일 헬퍼**(step6 `_emit_code_graph`도 이 헬퍼로 리팩터 → 중복 0). |
| **IV. No Silent Failure** | ✅ emit 실패는 표면화. 빈 델타는 정상(0건 message). step 자체 실패 정책 불변. |
| **V~VIII** | ✅ 프롬프트·스키마·submit·응집 영역 무관(본 변경은 표시/스트림 only). |

**Cross-Service Contracts**: 변경되는 계약 = **frontend↔analyzer NDJSON 이벤트 스트림**. 단 **이벤트 타입/형식 불변**(node_event/relationship_event/message/phase_event/processing_event 그대로) → 다른 서비스(antlr·architect·catalog) 영향 0. architect로 가는 Neo4j 스키마 계약 불변.

**게이트 통과**: 위반 0.

## Project Structure

### Documentation (this feature)

```text
specs/022-realtime-stream-views/
├── plan.md              # 이 파일
├── research.md          # Phase 0 — 미해결점 결정
├── data-model.md        # Phase 1 — 이벤트/노드/관계 엔티티
├── quickstart.md        # Phase 1 — 검증 시나리오
├── contracts/
│   └── ndjson-stream.md # NDJSON 이벤트 계약(현행 + 본 변경의 불변 보장)
└── tasks.md             # Phase 2 (/speckit-tasks)
```

### Source Code (영향 경로 — cross-service)

```text
robo-data-analyzer/                         (branch refactor)
├── shared/workflow/graph_stream.py         # ★신규 — 공통 emit 헬퍼(노드/관계 델타 → wire)
├── analysis_flow/step6_analyze_code/analyze_code_step.py  # _emit_code_graph → 헬퍼 재사용(DRY)
├── analysis_flow/step1_parse_ddl/parse_ddl_step.py        # 완료 시 TABLE/COLUMN/HAS_COLUMN emit + message
├── analysis_flow/step2_load_ast/load_ast_step.py          # FILE/PROC/CLASS/METHOD + PARENT_OF/HAS_MEMBER emit + message
├── analysis_flow/step3_scan_project/scan_codebase_step.py # (framework) 코드노드 emit + message
├── analysis_flow/step4_linking/**                         # READS/WRITES/CALLS/REFERENCES/INIT_BY emit + message (양 전략 변형)
└── shared/log/processing_progress.py                      # (참고) processing_event — 필요 시 활용

robo-data-frontend/                         (branch main)
├── src/features/code-analysis/analyzer.ts                 # (소비) 이미 reactive; processing_event 콘솔 핸들러 검토
├── src/features/code-analysis/components/graph/GraphTab.vue    # graphStats → nodeMap/linkMap 기준(B)
└── src/features/code-analysis/components/graph/NvlGraph.vue    # NVL 레이아웃 정상 forceDirected(D) — 프로토타입 정합
    └── components/graph/useGroupHulls.ts                  # 헐 동기(이미 동일 변환) — 연속 추종 보강 검토
```

**Structure Decision**: web(백엔드+프론트) 2-레포 capability. 백엔드가 이벤트를 더 이른 step에서 emit(생산자), 프론트는 기존 소비 경로 재사용 + 카운트/레이아웃 수정(소비자). 계약(NDJSON)은 형식 불변이라 blast radius 최소.

## Complexity Tracking

*위반 0 — 비움.*
