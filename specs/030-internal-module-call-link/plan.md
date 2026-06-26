# Implementation Plan: framework 내부 모듈 호출 정규 해소

**Branch**: `refactor` (spec dir `030-internal-module-call-link`) | **Date**: 2026-06-26 | **Spec**: [spec.md](./spec.md)

## Summary

framework 호출 분류기(`shared/lang/call_classifier.py`)에서 래퍼 호출(`mpfm_dlcall`)의 대상 이름을 내부 모듈로 해소하는 경로(`_wrapper_callee_to_internal`)가 **키 형식 불일치**(색인 키=owner_id 풀 id vs 조회=모듈 맨이름)로 항상 실패 → 내부 모듈이 외부 맨이름 스텁으로 중복 생성. 

**기술 접근(근본·DRY)**: 깨진 전용 리졸버를 **이미 정상 동작하는 `_resolve_internal` 재사용**으로 대체한다. `_resolve_internal`은 `by_name`(= `_bare_name(fn.id)` 키, 엔트리 함수 맨이름 "zapamcom10060" 포함)으로 내부를 정확히 찾으므로, 래퍼에서 추출한 모듈명도 이 함수에 태우면 내부 엔트리 함수로 해소된다. 깨진 `_wrapper_callee_to_internal`·`entry_by_module`·`other_by_module`(항상 빔, 죽은 코드)는 제거.

## Technical Context

**Language**: Python 3.11 / 동기 함수(분류는 LLM 0, 결정론). **변경 파일**: `shared/lang/call_classifier.py` 1개. **신규 의존성 0**. **Neo4j 스키마/계약 변경 0**.

## Constitution Check

| 원칙 | 판정 | 근거 |
|---|---|---|
| I. Determinism-First | ✅ PASS | 호출 분류 결정론 유지(LLM 0). `_resolve_internal`은 결정론. |
| II. Zero-Branch Strategy | ✅ PASS | framework 분류 내부 변경만. strategy 분기 추가 0(dbms 무관). |
| III. Single Source of Truth | ✅ **강화** | 중복 리졸버(`_wrapper_callee_to_internal`) 제거 → 내부 해소 = `_resolve_internal` 단일 출처(DRY). |
| IV. No Silent Failure | ✅ PASS | 조용한 오분류(내부→외부) 제거. 새 silent fail 없음. |
| V·VI·VII (LLM/프롬프트) | ✅ N/A | 프롬프트·LLM 출력 변경 0. |
| VIII. Cohesion | ✅ PASS | 죽은 헬퍼 제거로 응집↑. |

**결과: PASS** (신규 라벨·관계·노드 0, 계약 0). 오히려 원칙 III 강화 + 데드 제거.

## 설계 (현재 → 목표)

### 현재 (call_classifier.py, 전수조사 확정)
- `classify_call_sites`: `by_name`(키=`_bare_name(fn.id)`)·`module_of` 구축(L125-129) → 직접 호출은 `_resolve_internal(name, …)`로 내부 해소(L173, **정상**).
- 래퍼 경로(L181-199): `_extract_wrapper_callee`로 인자 추출 → `_wrapper_callee_to_internal(extracted)`(L190) 호출 → **항상 None**(키 불일치) → external(L196-198, 버그 발현).
- `entry_by_module`/`other_by_module`(L134-142): 키=owner_id(풀), entry 판정식 `fn.id == f"{owner_id}.{owner_id}"`(L139, 항상 False) → 색인 비거나 owner_id 키. `_wrapper_callee_to_internal`(L144-151): 맨이름으로 조회 → 불일치.

### 목표
- 래퍼 추출 대상도 **`_resolve_internal(extracted, caller, by_name, module_of)`** 로 해소:
  - 내부 모듈이면 엔트리 함수(by_name[모듈short명]) 정식 id → internal Calls.
  - 아니면 None → 기존대로 external(진짜 외부 보존).
- 죽은 `entry_by_module`·`other_by_module`·`_wrapper_callee_to_internal` **제거**(데드, 원칙 III·VIII).
- 동명 모듈 다수 시 `_resolve_internal`의 기존 규칙(같은 모듈 우선→정렬 첫)로 결정론(FR-006) 자동 충족.

### blast radius (전수조사)
- `_wrapper_callee_to_internal`·`entry_by_module`·`other_by_module`는 **`classify_call_sites` 내부 지역 정의** → 외부 참조 0(제거 안전). 구현 시 grep 재확인(원칙 §5).
- 직접 호출·stdlib/framework 드롭·dbms(`call_linker.py`)·다른 step 무변경 → 무회귀(US2).

## Project Structure

```text
specs/030-internal-module-call-link/
├── spec.md ✓ · plan.md(이 파일) · checklists/requirements.md ✓ · tasks.md(speckit-tasks)
변경 소스: shared/lang/call_classifier.py  (1파일)
```

## Complexity Tracking

해당 없음 (Constitution PASS, 위반 0).
