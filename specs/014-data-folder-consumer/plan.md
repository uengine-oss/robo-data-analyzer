# Implementation Plan: Data-Folder-Only Consumer

**Branch**: `014-data-folder-consumer` | **Date**: 2026-06-15 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/014-data-folder-consumer/spec.md`

## Summary

analyzer를 표준 `data/` 폴더의 순수 소비자로 되돌린다. `project_root` 경로 모드와 그 전파(약 8지점)를 제거해, run은 항상 `data/source`(소스)·`data/ddl`(표 정의)·`data/analysis`(AST)만 읽는다. 표 정의(DDL) 단계는 폴더 전체가 아니라 **`data/ddl`만** 읽어, 파서(antlr 스펙 006)가 거기 표 정의만 담았음에 기대 — 프로시저가 DDL 파서에 닿지 않아 현재 크래시가 사라진다. 분석 알고리즘·그래프 스키마·문법은 불변. 이는 헌법 II(Zero-Branch)의 단순화로, 새 변형이 아니다.

## Technical Context

**Language/Version**: Python 3.12 (`.venv`)

**Primary Dependencies**: LangGraph(파이프라인 StateGraph), sqlglot(DDL 파싱), Neo4j 드라이버, FastAPI(`/robo/analyze`)

**Storage**: 파일시스템 `data/{source, ddl, analysis}`(입력) + Neo4j(출력 그래프)

**Testing**: 파이프라인 baseline 러너(`cli_scripts/baseline_runner.py`), pytest 단위

**Target Platform**: Python 서비스(포트 5502), 게이트웨이 경유

**Project Type**: 분석 파이프라인 백엔드(단일 모듈, LangGraph DAG)

**Performance Goals**: 동작 보존 — 같은 `data/` 입력에 기존 산출과 동일(SC-004). 분기 제거는 순수 단순화.

**Constraints**: 분석 로직·그래프 스키마·LLM 프롬프트 불변(헌법 V/VI); run은 파괴적 전체 재빌드 유지; no-silent-failure(헌법 IV) 유지.

**Scale/Scope**: run당 단일 작업 세트(`data/`), 단일 동시 run.

## Constitution Check

*GATE: Phase 0 전 통과 필수. Phase 1 후 재확인.*

analyzer 헌법 v1.0.0 기준:

| 원칙 | 판정 | 근거 |
|---|---|---|
| I. Determinism-First | ✅ PASS | LLM 사용처 불변(입력 배선만 변경). |
| II. Zero-Branch Strategy | ✅ **강화** | `project_root` 모드 분기 제거 = 단순화(새 변형 0). 본 기능의 핵심. |
| III. Single Source of Truth | ✅ 강화 | 입력원을 `data/` 하나로 단일화. |
| IV. No Silent Failure | ✅ PASS | 무효/부재 입력엔 여전히 raise. 깨진 파일 격리는 상류 파서(antlr 006 US4) 담당 → analyzer는 skip 불필요, IV 유지. |
| V. KV-Cache-Friendly | ✅ PASS | 프롬프트 불변. |
| VI. Schema Restraint | ✅ PASS | 출력 스키마 불변. |
| VII. Submit-Tool Wrap | ✅ PASS | 에이전트 출력 경로 불변. |
| VIII. Cohesion Exception | ✅ PASS | COMPONENT 합본 불변. |
| Cost/Safety (run=파괴적 재빌드) | ✅ PASS | 재빌드 정책 유지. |

**GATE 결과: PASS** — 헌법 개정 불필요(antlr 006의 내용분류 개정과 별개).

## Project Structure

### Documentation (this feature)

```text
specs/014-data-folder-consumer/
├── plan.md · research.md · data-model.md · quickstart.md
├── contracts/data-input-contract.md
└── tasks.md   # /speckit-tasks (이 명령이 만들지 않음)
```

### Source Code (repository root) — 변경 지점(전수)

```text
api/analyze_router.py
  · AnalyzeRequest.project_root  → 수용하되 inert(소스 안 바꿈; 역호환)
  · root_str = project_root or data/source  → 항상 data/source
  · Codebase(root=..., ddl_dir=...)         → root=data/source, ddl_dir=data/ddl(기본)

data_models/codebase.py
  · ddl_dir 기본값  → ENV.data.dir/ddl (현재 None→root 였음)

analysis_flow/step1_parse_ddl/parse_ddl_step.py
  · ddl_dir = project.ddl_dir or project.root  → project.ddl_dir(=data/ddl)만; rglob 대상 축소

analysis_flow/step2_load_ast/{load_ast_step.py, antlr_json_reader.py}
  · ast_and_source_roots(project.root): source_root가 data/source로 고정됨(분기 없음 — 확인)

analysis_flow/step3_scan/{scan_codebase_step.py, scanner_input_collector.py, scan_agent.py}
  · project_root 전파 → data/source 단일 기준 (모드 분기 제거)

analysis_flow/step6_*/link_deterministic_step.py
  · grep/regex 도구의 project.root 기준 → data/source (분기 제거)
```

**Structure Decision**: 신규 파일 없음 — **제거·단순화 위주**. `Codebase`가 단일 진실(root=data/source, ddl_dir=data/ddl)이 되도록 기본값을 고정하고, 각 step에 퍼진 `project_root` 분기를 걷어낸다(DRY·탈바꿈). 분석 로직 본문은 손대지 않는다.

## Complexity Tracking

> Constitution Check 위반 없음 → 해당 없음.
