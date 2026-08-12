# DataObjectUsageEvidence contract

```text
source_id: stable producer node id
producer: parser_ast | linked_edges
datasource: run-scoped datasource when resolved
raw_reference: source spelling of the physical object
schema: optional exact schema spelling
name: required exact object spelling
database_link: optional exact database-link spelling
alias: optional exact alias spelling
access: READ | WRITE
evidence_file: source-relative file path
evidence_line: positive source line
qualified_columns[]:
  raw_reference: exact qualifier.column spelling
  qualifier: explicit source qualifier
  name: exact column spelling
  evidence_line: positive source line
```

Parser는 구문 사실만 생산하고 Analyzer는 qualifier를 같은 node의 alias/object에 exact
case-insensitive match한다. Catalog identity가 확정되기 전에는 schema/link를 버리거나 bare
name으로 합치지 않는다.
