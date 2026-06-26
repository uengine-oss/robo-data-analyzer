# Quickstart / 검증: 027-rw-grounded-insights

## 재분석 1회 (깨끗한 data + ddl)
```bash
cd robo-data-analyzer
.venv/Scripts/python.exe cli_scripts/analyze_data_dir.py zapamcom --strategy framework
```

## 검증 (Neo4j, neo4j/an1021402)
- **SC-001 컬럼 채움**: COLUMN.analyzed_description 빈값(코드에서 쓰이는 컬럼 중) 이전 10건 → 감소. cust_num/acnt_num 등 채워짐.
- **SC-002/003 가짜 테이블 0**: AFFECTS_TABLE 의 table_name 이 전부 실테이블(7종) 안. `e800_dbio_*`·`*_call_*_proc` 같은 함수명 0. reads/writes 빈 함수(c300) AFFECTS_TABLE 0.
- **경고**: `analyzed_desc_unmatched` 건수 감소. reads/writes 밖 드롭 시 신규 경고 표면화.
- **SC-004 회귀**: 분류기/baseline 무변경.

## 빠른 쿼리
```python
# 가짜 테이블 타깃 (실테이블 아닌 AFFECTS_TABLE)
MATCH (e:EXAMPLE)-[:AFFECTS_TABLE]->(t) WHERE NOT t:TABLE RETURN count(*)   # 0 이어야
# 컬럼 채움률
MATCH (c:COLUMN) RETURN sum(CASE WHEN c.analyzed_description IS NULL OR trim(c.analyzed_description)='' THEN 1 ELSE 0 END) AS empty, count(c) AS total
```
