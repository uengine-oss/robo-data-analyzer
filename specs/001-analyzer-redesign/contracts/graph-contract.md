# 제품 그래프 계약

## 내부 공통 속성

| 속성 | 대상 | 의미 |
|---|---|---|
| `_id` | 모든 노드 | 안정적인 merge와 관계 endpoint |
| `_owner` | 모든 Analyzer 노드·관계 | 공유 DB에서 Analyzer 소유 데이터 구분 |
| `_search_embedding` | routine | 직접 vector 검색 |

## 노드 속성

| 종류 | 표시 속성 |
|---|---|
| Package | `name`, `qualified_name` |
| source container·routine·statement | `name`, `file_path`, `start_line`, `end_line`, `code_text` |
| routine 추가 | `signature`, `summary`, `stereotype` |
| Variable·Constant 추가 | `data_type`, `value` |
| Table | `datasource`, `database`, `schema`, `name`, `qualified_name`, `logical_name`, `table_type`, `description`, `summary` |
| Column | `name`, `logical_name`, `data_type`, `nullable`, `primary_key`, `unique`, `default_value`, `description`, `summary` |
| Rule | `line`, `condition`, `effects`, `condition_description`, `effect_descriptions`, `code_text` |
| ExternalChunk | `key`, `kind`, `file_path`, `start_line`, `end_line`, `code_text` |

빈 optional 값은 property로 만들지 않는다. `false`와 `0`은 의미 있는 값이므로 보존한다.

## 관계

| 관계 | 방향 | 추가 속성 |
|---|---|---|
| `PARENT_OF` | parent → child | 없음 |
| `NEXT` | 이전 구문 → 다음 구문 | 없음 |
| `CALLS` | caller → callee | 없음 |
| `EXTENDS` | subtype → base | 없음 |
| `IMPLEMENTS` | type → interface | 없음 |
| `REFERENCES` | source → 참조 대상 | 없음 |
| `INIT_BY` | 변수·상수 → 초기값이 참조한 선언 | 없음 |
| `READS` | routine·Rule → Table | `operations` |
| `WRITES` | routine·Rule → Table | `operations` |
| `HAS_COLUMN` | Table → Column | 없음 |
| `FK` | source Table → target Table | `from_column`, `to_column` |
| `HAS_RULE` | routine → Rule | `order` |

## 공개 불변식

1. 모든 관계 endpoint는 같은 batch의 노드에 존재한다.
2. 노드 `_id`는 batch 안에서 유일하다.
3. FK는 column pair까지 identity에 포함하고 다른 관계는 endpoint별 하나다.
4. 중복 읽기·쓰기 관계의 `operations`는 정렬된 집합으로 합친다.
5. source 기반 노드의 `code_text`는 자기 line 범위와 일치한다.
