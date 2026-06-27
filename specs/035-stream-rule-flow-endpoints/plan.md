# Implementation Plan: 라이브 스트림 ↔ neo4j 그래프 정합

**Branch**: `035-stream-rule-flow-endpoints` | **Date**: 2026-06-28 | **Spec**: [spec.md](./spec.md)

## Summary

라이브 분석 스트림이 룰 흐름·외부 호출을 **가공 전 원시 형태**로 보내 저장본(neo4j)과 달랐다. 스트림을 **저장과 동일한 최종 형태**로 발신하게 한다 — (1) NEXT/BRANCH 를 룰→룰 끝점으로, (2) 외부 CALLS 를 스텁 노드까지 함께. 외부 스텁 산출은 저장부와 공유 함수로 단일화한다.

## Technical Context

**Language**: Python (analyzer 파이프라인). **Storage**: Neo4j(저장 로직 무변경). **Testing**: 재분석 후 스트림 이벤트 vs neo4j 전수 비교(노드·관계·끝점). **Constraints**: 저장 결과·타 관계 발신 불변(회귀 0). **Scope**: 발신부만(step6 룰흐름, step4 호출).

## Constitution Check

- 생산자(analyzer) 변경, 신규 라벨/관계 0(기존 EXTERNAL 스텁·NEXT/BRANCH 그대로). DB 저장 로직 불변. 발신만 저장과 일치시킴 → 단일출처(_split_calls)로 DRY. PASS.

## 변경 파일

```
analysis_flow/step6_analyze_code/analyze_code_step.py   # NEXT/BRANCH: local_rule_id→룰노드 id 해소, 룰→룰 발신
shared/neo4j/edges/calls.py                              # _split_calls 추출(저장·발신 공유) + emit_calls(스텁 노드 발신)
analysis_flow/step4_linking/stage2_extract_calls/link_calls_by_name_step.py  # emit_edges → emit_calls (framework)
analysis_flow/step4_linking/stage2_extract_calls/link_calls_by_ast_step.py   # emit_edges → emit_calls (dbms)
```

### 변경하지 않음 (회귀 경계)

- DB 저장: INTERNAL_QUERY/EXTERNAL_QUERY/rule_flow.py — 불변(저장본 결과 동일).
- 범용 발신 emit_edges — 다른 관계(HAS_COLUMN·PARENT_OF·REFERENCES·READS/WRITES…)에 그대로 사용.
- 프론트 — 룰→룰·외부스텁 이미 정상 렌더.

## Complexity Tracking

해당 없음 (위반 0).
