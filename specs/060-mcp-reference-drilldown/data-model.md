# Data Model

## cluster_retrieve response

```json
{"clusters":[{"score":0.0,"nodes":[{"id":"...","name":"...","label":"FUNCTION","logical_name":"...","summary":"...","role":"anchor","relevance":0.0,"reads":[],"writes":[],"calls":[],"rules":[{"statement":"...","examples":[{"given":"...","when":"...","then":"...","tables":[{"name":"...","op":"READ"}]}]}],"evidence":[]}]}],"meta":{}}
```

금지 키: `code_text`, `columns`, `search_text`, `embedding`, `element_id`, `graph_owner`.

## node_detail response

```json
{"node":{"id":"...","labels":["FUNCTION"],"name":"...","properties":{},"source":{"available":true,"file_path":"...","start_line":1,"end_line":20,"code_text":"..."},"rules":[],"columns":[],"relationships":[{"type":"CALLS","direction":"out","node":{"id":"...","name":"...","labels":["FUNCTION"]},"properties":{}}]}}
```

오류:

```json
{"error":{"code":"NODE_NOT_FOUND","message":"node not found"}}
```

## invariants

- `source.available=true`이면 네 좌표/본문 필드가 모두 유효하다.
- 관계 정체성은 `(type,direction,other.id)`이며 동일 실제 관계를 중복 직렬화하지 않는다.
- RULE/EXAMPLE은 대상 코드 노드가 소유한 example prefix 경계를 지킨다.
- 모든 응답은 ensure_ascii=False compact JSON 문자열 한 벌이다.
