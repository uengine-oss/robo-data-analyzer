# Implementation Plan: 노드 id 전역 유일성 + 반복 관측 정규화

**Branch**: `047-node-id-uniqueness` | **Date**: 2026-07-14 | **Spec**: [spec.md](./spec.md)

## Summary

`code:`/`db:` 위치 기반 id로 서로 다른 실체의 충돌을 구조적으로 막는다. 파일별 AST 합산 과정에는 공통 `NodeRegistry`를 두어 같은 id·같은 타입·같은 값은 한 노드로 정규화하고, 같은 id의 다른 실체는 실패시킨다. 최종 유일성 가드는 그대로 유지한다.

## Technical Context

**Language/Version**: Python 3.11
**Primary Dependencies**: FastAPI, LangGraph, dataclasses, Neo4j
**Storage**: Neo4j full rebuild
**Testing**: `unittest`/pytest 호환 단위 테스트, 실제 `project/data` AST 파이프라인
**Target Platform**: Windows/Linux analyzer service
**Project Type**: 분석 웹 서비스
**Constraints**: LLM 0, node id 파싱 금지, 조용한 덮어쓰기 금지

## Constitution Check

- I Determinism-First: 타입·id·dataclass 값 비교만 사용, LLM 0 — PASS.
- III Single Source of Truth: 모든 AST 노드가 공통 등록기 한 곳을 사용 — PASS.
- IV No Silent Failure: 실제 충돌은 로그·스트림·예외로 실패 — PASS.
- Cross-Service: terminal analyzer failure는 기존 NDJSON `error`에 `fatal: true`를 추가하며 frontend 소비 계약을 함께 변경 — PASS.
- Full rebuild: id 변경/정규화에 마이그레이션 불필요 — PASS.

## Current → Target Flow

```text
data/analysis/*.json
→ read_antlr_json(파일별 ParseResult)
→ 현재: list.extend → raw 중복 검사 → 공유 Package에서 오탐 실패
→ 목표: NodeRegistry.register → 동일 실체 1개 / 다른 실체 충돌
→ 최종 id 유일성 가드
→ Neo4j + NDJSON graph events
```

## Design Decisions

1. 반복 허용 노드 목록을 만들지 않는다. 모든 노드에 동일 규칙을 적용한다.
2. 동일 실체 판정은 `id + 구체 Python 타입 + dataclass 값 전체`가 같은 경우다.
3. 값이 다르면 어느 쪽도 덮어쓰지 않고 충돌 오류를 낸다.
4. 등록기는 입력 목록별 출력 순서를 보존해 기존 그래프/스트림 순서를 회귀시키지 않는다.
5. `_assert_unique_ids`는 등록기 뒤 최종 방어선으로 유지한다.

## Project Structure

```text
shared/node_registry.py
shared/test_node_registry.py
analysis_flow/step2_load_ast/load_ast_step.py
shared/workflow/executor.py
specs/047-node-id-uniqueness/
```

## Verification

- 동일 Package 12회 → 최종 1개, 정규화 수 11.
- 같은 id의 Package/Module → 충돌.
- 같은 id의 Package인데 name 다름 → 충돌.
- 실제 `shopmall` AST 12개로 `load_ast_step` 통과.
- 최종 노드 id 중복 0, 기존 Module/CodeNode 수 회귀 없음.

## Addendum: downstream opaque-id repair

- 호출 분류는 `CodeNode.name`/`owner_id`만 사용한다.
- AST 종류는 `label`, 표시 이름과 DB 소속은 명시 필드/관계만 사용한다.
- external id는 `shared/neo4j/ids.py`의 중앙 helper로 생성한다.
- 검색 결과를 graph id, SQL FQN, 파일 경로, URL로 분류해 `research.md`에 기록한다.
- opaque id 내부/외부/stdlib/framework/동명이인 호출 회귀와 shopmall 전체 분석을 검증한다.

## Complexity Tracking

위반 없음.
