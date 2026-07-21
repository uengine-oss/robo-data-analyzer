# Frontend Contract

Implementation belongs to a separate Frontend SDD before P6 source changes. This document is the producer/consumer
boundary owned by Analyzer spec 075.

## Progress

`analyzer.ts` maps:

- `step3.load_external` → `외부 파일 읽는 중`
- `step5.link_external` → `외부 파일 연결하는 중`

## Node display

New labels receive stable icons and `resolveNodeName` resolves explicit `name`, then `key`/`title`, then ID fallback.
No label-specific parsing of opaque IDs is allowed.

## Visibility

Default hidden node labels:

`PROJECT_FILE`, `SQL_STATEMENT`, `CONFIG_SECTION`, `ARTIFACT_CHUNK`, `CHUNK`, `DOCUMENT`,
`DOC_SECTION`, `DOC_CHUNK`, `UNRESOLVED_REF`, `CANDIDATE_LINK`.

Default hidden relationships:

`CALLS_SQL`, `USES_CONFIG`, `REFER_TO`, `MENTIONS`, `HAS_CHUNK`, `HAS_PART`, `HAS_SECTION`.

Existing READS/WRITES remain visible. “Show hidden” exposes the support graph through the existing single policy;
no second filter implementation is added.

## Live event acceptance

- node and relationship events merge by ID without duplicate canvas entities.
- every relationship endpoint exists or is deferred through the existing event ordering mechanism.
- selecting a new relationship type includes only its endpoints and excludes unrelated edges.
- console errors and failed network requests are zero in the tested flow.

## WIP guard

Existing dirty `package.json`, `graphCanvas.ts`, `scripts/`, spec057, panel projection files and tsconfig are not
reformatted, reverted or staged. Target-file preimage hashes are recorded immediately before P6.
