# Tasks: Residual Submit Resilience

**Input**: `spec.md`, `plan.md`

## Phase 1: Contract Red

- [x] T001 `tests/pipeline/step4_linking/test_residual_resilience.py`에 공용 submit 누락 전용 타입 계약을 추가하고 Red를 확인한다.
- [x] T002 같은 파일에 residual 보존과 다른 `ValueError` 전파 반대 사례를 추가하고 Red를 확인한다.

## Phase 2: Implementation

- [x] T003 `llm/client/runners/structured_agent.py`에 submit 누락 전용 예외를 정의하고 정확한 경계에서 발생시킨다.
- [x] T004 `pipeline/steps/step4_linking/stage3_residual_patterns/residual_pattern_loop.py`가 전용 예외만 처리해 경고 후 기존 residual을 보존하게 한다.
- [x] T005 오류 메시지 substring 판정이 0건인지 검색한다.

## Phase 3: Verification

- [x] T006 국소 정상/submit 누락/다른 오류 테스트를 통과시킨다.
- [x] T007 관련 LLM 경계·step4 회귀를 통과시킨다.
- [x] T008 구조·전체 unittest(`-t .`)·prompt byte-identical·compileall·diff check를 통과시킨다.
- [x] T009 감사대장과 최종 D-0 검증됨/실패/미검증 기록을 현재 증거로 갱신한다.

## Evidence

- Red: 새 테스트 import가 `StructuredResponseMissingError` 부재로 실패.
- Green: residual 계약 3/3, step4 6/6, LLM 경계 30/30.
- 전체: structure 18/18, unittest 150/150, prompt 26/26 byte-identical, compileall·`git diff --check` 통과.
- 반증 검색: `submit_residual` 메시지 substring 예외 판정 0건.

## Dependencies

`T001,T002 -> T003,T004 -> T005,T006 -> T007,T008 -> T009`
