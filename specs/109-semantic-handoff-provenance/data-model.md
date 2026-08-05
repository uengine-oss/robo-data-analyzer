# Data Model: Semantic Handoff and Table Effects

## AnalysisContext

| Field | Type | Owner | Meaning |
|---|---|---|---|
| `summary` | optional string | summary response | concise UI/range-compaction description |
| `search_text` | optional string | summary response | detailed semantic retrieval and CALLS handoff text |

Neither field may fill the other when absent.

## LLM Table Effect Candidate

| Field | Type | Rule |
|---|---|---|
| `table` | string | exact injected table ID |
| `op` | optional enum | `READ/INSERT/UPDATE/DELETE/MERGE/TRUNCATE`; null if exact op is not grounded |

This remains the entire LLM-owned schema.

## Final AFFECTS_TABLE Effect

| Property | Values | Authority |
|---|---|---|
| `access` | `READ`, `WRITE`, `READ_WRITE` | deterministic direct R/W membership, narrowed by a valid exact candidate |
| `op` | supported exact op or `UNKNOWN` | scanner single-op first; valid LLM candidate second |
| `op_source` | `SCANNER`, `LLM_INFERRED`, `UNRESOLVED` | deterministic finalizer |

### Finalization Matrix

| Direct evidence | Scanner single op | Candidate | Final |
|---|---|---|---|
| read-only | absent | absent or write | `READ / READ / SCANNER` |
| write-only | exact | any | `WRITE / exact / SCANNER` |
| write-only | absent | consistent write | `WRITE / candidate / LLM_INFERRED` |
| write-only | absent | absent or READ | `WRITE / UNKNOWN / UNRESOLVED` |
| read+write | exact | any | op-derived access / exact / SCANNER |
| read+write | absent | valid exact | op-derived access / candidate / LLM_INFERRED |
| read+write | absent | absent | `READ_WRITE / UNKNOWN / UNRESOLVED` |

## Table Insight Role

`READ_WRITE` replaces `WRITE-then-READ`. It asserts coexistence only, never sequence.

