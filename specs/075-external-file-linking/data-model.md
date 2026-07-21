# Data Model: External File Linking

## ID helpers

```python
FILE_NS = "file:"

project_file_id(project: str, rel_path: str) -> str
external_chunk_id(file_id: str, key: str, ordinal: int = 0) -> str
unresolved_ref_id(*identity_parts: str) -> str
candidate_link_id(source_id: str, target_id: str, model: str) -> str
```

All helpers normalize Unicode/path separators deterministically and reject empty project/path or absolute/root-
escaping paths. Hash IDs use SHA-256, not SHA-1, and store the unhashed semantic fields as properties.

## Node dataclasses

### ProjectFile

| Field | Type | Contract |
|---|---|---|
| id | str | `file:` namespace |
| name | str | display basename |
| project | str | analysis project identity |
| relpath | str | POSIX relative path |
| ext | str | lowercase suffix or empty |
| category | Literal | source/sql_xml/config/artifact_xml/document/custom/binary/unknown |
| status | Literal | registered/analyzed/unanalyzed/failed |
| reason | str | empty only for successful/registered state |
| label | str | fixed `PROJECT_FILE` |

### ExternalChunk

| Field | Type | Contract |
|---|---|---|
| id | str | file-derived opaque ID |
| name | str | display value, equal to key for anchor; `part N` for part |
| key | str | anchor direct-use key; empty for part |
| kind | Literal | sql/config/artifact/custom |
| label | Literal | SQL_STATEMENT/CONFIG_SECTION/ARTIFACT_CHUNK/CHUNK |
| text | str | length <= 1200 |
| file_path | str | project-relative source |
| start_line/end_line | int | 1-based, ordered; 0 only when source format cannot provide line |
| part_index | int | 0 anchor, positive part |
| anchor_id | str | empty on anchor, exact anchor ID on part |
| full_len | int | original text character length |
| content_hash | str | SHA-256 of complete original text, same on anchor/parts |
| sql_type | str | SQL only |
| related_fqns | tuple[str, ...] | artifact only |

### DocumentNode

| Field | Type | Contract |
|---|---|---|
| id | str | file-derived opaque ID |
| label | Literal | DOCUMENT/DOC_SECTION/DOC_CHUNK |
| name | str | title/display name |
| title | str | document/section heading |
| text | str | chunk text; <=1200 for DOC_CHUNK |
| file_path | str | project-relative source |
| start_line/end_line | int | source coordinates when available |
| order | int | stable sibling order |
| parent_id | str | empty for DOCUMENT; parent for section/chunk |
| content_hash | str | source/chunk SHA-256 |

### UnresolvedRef

| Field | Type | Contract |
|---|---|---|
| id | str | deterministic hash identity |
| label | str | `UNRESOLVED_REF` |
| name | str | key or target type for display |
| target_type | str | expected target/format |
| key | str | unresolved key, may be empty for file-level failure |
| reason | Literal | key_not_found_in_code/table_not_in_ddl/parse_failed/binary_unanalyzed/pattern_rejected/dynamic_key_suspected/optional_dependency_missing/path_rejected |
| file_path | str | evidence file |
| detail | str | bounded, non-secret diagnostic |

### CandidateLink

| Field | Type | Contract |
|---|---|---|
| id | str | deterministic source+target+model identity |
| label | str | `CANDIDATE_LINK` |
| name | str | display summary |
| source_id/target_id | str | candidate endpoints stored as properties, not a graph fact edge |
| score | float | finite 0..1 |
| model | str | embedding model ID |
| status | str | fixed `candidate` |
| reason | str | retrieval evidence |

## Relationship dataclasses

All classes are frozen and have typed endpoints. Neo4j identity is `(source_id, relation, target_id)`.

| Class | Relation | Direction | Properties |
|---|---|---|---|
| CallsSql | CALLS_SQL | CodeNode → SQL_STATEMENT | evidence_file, evidence_line, via_const |
| UsesConfig | USES_CONFIG | CodeNode → CONFIG_SECTION/CHUNK | evidence_file, evidence_line, via_const |
| ReferTo | REFER_TO | CodeNode→ARTIFACT_CHUNK for direct keys; ARTIFACT_CHUNK→Module for exact class/file FQN | evidence_file, evidence_line, matched_fqn |
| Mentions | MENTIONS | DOC_CHUNK → code/TABLE | matched_text, evidence_file, evidence_line |
| HasChunk | HAS_CHUNK | PROJECT_FILE → chunk/document root | none |
| HasPart | HAS_PART | anchor → part | part_index |
| HasSection | HAS_SECTION | DOCUMENT/section → section/chunk | order |
| ReadsWrites | READS/WRITES | SQL_STATEMENT or CodeNode → TABLE | existing dml/evidence contract |

`CANDIDATE_LINK` deliberately has no candidate relationship.

## WorkflowState additions

```python
project_files: list[ProjectFile]
external_chunks: list[ExternalChunk]
document_nodes: list[DocumentNode]
external_edges: list[ExternalEdge]
unresolved_refs: Annotated[list[UnresolvedRef], operator.add]
candidate_links: list[CandidateLink]
```

`ExternalEdge` is a typing union of the seven relationship dataclasses, not a runtime generic edge class.

## State transitions

```text
ProjectFile.registered
  -> analyzed     parser returned nodes/chunks
  -> unanalyzed   binary/unsupported/optional dependency missing
  -> failed       selected parser attempted and failed

ExternalSkill proposal
  -> rejected     unsafe/invalid/overfit/no consensus
  -> accepted     deterministic validation and consensus pass
  -> persisted    atomic write under agents/skills/external/<slug>/SKILL.md
```

Step3 owns initial `project_files`/`external_chunks`. Step4 is their only sequential refinement owner: it returns the
complete prior collection plus validated custom chunks. Structural `external_edges` and explicit `unresolved_refs`
remain additive reducer channels across steps 3–5.

No transition silently drops the file or changes a candidate into a fact.
