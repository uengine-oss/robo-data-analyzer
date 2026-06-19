# Quickstart: 검증 가이드 (용어 정체화)

**원칙**: 단위테스트 아님 — 실제 파이프라인 돌려 그래프·로그를 **원문/실제 종류와 대조**([[principles]] §6). 대상 = `data/source/RWIS`(DBMS) + (가능 시) framework 샘플. ★분석 실행·커밋 = 사용자 OK 후.

## 사전 준비
- 풀스택(Neo4j·antlr 8081·analyzer 5502·catalog 5503·gateway 9000) — [[manual]] §3.
- 분석: `./.venv/Scripts/python.exe cli_scripts/analyze_data_dir.py RWIS --strategy dbms` (★매 run wipe → 전체 재빌드).

## 검증 시나리오

### V1 — 그릇 라벨 정체대로 (US1 / SC-001)
```cypher
MATCH (g)-[:HAS_MEMBER]->() RETURN DISTINCT labels(g)
```
기대: DBMS 그릇 라벨에 `FILE` 포함(`:MODULE` 단독 아님). framework=CLASS/FILE. 외부 stub(:MODULE:EXTERNAL)만 MODULE 잔존.
- frontend(standalone/Electron): .sql 파일 노드패널/네비 = "파일", Java=클래스 표시. (★UI = 내가 Playwright 자가검증, [[manual]] §3.)

### V2 — 소비처 회귀 0 (US1 / SC-002)
- 네비게이터 그릇 섹션이 **빠짐없이** 채워짐(타입집합 `isContainerNode` 로 식별). templateRouter 가 FILE→FileCard·CLASS→ModuleCard 정상.
- catalog `/robo/graph` 정상, architect ingestion 0-match 없음.

### V3 — 로그/스트림 실제 종류 (US2 / SC-003·004)
- DBMS 분석 진행 로그(`logs/analysis.run/latest/analyzer.log`)·NDJSON 스트림에서 node_event 종류가 실제(SELECT/IF/PROCEDURE/FILE)로 — `"label":"FUNCTION"` 고정 표기 0.
- 동일 노드의 스트림 종류 = Neo4j 영속 라벨(쿼리 대조) 일치.

### V4 — 분석단위 용어 중립 (US3 / SC-005)
- 코드 grep: 분석단위 지칭 `Function`/`functions`/`analyze_function`/`function_*` 잔재 0 (실제 루틴 의미 `:FUNCTION`/`:PROCEDURE`·`_ROUTINE_LABELS`·ontology 루틴 role 은 유지=정상).
- 소비처(frontend·catalog·architect) 동작 회귀 0.

### V5 — KV 캐시 불변 (constitution V)
- 3분할 프롬프트 system 본문 byte-identical(`check_byte_identical.py` 또는 `diff -q` 0) — 개명이 프롬프트 텍스트를 안 바꿨는지.

### V6 — 017/018 불변
- id 단일·HAS_MEMBER/BELONGS_TO/NEXT/구조노드(DECLARE) 등 018 결과 그대로.

## 합격 기준
V1~V6 통과 + 원문/실제종류 대조에서 왜곡/누수/회귀 0. 실패 시 AST·LOGS·그래프 3단 추적([[principles]] §6).
