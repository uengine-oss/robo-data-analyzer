# Implementation Plan: Residual Submit Resilience

**Branch**: `main` | **Date**: 2026-07-17 | **Spec**: `specs/063-residual-submit-resilience/spec.md`

## Summary

`run_structured_agent`가 submit 누락을 전용 예외로 표현하게 하고, `run_residual_loop`만 그 타입을 잡아 기존 미해결을 보존한다. 메시지 문자열 판정과 전역 정상화는 사용하지 않는다.

## Technical Context

**Language/Version**: Python 3.12
**Primary Dependencies**: LangChain, LangGraph, Pydantic
**Storage**: N/A
**Testing**: `unittest` discovery with repository top-level `-t .`
**Target Platform**: Windows/Linux Analyzer service
**Project Type**: Python service
**Performance Goals**: 추가 LLM 호출·I/O 0
**Constraints**: 기존 prompt 바이트와 public API/MCP/Neo4j 계약 불변
**Scale/Scope**: 공용 Agent 경계 1개, residual 소비자 1개, 계약 테스트 1개

## Constitution Check

- **I Determinism-First**: 새 판단은 예외 타입이며 LLM 호출을 추가하지 않는다.
- **III SSOT**: submit 누락 정의는 공용 runner의 전용 타입 하나다.
- **IV No Silent Failure**: 공용 경계는 fail-closed, residual은 경고와 unresolved 보존으로 명시적 부분 실패다.
- **VII Submit-Tool Wrap**: submit 도구 패턴은 유지하고 누락 결과의 타입만 명확히 한다.
- **VIII Dependency Direction**: pipeline이 좁은 LLM 경계 예외를 의존하며 역방향 import는 없다.

## Data and Control Flow

```text
Agent invoke
  -> structured_response 존재: 기존 결과 반환
  -> structured_response 부재: StructuredResponseMissingError
       -> scan/기타 호출자: 전파(fail-closed)
       -> residual loop: warning + break + 기존 residual 반환
```

## Project Structure

```text
llm/client/runners/structured_agent.py
pipeline/steps/step4_linking/stage3_residual_patterns/residual_pattern_loop.py
tests/pipeline/step4_linking/test_residual_resilience.py
specs/063-residual-submit-resilience/
```

## Rollback and Failure Handling

- 전용 예외 타입 도입과 residual catch는 한 슬라이스로 검증한다.
- 국소 반대 사례가 실패하면 전체 테스트로 넘어가지 않는다.
- prompt·API·그래프 스키마에는 변경이 없어 별도 데이터 마이그레이션이 없다.
