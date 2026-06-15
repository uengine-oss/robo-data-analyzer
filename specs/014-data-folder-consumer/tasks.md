# Tasks: Data-Folder-Only Consumer

**Feature**: 014-data-folder-consumer | **Branch**: `014-data-folder-consumer`
**Input**: plan.md · research.md · data-model.md · contracts/data-input-contract.md · quickstart.md

핵심 = `project_root` 경로 분기 제거 + `data/`만 소비 + DDL은 `data/ddl`만. 경로는 `robo-data-analyzer/` 기준. **antlr 006(`data/ddl` 청결 보장) 선행/동반 의존.**

> **★ 구현 결과 정정 (원문 재확인, 원칙 ②·④)**: 플랜의 "분기 8지점"은 과대 추정이었음. 실제로 파이프라인(step2/3/4)엔 **경로모드 분기가 없고** 모두 `project.root`만 사용 → router에서 root를 `data/source`로 고정하면 자동 정합. **유일한 결정 지점은 `analyze_router.py` 한 줄.** CLI(`analyze_data_dir.py`)는 이미 `data/source`·`data/ddl` 정석 사용 중. 그래서 실제 수정 = 2파일·소규모.

---

## Phase 1: Setup

- [x] T001 현재 `project_root` 분기 **재확인 완료**(원문 대조) → 실제 분기는 `analyze_router.py` 1곳뿐, step들은 `project.root`만 사용. (메모리 4일 전 — 원문 직접 대조, 원칙 ②) — `api/analyze_router.py`·`data_models/codebase.py`·`analysis_flow/step1_parse_ddl/parse_ddl_step.py`·`step2_load_ast/{load_ast_step.py,antlr_json_reader.py}`·`step3_scan/{scan_codebase_step.py,scanner_input_collector.py,scan_agent.py}`·`step6_*/link_deterministic_step.py`. 실제 분기 목록 확정.

## Phase 2: Foundational (단일 진실 고정)

- [x] T002 단일 진실 고정 — `analyze_router`가 `root=data/source`·`ddl_dir=data/ddl` 항상 설정(코드베이스 dataclass는 무변경; 라우터+CLI가 단일 진실 주입). (research R1)

**Checkpoint**: 입력원이 `Codebase`에서 `data/`로 고정 → step들 분기 자연 소멸.

---

## Phase 3: User Story 1 - analyzer는 data/만 소비 (P1)

**Goal**: run이 `data/source`·`data/ddl`·`data/analysis`만 읽음.
**Independent Test**: `project_root` 없이 `POST /robo/analyze` → `data/`만으로 그래프 생성.

- [x] T003 [US1] `analyze_router`에서 `root_str = project_root or data/source` 삼항식 제거 → 항상 `data/source`, `ddl_dir`=data/ddl; `project_root`는 수용하되 inert(역호환, R4). 완료 in `api/analyze_router.py`.
- [x] T004 [US1] **확인: 변경 불필요** — step3(`scan_codebase_step`/`scanner_input_collector`/`scan_agent`)는 `project.root`만 사용, 모드 분기 없음 → root가 data/source로 고정돼 자동 정합.
- [x] T005 [US1] **확인: 변경 불필요** — step4 링킹(`step4_linking/.../link_deterministic_step.py` 등)도 `project.root`만 사용, 분기 없음 → 자동 정합. (실제 위치 step4, plan의 step6 표기 정정)
- [x] T006 [US1] **확인** — step2 `ast_and_source_roots(project.root)`는 AST=`data/analysis` 고정 + source_root=project.root(=data/source) → 정합. 변경 불필요.

**Checkpoint**: 파이프라인 전 구간이 `data/`만 참조.

---

## Phase 4: User Story 2 - 프로시저가 DDL 파서에 닿지 않음 (P1) ★크래시 해결

**Goal**: DDL 단계가 `data/ddl`만 읽음.
**Independent Test**: 프로시저 포함 프로젝트(antlr 006이 `data/ddl`엔 표만 둠) → ParseError 없이 완료.

- [x] T007 [US2] `parse_ddl_step` 폴백 `or project.root` → `or (ENV.data.dir/"ddl")` 로 교체(소스 루트 절대 안 긁음) + ENV import. 완료 in `analysis_flow/step1_parse_ddl/parse_ddl_step.py` (research R2). ★크래시 가드.

> 진행: 위 핵심(T001-T007) **완료 + py_compile 통과**. T008(잔여 분기 정리)=확인결과 잔여 분기 없음 → 추가 데드 없음. T009-T011(검증)은 **antlr 006 구현 후** rwis 실행 시 함께.

**Checkpoint**: 함수 .sql이 sqlglot에 닿지 않아 run 완료(현재 크래시 해소).

---

## Phase 5: User Story 3 - 경로 모드 분기 제거(정리) (P2)

**Goal**: 입력 모드 조건문 0.
**Independent Test**: `project_root` 주든 안 주든 동일 동작; 코드에 모드 분기 없음.

- [ ] T008 [US3] 파이프라인 전반 잔여 모드 분기·로그·데드 통째 폐기(원칙 ① 한 번 수정=영구) — T001에서 확정한 8지점 전수 정리.

---

## Phase 6: Polish & 검증

- [ ] T009 [P] baseline 동작 보존 확인 — 같은 `data/` 입력에 변경 전후 산출 그래프 동일(SC-004) via `cli_scripts/baseline_runner.py`.
- [ ] T010 ★데이터 정확도 검증(원칙 ③) — rwis 실제 실행 후 AST·LOGS·그래프를 원문과 대조(TABLE·R/W·FK·call·reference 6대 체크), 섞인 파일의 표 정의 누락 없음 확인(quickstart).
- [ ] T011 그래프 스키마·LLM 프롬프트 불변 확인(헌법 VI/V).

---

## Dependencies & 실행 순서

- **antlr 006(US2: `data/ddl` 청결)** 선행/동반 — 없으면 T007이 의미 없음.
- Setup(T001) → Foundational(T002) → US1(T003-T006) → US2(T007) → US3(T008) → Polish(T009-T011).
- T002(단일 진실 고정)가 핵심 — 이후 분기 제거가 따라옴.

## MVP 범위

**T001-T007** (Foundational + US1 + US2) = analyzer 쪽 **그래프 복구 MVP**. antlr 006 MVP와 한 쌍으로 그래프 복구 완성.
