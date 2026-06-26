# Quickstart: 024 검증 (식별자 정규화 + 분석설명 복구)

## 전제
- analyzer 루트 `d:\work\robo\project\robo-data-analyzer`, branch refactor, venv `.venv`.
- Neo4j 기동(neo4j/an1021402, DB=neo4j). LLM frentis-ai-model 가동.
- 분석 입력: `d:\work\robo\project\data\` (RWIS DDL+소스, 기존 분석대상 재사용).

## 1. 수정 전 현황 박제 (회귀 기준)
```
# 그래프 직접쿼리 (현재 = 버그 상태)
MATCH (t:TABLE) RETURN count(t),
  sum(CASE WHEN coalesce(t.analyzed_description,'')<>'' THEN 1 ELSE 0 END)  # 6/16
MATCH (c:COLUMN) ... analyzed_description                                    # 61/153
MATCH (t:TABLE) WHERE t.id STARTS WITH '.' RETURN count(t)                   # 16 (선두점)
```

## 2. 수정 후 재분석 (★실행검증, 헌법 VI)
```
cd d:\work\robo\project\robo-data-analyzer
.venv\Scripts\python.exe cli_scripts\analyze_data_dir.py <대상> --strategy dbms
```
- 로그 `logs/analysis.run/latest/analyzer.log` step7: table_description done 수 = 노드 반영 수 일치 확인.

## 3. 검증 (SC 대조 — 그래프 직접쿼리)
| 검증 | 기대 |
|---|---|
| R/W 테이블 중 analyzed_description 빈 것 | **0** (특히 RDD01DD_TB 채움) |
| R/W 컬럼 analyzed_description 유실 | **0** |
| 선두점 id (`t.id STARTS WITH '.'`) | **0** |
| 빈 db/comment 키 저장 | **없음**(키 부재) |
| update 매칭 0건 시 경고 로그 | **남음** |
| 기존 채워졌던 6개·코드노드 id | **회귀 0** |

## 4. 소비처 회귀 (선택)
- catalog `/robo/graph` 응답·frontend 그래프에서 테이블 노드 정상 표시(불투명 id read).

## 상세
- 설계: [research.md](research.md) · 엔티티: [data-model.md](data-model.md) · 계약: [contracts/interfaces.md](contracts/interfaces.md)
