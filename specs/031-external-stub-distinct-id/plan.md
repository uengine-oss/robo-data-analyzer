# Implementation Plan: 외부 스텁 모듈·엔트리 함수 id 구분

**Branch**: `refactor` (spec dir `031-external-stub-distinct-id`) | **Date**: 2026-06-26 | **Spec**: [spec.md](./spec.md)

## Summary

`shared/neo4j/edges/calls.py` 의 `upsert_calls` 가 외부 CALLS 의 함수 id 와 모듈 id 를 같은 값(호출명)으로 만들어, 식별키 유일성 위반 + 프론트가 한 노드로 합침 → 외부 모듈 아래 함수 미표시. → 외부 **엔트리 함수 id = `{module_id}.{name}`** 로 구분(내부 `owner.name` 패턴과 동일). `EXTERNAL_QUERY` 본문 무변경(이미 callee_id=함수·callee_module=모듈로 분리 — 값만 구분되게).

## 기술 접근 (최소·안전)
- 변경 = `upsert_calls` 의 external dict 구성 1곳: `callee_id = f"{module_id}.{name}"`(module_id=`callee_module or callee_id`, name=`callee_name or callee_id`). `callee_module`=module_id, `callee_name`=name 유지.
- `EXTERNAL_QUERY`(MERGE FUNCTION/MODULE/HAS_MEMBER) 그대로 → 이제 함수 id ≠ 모듈 id → 별개 노드 공존.
- **Blast radius 전수확인**: 외부 callee_id 소비처 = `build_context_step`(callees/callers 집합 라벨), `three_split_llm_fanout`(callee 요약 조회 실패 시 "(외부 또는 미해결)" 표기) — 외부는 어차피 내부 contexts 에 없어 동일 처리, **id 형식 변경 무해**. architect `api/` 는 `:EXTERNAL` 의존 0(grep). 내부 CALLS·노드 무변경.

## Constitution Check
| 원칙 | 판정 | 근거 |
|---|---|---|
| I. Determinism-First | ✅ | id 합성 결정론(LLM 0) |
| II. Zero-Branch Strategy | ✅ | calls.py 공유 1곳, strategy 분기 0(framework/dbms 공통) |
| III. Single Source of Truth | ✅ | 외부 식별키 = id 단일·유일(같은-id 위반 해소) |
| IV. No Silent Failure | ✅ | 해당 없음 |
| 신규 라벨/관계/스키마 | ✅ 0 | 기존 라벨·HAS_MEMBER 재사용, id 값만 구분 |
**PASS**.

## 설계 (현재 → 목표)
- 현재 `upsert_calls`(L64-71): `callee_id: e.callee_id`(=호출명), `callee_module: e.callee_module or e.callee_id`(=호출명) → 동일.
- 목표: `module_id = e.callee_module or e.callee_id`; `name = e.callee_name or e.callee_id`; `func_id = f"{module_id}.{name}"`; dict `callee_id=func_id, callee_module=module_id, callee_name=name`.
- 결과: 외부 zapamcom10020 → MODULE id `zapamcom10020` + FUNCTION id `zapamcom10020.zapamcom10020` (구분) + HAS_MEMBER. 내부 패턴과 동일.

## Project Structure
```
specs/031-external-stub-distinct-id/ : spec.md ✓ plan.md tasks.md
변경 소스: shared/neo4j/edges/calls.py (upsert_calls external dict)
```

## Complexity Tracking
없음.
