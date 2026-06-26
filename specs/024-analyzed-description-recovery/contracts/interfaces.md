# Phase 1 Contracts: 내부 인터페이스 (식별자 정규화)

analyzer 내부 — 외부 API 변경 없음. 그래프 노드 id 형식이 바뀌나 소비처는 불투명 read(D3).

## fqn 헬퍼 (신규 단일진실)
```python
def fqn(*parts: str) -> str
# 비어있지 않은 조각만 "." join. 빈 구성요소 생략(선두/중복 점 0).
# 위치: shared/ (graph_nodes·neo4j·step 전부 import 가능한 곳). props.py 인접 또는 신규 shared/ids.py.
```
**계약**: 결정론. 같은 입력 → 같은 출력. db 유무로 자연 적응(전략무관).

## data_models/graph_nodes.py
```python
Table.id  -> fqn(self.db, self.schema, self.name)        # 직접 f-string 조립 폐기
# Column.id 생성(DDL 파싱 지점)도 fqn(db, schema, table, col)로
```
**계약**: 노드 식별자가 헬퍼 단일 출처. 빈 db → 선두점 없음.

## shared/neo4j/nodes/table.py · column.py
```python
# MERGE_QUERY 입력 item을 nonempty()로 필터(빈 db/comment 키 미저장). coalesce 보존의미 유지.
async def update_analyzed_descriptions(client, updates) -> int   # 매칭 건수 반환
# SET_ANALYZED_QUERY가 RETURN count(*) → 0건/부분이면 호출측이 경고
```
**계약**: 빈값미저장 일관. update 매칭 0건이면 표면화(조용한 no-op 금지).

## step7 enrich_table_description_step.py
```python
# table write id: fqn(r.db, r.schema_, r.name)        (현재 f"{...}.{...}" 폐기)
# column write id: fqn(table_fqn, cname)
# update_analyzed_descriptions 반환 매칭수 < 후보수 → log_warning(유실 표면화)
```
**계약**: write id == 노드 id(동일 헬퍼). insights table_fqn 원천도 동일 헬퍼(구현 시 핀포인트).

## 불변(회귀 게이트)
- 코드 노드 id(`PRC...:...:MERGE:107` 등)는 **불변**(이 작업은 TABLE/COLUMN id만).
- embed_step `c.id.split(".")[-2]` 음수 인덱스 → 안전(불변).
- 외부 소비처(catalog/frontend) graph read 불투명 → 재분석 신선 read.
