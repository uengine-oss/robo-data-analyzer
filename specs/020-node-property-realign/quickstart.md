# Quickstart 검증: 노드 속성 정체화 (재분석 후 원문 대조)

전제: 풀스택 기동([[manual]] §3) + RWIS 재분석(★Neo4j wipe — 사용자 OK 후). 검증 = 단위테스트 아닌 **실제 그래프 속성 원문 대조**.

## V1. 구문 노드 잡속성 0 (SC-001, FR-001/002/003/004)
Neo4j:
```cypher
MATCH (n:SELECT) RETURN keys(n) LIMIT 5
```
기대: `id·name?·file_path·start_line·end_line·code_text·summary·stereotype·logical_name·notes?`만. **annotations·params·return_type·signature·loc·god_flag·fan_in·fan_out·table_coupling 0건**.

## V2. 루틴 노드 속성 (FR-002/004)
```cypher
MATCH (n) WHERE n:PROCEDURE OR n:FUNCTION OR n:METHOD OR n:TRIGGER
RETURN n.name, n.params, n.return_type, n.risk_level, n.in_call_cycle LIMIT 10
```
기대: params·return_type(있으면)·risk_level·in_call_cycle 존재. signature/loc/god_flag/fan_* 없음.

## V3. notes 통합 (FR-003, SC-005)
```cypher
MATCH (n) WHERE n.notes IS NOT NULL RETURN count(n);
MATCH (n) WHERE n.annotations IS NOT NULL OR n.comment IS NOT NULL OR n.signature IS NOT NULL RETURN count(n)  // = 0 기대
```

## V4. 위험도 품질 불변 (SC-003)
변경 전 `risk` 분포 vs 변경 후 `risk_level` 분포 동일 확인(같은 RWIS 입력):
```cypher
MATCH (n) WHERE n.risk_level IS NOT NULL RETURN n.risk_level, count(*) ORDER BY n.risk_level
```
기대: low/medium/high 분포가 019 시점 risk와 동일.

## V5. table_insights 분배·블롭 폐기 (SC-002, FR-005)
```cypher
MATCH (n) WHERE n.table_insights_json IS NOT NULL RETURN count(n)   // = 0 기대
MATCH (c:COLUMN) WHERE c.analyzed_description <> '' RETURN count(c)  // > 0 (인사이트 분배됨)
```

## V6. 소비처 UI (FR-006, SC-004) — Playwright 자가검증([[manual]] §3)
노드패널: 루틴=params/return/risk_level 표시, 구문=잡속성 안 뜸, notes 표시. 그래프 건강보기=risk_level 색. 회귀 0.

## V7. 회귀 가드
017 id·018 관계·019 라벨 불변(`MATCH` 라벨/관계 수 동일). 프롬프트 byte-identical(`check_byte_identical.py`).
