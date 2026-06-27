# Quickstart: stereotype 라벨 검증

## 1. 코드 적용 후 py_compile

```bash
cd robo-data-analyzer
.venv/Scripts/python.exe -m py_compile shared/neo4j/nodes/stereotype_label.py shared/neo4j/nodes/code_node.py shared/neo4j/nodes/module.py
```

## 2-A. 현재 그래프 마이그레이션 (재분석 없이 즉시 검증)

distinct stereotype 목록 조회 후 값별 SET (Neo4j Browser 또는 python driver, bolt://127.0.0.1:7687 neo4j/an1021402, DB=neo4j):

```cypher
// 1) 부여
MATCH (n) WHERE n.stereotype IS NOT NULL AND n.stereotype <> ''
WITH DISTINCT n.stereotype AS st
CALL apoc.create.addLabels(...) // APOC 있으면. 없으면 값별 아래 반복:
// MATCH (n) WHERE n.stereotype = '<값>' SET n:`<값>`
```
APOC 없으면 python으로 distinct 값 받아 값별 `SET n:`<값>`` 실행.

## 2-B. 또는 framework 재분석 (영구 코드경로 실증, ~4분)

```bash
.venv/Scripts/python.exe cli_scripts/analyze_data_dir.py zapamcom --strategy framework
```
(neo4j DB 덮어씀 인지.)

## 3. 검증 쿼리 (ground truth)

```cypher
// 보유 노드 labels[0]=타입 확인
MATCH (n) WHERE n.stereotype IS NOT NULL AND n.stereotype <> ''
RETURN labels(n)[0] AS first, n.stereotype AS st, count(*) AS c
ORDER BY c DESC
// 기대: first ∈ {FUNCTION, FILE, ...}(타입), st ∈ {Command, Service, ...}, st ∈ labels(n)
```

검증 통과 기준:
- SC-001: stereotype 보유 노드 100%가 stereotype을 라벨로 보유.
- SC-002: 보유 노드 100% `labels[0]`=antlr 타입.
- SC-003: 속성 `stereotype`도 그대로(무중단).
- SC-004: `id`·관계 불변.
