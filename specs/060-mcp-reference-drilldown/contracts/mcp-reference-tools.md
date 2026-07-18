# MCP Contract: legacy reference tools

## `cluster_retrieve`

- Input: `query: string`, `database?: string`.
- Output: compact JSON text. 목록 필드는 `data-model.md`를 따른다.
- Empty query: `{"error":{"code":"EMPTY_QUERY",...}}`.
- Selection/ranking semantics: spec 057 그대로.

## `node_detail`

- Input: `node_id: string`, `database?: string`.
- Output: compact JSON text with `node` or `error` exactly one.
- Calls are repeatable and side-effect free.
- Database selection is request-local. A call must not change another request's target DB.
- Code source coordinates are never omitted. Incomplete code provenance is an explicit error.

## Compatibility

- MCP URL and compact string transport are unchanged.
- `cluster_retrieve.detail` and full/tiered modes are removed in the coordinated producer/consumer change.
- Consumers must fetch code/columns only through `node_detail` and may call it zero or many times.
