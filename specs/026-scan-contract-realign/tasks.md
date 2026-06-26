# Tasks: 026-scan-contract-realign

**입력**: [plan.md](./plan.md) · [research.md](./research.md) · [data-model.md](./data-model.md) · [spec.md](./spec.md)
**원칙**: 리네임 `framework_code_nodes` / 옛 `framework_functions` → **`framework_runtime_functions`**. 분류 알고리즘 불변.

## Phase A — 코드 필드 리네임 (US2 P1)

- [x] **T001** `data_models/agent_output.py`: `ScanResult.framework_code_nodes` → `framework_runtime_functions` (line 164). 주석 94·120 갱신. description 에 "프로젝트 함수(code_nodes) 아님 — 프레임워크/런타임 함수만" 보강(LLM 오인 방지). ✅
- [x] **T002** `analysis_flow/step3_scan_project/scan_agent.py:178`: `framework_code_nodes=draft.framework_code_nodes` → 신규명. ✅
- [x] **T003** `analysis_flow/step4_linking/stage2_extract_calls/link_calls_by_name_step.py:40`: `scan_result.get("framework_code_nodes")` → 신규 키. (내부 local `framework_names` 유지.) ✅
- [x] **T004** `analysis_flow/step4_linking/stage3_residual_patterns/residual_pattern_loop.py:211,224`: `scan.framework_code_nodes` → 신규명. ✅
- [x] **T005** `shared/lang/call_classifier.py:13`: docstring 의 `framework_code_nodes` → 신규명. (param `framework_names` 유지.) ✅

## Phase B — 문서/프롬프트/스킬 정합 (US2 P1)

- [x] **T006** 옛 이름 `framework_functions` → `framework_runtime_functions`: ✅
  - `prompts/agents/scan_agent_system.md` (57,60,69,121 필드표)
  - `prompts/agents/residual_pattern_system.md` (28)
  - `skills/proframe-c-pfm-dbio/SKILL.md` (70,88)
  - `skills/_templates/SKILL.md` (44)
  - ⚠️ 헌법 V: 스캔/잔여 에이전트 시스템 프롬프트(함수분석 hot prefix 아님) — 안전. 함수분석 프롬프트 무변경 확인됨.

## Phase C — 가드 (US3 P2, 헌법 IV)

- [x] **T007** `analysis_flow/step3_scan_project/scan_codebase_step.py`: `_warn_if_framework_overlaps_project(result_dict, code_nodes)` 추가 + `_warn_if_call_wrappers_missing` 옆 호출. 드롭목록 ∩ 프로젝트 code_nodes(bare name, `_bare_name` 재사용) ≥ 1 이면 `log_warning`. 글로브(`*?[`) 항목은 오탐 방지 제외 → 경고(에러 아님). ✅
- [x] **T008** 가드 단위 테스트 `test_framework_overlap_guard.py`: 겹침→경고 / 정상·빈값→무경고. 3/3 통과. ✅

## Phase D — 검증

- [x] **T009** V1 전수 grep 정합: 옛/중간 이름 0건, 신규명 코드+문서 정합 ✅ / 임포트·순환참조 클린 ✅ / 가드 3/3 ✅ (analyzer는 pytest 스위트 부재 → 실행기반 검증).
- [ ] **T010** (운영자, 선택·~4분) quickstart V4 라이브 재현: **깨끗한 data**(올바른 source/<proj> + ddl)로 framework 재분석 → `framework_runtime_functions` 에 PFM_*/mpfm_* 만, 프로젝트 함수 0, internal 회복(SC-001/002). ※data 구조·DDL 정비 후.

## 의존
- A·B 병렬. C 는 A 후. D 는 A·B·C 후. (모두 한 커밋으로 — 필드명 불일치 순간 방지.)
