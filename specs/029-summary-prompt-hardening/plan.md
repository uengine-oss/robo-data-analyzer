# Implementation Plan: code_summary 프롬프트 환각·쓰기누락 방지 강화

**Branch**: `029-summary-prompt-hardening` | **Date**: 2026-06-26 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/029-summary-prompt-hardening/spec.md`

## Summary

code_summary 분할(분할1)의 summary 생성 지침을 dbms·framework 두 전략에 동일 원칙으로 강화해 LLM 환각·쓰기누락을 방지한다. 핵심 변경: ① 하위 노드(자식 구문/하위 함수) [핵심]의 처리 축·대상을 부모(호출 함수)가 충실히 계승(환각 금지), ② 주어진 모든 컨텍스트 단서를 무시·치환·누락 금지(충돌 시 코드·하위 [핵심] 우선), ③ 쓰기 노드는 [핵심]에 쓰기 동작·대상 명시(대상도 의미어), ④ 입력 테이블→출력 테이블 종합, ⑤ 예시 범용화. 지침은 KV-cache 공유 `_common`(전략별)과 전략 고유 `code_summary` 파일에 분산 반영하되 상호 충돌이 없어야 한다.

**접근**: 프롬프트 yaml 텍스트만 수정(코드 로직·스키마·노드/엣지 변경 0). 검증은 실데이터 재분석 후 그래프 조회 + 임베딩 검색 측정. 일부(①②③⑤)는 탐색적으로 적용·검증 완료, 잔여(④ 입력→출력 종합, ③ "대상 의미어" 명확화, prompt_hashes 갱신)는 본 plan 절차로 정식 반영.

## Technical Context

**Language/Version**: YAML 프롬프트(analyzer Python 3.11 파이프라인이 로드)

**Primary Dependencies**: `shared/llm/yaml_llm_runner.py`(`extends: _common` 합성 + user_template_specific 결합), 사내 qwen(SGLang) 분석 LLM

**Storage**: N/A (프롬프트는 파일; 산출 summary는 Neo4j 노드 속성)

**Testing**: (1) 재분석 후 Neo4j 조회로 노드 summary 검증(권역 등), (2) `module_test/임베딩서칭`의 요약질의 검색 정확도 측정, (3) CI `byte_identical.yml` + `prompt_hashes.json` SHA256 가드

**Target Platform**: analyzer 분석 파이프라인(dbms/framework 3-split fan-out)

**Project Type**: 분석기 프롬프트(LLM 의미 추출 단계의 system/user 지침)

**Performance Goals**: 환각 0·쓰기누락 0%·요약질의 검색 10/10(SC-001~004). 토큰/latency 회귀 없음(지침 추가는 짧고 KV-cache prefix 일관 유지)

**Constraints**:
- ★KV-cache(헌법 V): `_common.yaml`은 3-split 공유 prefix. 수정 시 세 분할 모두 같은 `_common`을 보므로 분할 간 byte-identical은 유지되나, **prefix 내용이 바뀌므로 `cli_scripts/prompt_hashes.json`의 SHA256을 갱신**해야 CI(`byte_identical.yml`)가 통과한다.
- 헌법 II(분기 0): 프롬프트는 전략별 파일로 이미 분리 — 코드 분기 추가 0.
- 헌법 VI(스키마 절제): 출력 스키마 불변(지침만 강화).

**Scale/Scope**: 4개 yaml — `prompts/dbms/_common.yaml`, `prompts/dbms/code_summary_batch.yaml`, `prompts/framework/_common.yaml`, `prompts/framework/code_summary.yaml`. 분할2·3 직접 변경 없음(공유 `_common` 전파만).

## Constitution Check

*GATE: Phase 0 전 통과 필수. Phase 1 후 재확인.*

| 원칙 | 판정 | 근거 |
|---|---|---|
| I. Determinism-First | ✅ PASS | 변경 대상은 LLM이 담당하는 "의미 요약" 단계. 결정론 단계 무관. |
| II. Zero-Branch | ✅ PASS | 전략별 프롬프트 파일 분리 기존 구조 사용. 코드 분기 추가 0. |
| III. Single Source of Truth | ✅ PASS | 지침은 각 전략 `_common`/`code_summary`에 단일 위치. 매직넘버 무. |
| IV. No Silent Failure | ✅ PASS | 프롬프트 지침이라 무관(실행 경로 불변). |
| V. KV-Cache-Friendly | ⚠️ PASS(조건) | `_common` 수정 → 3분할 공유 일관 유지(byte-identical), 단 **prompt_hashes.json 갱신 task 필수**(미갱신 시 CI 실패). |
| VI. Schema Restraint | ✅ PASS | 출력 Pydantic 스키마 불변. 자연어 [핵심] 단일 문자열 유지. |
| VII. Submit-Tool Wrap | ✅ PASS | 무관(출력 경로 불변). |
| VIII. Cohesion | ✅ PASS | 무관. |
| Cross-Service Contracts | ✅ PASS(내부) | Output 계약 = Neo4j **스키마(node label/edge type)** — 불변. summary는 노드 **값**의 품질이지 스키마가 아님 → 다운스트림 계약 무영향(내부 변경). |

**위반 없음 — Complexity Tracking 불필요.**

## Project Structure

### Documentation (this feature)

```text
specs/029-summary-prompt-hardening/
├── plan.md              # 이 파일
├── research.md          # Phase 0 — 환각/쓰기누락 방지 지침 결정 근거
├── data-model.md        # Phase 1 — summary 엔티티·계승 관계
├── quickstart.md        # Phase 1 — 재분석→권역 회복·검색 검증 절차
└── tasks.md             # Phase 2 (/speckit-tasks)
```

contracts/ 없음 — 외부 인터페이스(스키마/엔드포인트) 변경이 없는 순수 내부 프롬프트 변경.

### Source Code (repository root)

```text
prompts/
├── dbms/
│   ├── _common.yaml              # 단계4: 자식 집계축 충실 계승(공유 — 3분할 전파)
│   └── code_summary_batch.yaml   # [1.5] logical_name 쓰기 / [2] 쓰기노드·컨텍스트반영·대상의미어·입력출력종합
└── framework/
    ├── _common.yaml              # 단계4(a): 하위 함수(callee) 처리축 계승(공유)
    └── code_summary.yaml         # [2.3] 컨텍스트반영·쓰기·대상의미어·입력출력종합

cli_scripts/
└── prompt_hashes.json            # ★_common 변경분 SHA256 재핀(갱신 task)
```

**Structure Decision**: 기존 3-split 프롬프트 레이아웃 유지. 공유 원칙은 각 전략 `_common`(KV-cache prefix)에, 전략 고유·쓰기노드 지침은 `code_summary*`에 배치. dbms는 "부모-자식(구문 트리)", framework는 "호출 함수-하위 함수(callee)" 용어로 같은 원칙을 각각 표현(상호 충돌 없이 필드별 층위 분리: 단계1=표현 / 쓰기동작=의미 / 단계4=계승).
