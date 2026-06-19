# Quickstart: 검증 가이드 (그래프 스키마 정합)

**원칙**: 단위 테스트 아님 — 실제 파이프라인 돌려 그래프를 **원문 소스와 대조**([[principles]] §6). 대상 = `data/source/RWIS`(9개 .sql, 30+ 라벨).

## 사전 준비
- 풀스택 기동(Neo4j·antlr 8081·analyzer 5502·catalog 5503·gateway 9000) — [[manual]] §3.
- 분석: `cd robo-data-analyzer && ./.venv/Scripts/python.exe cli_scripts/analyze_data_dir.py RWIS --strategy dbms` (NDJSON 스트림 관찰, ~4분). ★매 run wipe → 전체 재빌드.

## 검증 시나리오 (Neo4j 직접 쿼리 + 원문 대조)

### V1 — NEXT 흐름 복원 (US1 / SC-001)
```cypher
MATCH (a)-[:NEXT]->(b) WHERE a.id CONTAINS ':' RETURN a.start_line, b.start_line LIMIT 20
```
기대: 한 프로시저의 인접 형제 구문이 라인 순서대로 연결. framework 분석엔 구문 NEXT 0.

### V2 — 구조 노드 존재 + LLM 스킵 (US2 / SC-002)
```cypher
MATCH (n:DECLARE) RETURN count(n)              // > 0 (선언부 있는 소스면)
MATCH (n) WHERE n.summary IS NULL AND labels(n)[0] IN ['DECLARE','TRY','TRIGGER_BLOCK'] RETURN count(n)
```
기대: DECLARE 노드 존재. 구조 노드는 summary 비어도 정상(분석 누락 아님). 표시는 "변수 선언부 (구조)".
원문 대조: .sql 의 DECLARE 블록이 그래프에 노드로 있는지 눈으로 확인.

### V3 — HAS_MEMBER 범위 = 루틴만 (US4 / SC-004)
```cypher
MATCH (g)-[:HAS_MEMBER]->(m) RETURN DISTINCT labels(m)[0]
```
기대: 결과 라벨이 루틴(PROCEDURE/FUNCTION/METHOD/TRIGGER)·데이터(VARIABLE/…)만. **SELECT/IF 등 구문 0건**.

### V4 — 관계 통합 + 옛 이름 잔재 0 (SC-005)
```cypher
CALL db.relationshipTypes() YIELD relationshipType RETURN relationshipType
```
기대: `HAS_MEMBER`·`BELONGS_TO` 존재, `HAS_FUNCTION`·`HAS_VARIABLE`·`BELONGS_TO_PACKAGE` **0건**.
소비처: frontend·catalog·architect 전수 grep 으로 옛 이름 0 확인.

### V5 — 그릇 라벨 정체대로 (US4)
```cypher
MATCH (g)-[:HAS_MEMBER]->() RETURN DISTINCT labels(g)[0]
```
기대: DBMS=FILE(·PACKAGE), framework=CLASS/FILE. MODULE 강제 통일 아님.

### V6 — 룰 중복·열화 (US5 / SC-006)
- 한 프로시저의 RULE 노드 텍스트를 모아 **의미 중복 없는지** 확인.
- 하위 구문 세부가 부모 룰에서 **요약 열화로 손실 안 됐는지** 원문 대조.

### V7 — 회귀 0 (SC-005)
- frontend(standalone 3000 + Electron) 에서 그래프·네비게이터·노드패널 정상 렌더(드래그·캔버스 불변).
- ★프론트 UI 는 내가 직접 자가검증(Playwright 스크린샷, [[manual]] §3).
- catalog `/robo/graph` 정상 응답.

## 합격 기준
V1~V7 전부 통과 + 원문 대조에서 누락/왜곡/중복/열화 0. 실패 시 AST·LOGS·그래프 3단 추적([[principles]] §6).
