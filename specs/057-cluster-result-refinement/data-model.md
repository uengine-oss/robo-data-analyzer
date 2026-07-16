# Data Model — 057 계층화 페이로드 (MCP `detail='tiered'` 기본)

응답 전체 = **JSON 텍스트**(compact, 비이스케이프):
`{"clusters":[{"score":float,"nodes":[Node...]}],"meta":{...기존 meta 그대로...}}`

Node 는 세 형태 중 하나. 공통 원칙: 내부 전용 필드(`search_text`,`element_id`) 없음,
값이 없으면 빈 값 명시(누락 은폐 금지).

## 1. AnchorFunction (role="anchor")

| 필드 | 타입 | 출처 | 비고 |
|---|---|---|---|
| id | str | 코어 | `code:<file>:<name>` — 소속 파일이 id 에 내재 |
| name | str | 코어 | |
| logical_name | str | corpus by_id | 빈 값 가능 |
| label | str | 코어 | FUNCTION 등 |
| stereotype | str | 코어 | |
| summary | str | 코어 | |
| code_text | str | corpus by_id | **원문 코드 — 필수(사용자 결정)** |
| reads / writes | [str] | 코어 | 테이블 물리명 |
| calls | [str] | 코어 | 게이트 통과 이웃 이름 |
| relevance | float | 코어 | |
| role | "anchor" | 코어 | |
| rules | [Rule] | 코어 `_rules_for` | **전량** + 동일 statement 병합 |

Rule = `{statement:str, examples:[{given,when,then,tables:[{name,op}]}]}` — 전량.

## 2. EvidenceFunction (role="evidence", label≠TABLE)

AnchorFunction 과 동일하되:

| 차이 | 값 |
|---|---|
| rules | `[]` 고정 (형태 안정 — 아키텍트 `_compact_nodes` rulesCount=0 소비 확인) |
| evidence | `[{relation, direction, support_root_id(이름 아님 id)}]` — **포함 근거 명시** |
| code_text | 포함 (사용자 결정: 원문 코드 무조건) |

## 3. TableNode (label="TABLE")

| 필드 | 타입 | 출처 | 비고 |
|---|---|---|---|
| id | str | 코어 | `db:<물리명>` |
| name | str | 코어 | 논리명 우선 |
| physical_name | str | 코어 | |
| label | "TABLE" | 코어 | |
| summary | str | 코어 | analyzed_description 우선 |
| columns | [Column] | **신규 HAS_COLUMN 배치 쿼리** | 컬럼 미수집 테이블은 `[]` |
| relevance / role | float / "evidence" | 코어 | 앵커 점수 계승(기존 계약) |
| evidence | [{anchor_id, relation, direction}] | 코어 | 어느 앵커가 읽고/쓰나 |
| rules | `[]` 고정 | — | 형태 안정 |

Column = `{name:str, dtype:str, is_primary_key:bool, logical_name:str, description:str}`
(description = analyzed_description 우선, 없으면 description, 없으면 comment, 없으면 "").

## 상태·전이

없음(읽기 전용 변환). 캐시 무관 — 요청마다 코어 결과를 변환.

## 검증 규칙 (테스트 대상)

- V1: tiered 결과에 `search_text`/`element_id` 키가 어떤 노드에도 없다.
- V2: 모든 FUNCTION(anchor+evidence)에 `code_text` 키 존재(값은 빈 문자열 허용).
- V3: 모든 TABLE 에 `columns` 키 존재, 각 Column 에 5필드 전부 존재.
- V4: anchor 의 rules 는 코어 원형과 example 개수까지 동일(전량 보존), evidence 는 `[]`.
- V5: 동일 statement rule 이 입력에 있으면 출력에서 1개, examples 합집합·순서 보존.
- V6: 직렬화 텍스트에 `\uXXXX` 이스케이프·연속 공백 indent 없음(compact).
- V7: `detail='full'` 은 코어 원형 그대로(변환 0). `'summary'`/`_slim_clusters` 는 삭제됨
  (호출자 0 실측 — FR-007).
