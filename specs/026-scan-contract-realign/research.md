# Phase 0 Research: 026-scan-contract-realign

## R1. Drift 전수조사 (확정)

`framework_functions`(옛, 코드에 없음) 잔재 — 4파일 8곳:
- `prompts/agents/scan_agent_system.md`:57,60,69,121 (스캔 에이전트 시스템 프롬프트, 필드표 포함)
- `prompts/agents/residual_pattern_system.md`:28
- `skills/proframe-c-pfm-dbio/SKILL.md`:70,88
- `skills/_templates/SKILL.md`:44

`framework_code_nodes`(현 코드 필드) — 코드 8곳:
- `data_models/agent_output.py`:94,120(주석),164(필드 정의, 클래스 `ScanResult`)
- `analysis_flow/step3_scan_project/scan_agent.py`:178
- `analysis_flow/step4_linking/stage2_extract_calls/link_calls_by_name_step.py`:40
- `analysis_flow/step4_linking/stage3_residual_patterns/residual_pattern_loop.py`:211,224
- `shared/lang/call_classifier.py`:13(docstring)

**결론**: 계약 필드 drift 는 이 하나뿐(다른 계약 필드 call_wrappers/table_access_patterns/ddl_transform/codebase_notes/verify_table_regex/matched_skill/no_match_reason/confidence/evidence 는 전부 정합). 리네임은 옛(framework_functions)·현(framework_code_nodes) 양쪽을 **신규 `framework_runtime_functions`** 로 수렴.

## R2. 모델 구조 (확정)

- `ScanResult`(agent_output.py:134) 단일 클래스에 필드 존재. `scan_agent.py` 는 `response_format=ScanResult` 로 Pydantic 강제(submit-tool wrap, 헌법 VII). draft 도 ScanResult 타입 → **필드 정의는 1곳(line 164)**.
- 내부 함수 파라미터 `framework_names`(call_classifier.py:112 / link_calls_by_name_step.py:40) 는 **계약 필드가 아님**(분류기 내부 인자). 리네임 비범위 — 단 link step 의 `scan_result.get("framework_code_nodes")` 키는 계약이므로 변경 대상.

## R3. KV-Cache 영향 (헌법 V — 확정)

- 드롭목록 필드를 참조하는 프롬프트 = `prompts/agents/{scan_agent_system,residual_pattern_system}.md` 둘뿐.
- **함수분석(step6) 프롬프트(`prompts/{dbms,framework,common}/…`)는 그 필드를 참조하지 않음** → 편집해도 함수분석 LLM 의 공유 hot prefix 바이트동일성 불변.
- 스캔/잔여 에이전트 프롬프트는 run 당 소수 호출이라 캐시 hot path 아님 → 안전.

## R4. 회귀 기준선

- "옛날엔 잘 됐다" = 6/19(`17922c3`) 리네임 이전, 필드명이 `framework_functions` 로 코드·문서 일치하던 상태. 그때 스캔 에이전트가 PFM_*/mpfm_* 를 드롭목록에 정상 적재.
- 따라서 본 수정의 합격 = 그 상태의 동작 회복(드롭목록=런타임함수, internal 회복).

## R5. FR-003 가드 설계 근거

- `scan_codebase_step.py` 에 이미 `_warn_if_call_wrappers_missing(result_dict)` 동종 패턴 존재(line 78,93~). `code_nodes` 는 같은 step 에서 가용(line 62).
- 가드 = `_warn_if_framework_overlaps_project(result_dict, code_nodes)`: 드롭목록 ∩ 프로젝트 code_nodes bare-name 이 임계 이상이면 `log_warning`(+ 가능시 stream emit). 임계는 보수적으로(예: 1건 이상이면 경고, 다수면 강조) — 글로브 패턴 오탐 고려해 에러가 아닌 경고로 표면화(조용한 실패만 차단, 헌법 IV).
