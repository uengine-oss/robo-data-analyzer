# Data Model: stereotype 라벨 부여

## 노드 라벨 구조 (변경 후)

| 노드 | 라벨 (순서) | 비고 |
|---|---|---|
| stereotype 보유 함수 | `[<antlr타입>, <stereotype>]` 예 `[FUNCTION, Command]` | 타입 먼저 |
| stereotype 보유 그릇 | `[<antlr타입>, <stereotype>]` 예 `[FILE, Service]` | |
| stereotype 미보유(구문 등) | `[<antlr타입>]` | 역할 라벨 미부여 |
| 외부 stub | `[FUNCTION, EXTERNAL]` / `[MODULE, EXTERNAL]` | stereotype 없음(EXTERNAL은 별개) |

- 식별 속성 `id`·기존 관계 불변. stereotype **속성도 유지**(무중단).

## apply_stereotype_labels 동작

- 입력: `(node_id, stereotype)` 쌍들.
- 빈 stereotype 건너뜀.
- 같은 stereotype끼리 묶어 1 배치: `UNWIND $items AS item MATCH (node {id: item.id}) SET node:`<stereotype>``.
- 값=`coerce_stereotype` 통제값(식별자 형태) + 백틱 방어.

## 현재 그래프 마이그레이션 (재분석 없이)

distinct stereotype별:
```cypher
MATCH (n) WHERE n.stereotype = $v AND NOT $v IN labels(n)
CALL apoc... // APOC 불요 — 값별 직접 SET
```
실제: 값별로 `MATCH (n) WHERE n.stereotype = '<값>' SET n:`<값>``. 사전에 distinct stereotype 목록 조회 후 값별 실행(quickstart 참조).

## 적재 시점 (labels[0]=타입 보장)

```
step2 load_ast      : upsert_code_nodes / upsert_modules  → 타입 라벨 MERGE (먼저)
step6 analyze_code  : update_summaries                    → stereotype 라벨 (나중)
step7 summarize     : upsert_modules(재호출)              → 모듈 stereotype 라벨 (나중)
```
