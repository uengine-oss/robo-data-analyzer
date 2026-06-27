# Implementation Plan: 스테레오타입 → 라벨 (Phase 1, additive)

**Branch**: `refactor` | **Date**: 2026-06-27 | **Spec**: [spec.md](./spec.md)

## Summary
stereotype을 노드 라벨로 부여하는 공용 헬퍼 `shared/neo4j/nodes/stereotype_label.py::apply_stereotype_labels((id,stereotype)…)` 추가 — 값별로 묶어 `MATCH (n {id}) SET n:`<값>``(검증풀 통제값+백틱). `code_node.update_summaries`·`module.upsert_modules`에서 호출. 속성은 유지(무중단). 향후 분석에 영구 적용 + 현재 그래프는 마이그레이션 쿼리로 즉시 적용.

## Technical Context
Python/Neo4j. 변경: `shared/neo4j/nodes/stereotype_label.py`(신규), `code_node.py`(import+update_summaries), `module.py`(import+upsert_modules). 동적 라벨=값별 배치(Cypher 파라미터 라벨 불가). APOC 불요.

## Constitution Check (analyzer)
- 생산자 변경이나 **신규 라벨만 추가(스키마 확장)**, 식별 id·관계·기존 라벨 불변. 무중단(속성 유지). PASS.

## 설계
- `apply_stereotype_labels(client, pairs)`: stereotype별 그룹 → `UNWIND $items AS item MATCH (node {id:item.id}) SET node:`<st>``.
- 호출: `update_summaries`(analyze_code 결과), `upsert_modules`(summarize_module 결과).
- 무중단: `to_summary_item`/`to_item`의 stereotype 속성 SET 그대로(후속 038서 제거).

## 검증
- py_compile OK.
- 현재 그래프 마이그레이션(distinct stereotype별 SET 라벨) → labels 확인: FUNCTION+{Command/Query/Adapter/Validator/ApplicationService/Helper}, FILE+Service. 카운트 일치.
- 후속(036/037/038)에서 소비처 이관·속성 제거.

## Complexity Tracking
*위반 없음.*
