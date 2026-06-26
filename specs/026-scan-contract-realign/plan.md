# Implementation Plan: Scan-Agent 계약 명칭 정합 (026-scan-contract-realign)

**Spec**: [spec.md](./spec.md) · **Branch**: `refactor` · **Created**: 2026-06-26

## Summary

스캔 에이전트의 "드롭할 프레임워크/런타임 함수 목록" 계약 필드를 **`framework_code_nodes` → `framework_runtime_functions`** 로 리네임하고(코드 5 + 문서 4 일괄), 에이전트 지시 문서의 옛 이름 `framework_functions` 잔재(4파일 8곳)를 **곧장 신규명으로 정합**한다. 더해, 드롭 목록이 프로젝트 자기 `code_nodes` 와 임계 이상 겹치면 표면화하는 **가드(FR-003)** 를 기존 `_warn_*` 패턴으로 추가한다. 분류 알고리즘(call_classifier 동작)은 불변.

## Technical Context

- **언어/런타임**: Python 3.12, LangGraph 파이프라인, Pydantic 출력 모델(submit_scan 도구 wrap).
- **변경 핵심**: 계약 필드명 1개(에이전트 출력 스키마) + 그 필드를 읽는 소비처 + 그 필드를 지시하는 문서. 알고리즘/그래프 스키마 무변경.
- **회귀 기준선**: 6/19 리네임 이전(framework_functions 일치) 정상 동작 = "옛날엔 잘 됐다". baseline/단위 테스트.
- **검증**: (1) 전수 grep 정합(코드 계약 ↔ 문서), (2) ProFrame C 재분석 → 드롭목록에 프로젝트 함수 0, internal 회복, (3) 가드 합성 테스트.

## Constitution Check

*GATE: Phase 0 전 통과 필수. Phase 1 후 재확인.*

| 원칙 | 판정 | 근거 |
|---|---|---|
| I. Determinism-First | ✅ 해당없음 | 분류는 이미 결정론, 변경 안 함 |
| II. Zero-Branch | ✅ 충족 | 리네임=선언부 단일 변경, 제어분기 0 |
| III. Single Source of Truth | ✅ **강화** | 본 작업의 목적 — 필드명 단일진실 확립 |
| IV. No Silent Failure | ✅ **강화** | FR-003 가드로 조용한 업무함수 드롭 표면화 |
| V. KV-Cache-Friendly | ✅ 충족(주의) | 편집 대상은 **스캔/잔여 에이전트 시스템 프롬프트**(run당 소수 호출)지, **함수분석 LLM의 공유 hot prefix 아님**. 함수분석 프롬프트는 무변경 — Phase 0 에서 확인. |
| VI. Schema Restraint | ✅ 충족 | 신규 출력필드 0(리네임만) |
| VII. Submit-Tool Wrap | ✅ 충족 | 리네임 필드는 submit_scan 스키마 내 유지 |
| VIII. Cohesion 예외 | ✅ 해당없음 | |

**결과: PASS** (위반 0 → Complexity Tracking 불필요).

## Project Structure

### Documentation (this feature)
```
specs/026-scan-contract-realign/
├── spec.md
├── plan.md            # 이 문서
├── research.md        # Phase 0 — drift 전수조사 + KV-cache 영향 확인
├── data-model.md      # Phase 1 — 필드 리네임 정의
├── quickstart.md      # Phase 1 — 검증 절차
├── checklists/requirements.md
└── tasks.md           # Phase 2 (/speckit-tasks)
```

### Source Code (변경 지점 — 전수 확정)
```
data_models/agent_output.py                 # 필드 정의(line 164) + 주석(94,120) + draft/ScanResult 양쪽
analysis_flow/step3_scan_project/
    scan_agent.py                           # line 178 draft→ScanResult 매핑
    scan_codebase_step.py                   # FR-003 가드 추가(_warn_* 패턴, code_nodes 가용)
analysis_flow/step4_linking/
    stage2_extract_calls/link_calls_by_name_step.py   # line 40 .get("...")
    stage3_residual_patterns/residual_pattern_loop.py # line 211,224
shared/lang/call_classifier.py              # docstring(line 13). 내부 param framework_names = 유지(계약 아님)

prompts/agents/scan_agent_system.md         # 57,60,69,121  framework_functions → framework_runtime_functions
prompts/agents/residual_pattern_system.md   # 28
skills/proframe-c-pfm-dbio/SKILL.md         # 70,88
skills/_templates/SKILL.md                  # 44 (전파 방지)
```

## Phase 0 — Research (research.md)
- drift 전수조사 결과(4파일 8곳 framework_functions, 코드 8곳 framework_code_nodes) 고정.
- 내부 param `framework_names` 는 계약 필드 아님 → 리네임 비범위(혼동 시 주석만 보강) 결정 근거.
- **KV-cache 확인**: 함수분석(step6) 시스템 프롬프트가 드롭목록 필드를 참조하지 않음을 확인 → 프롬프트 편집이 공유 hot prefix 의 바이트동일을 깨지 않음.

## Phase 1 — Design
- **data-model.md**: `ScanResult` / 에이전트 draft 모델의 필드 `framework_code_nodes` → `framework_runtime_functions`(타입·의미 동일). 신규 필드/스키마 0.
- **contracts/**: 별도 외부 계약 변경 없음(submit_scan 도구 내부 스키마 필드명만). contracts 디렉토리는 N/A 표기.
- **quickstart.md**: 재현·검증 절차(전수 grep + ProFrame C 재분석 + 가드 합성 테스트).

## Complexity Tracking
(해당 없음 — Constitution 위반 0.)
