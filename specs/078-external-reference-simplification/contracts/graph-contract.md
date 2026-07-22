# 078 Graph Contract — 외부 파일 (supersedes 075 graph-contract)

## Nodes (외부 계약 전체)

### PROJECT_FILE (유지)
`id=project_file_id(project, relpath)`, `name`, `project`, `relpath`, `ext`,
`category`(source|sql_xml|config|artifact_xml|document|custom|binary|unknown),
`status`(registered|analyzed|unanalyzed|failed), `reason`(비정상 status 필수).
파일 수준 실패/미지원의 유일한 표현. UNRESOLVED_REF 노드는 존재하지 않는다.

### EXTERNAL_CHUNK (신규 단일 라벨)
`id=external_chunk_id(file_id, char_start, char_end)`, `name`, `keys: list[str]`,
`semantic_kind`(xml_element|yaml_entry|json_entry|md_section|record|row|agent_segment|
derived_document), `text`, `file_path`, `char_start`, `char_end`, `start_line`,
`end_line`, `provenance`(source_span|derived_document), `source_hash`, `content_hash`.

불변식(source_span): `text == decoded_source[char_start:char_end]`,
`content_hash == sha256(text)`, 같은 파일 내 span 무중첩, 크기 상한 없음.
derived_document: `char_start=char_end=0`, 직접 REFER_TO 대상 아님.

### 폐기 노드 라벨
`SQL_STATEMENT`, `CONFIG_SECTION`, `ARTIFACT_CHUNK`, `CHUNK`, `DOCUMENT`,
`DOC_SECTION`, `DOC_CHUNK`, `UNRESOLVED_REF`, `CANDIDATE_LINK` — 생산자·소비자 0.

## Relationships (외부 계약 전체)

### HAS_CHUNK
`PROJECT_FILE → EXTERNAL_CHUNK`. 속성 없음.

### REFER_TO (코드→청크 방향만)
`METHOD/FUNCTION → EXTERNAL_CHUNK`.
속성: `matched_key`, `evidence_file`, `evidence_line`, `via_symbol`.
생성 조건: routine code_text의 주석 밖 리터럴(또는 1단계 불변 상수값)이 어떤 청크
key와 whole-token·case-sensitive 완전 일치하고, **그 key를 노출하는 청크가 프로젝트
전체에서 정확히 1개**일 때. 의미 = occurrence 사실. 청크→코드 방향 금지.

주의: Catalog의 `METHOD→REFER_TO→TABLE`(DB 계보)은 별개 계약으로 유지 — target
라벨이 다르므로 충돌하지 않는다. Frontend 숨김 규칙은 외부 청크 endpoint일 때만 적용.

### 폐기 관계
`CALLS_SQL`, `USES_CONFIG`, `MENTIONS`, `HAS_PART`, `HAS_SECTION`,
외부 SQL 유래 `READS/WRITES`, chunk→Module 역방향 `REFER_TO` — 생산자·소비자 0.

## Writers

- `graph/nodes/external.py`: 허용 라벨 = {PROJECT_FILE, EXTERNAL_CHUNK}. 그 외 즉시 예외.
- `graph/edges/external_links.py`: 허용 타입 = {ReferTo, HasChunk}. endpoint 존재 검증 유지.
- graph_owner scoped lifecycle 밖의 삭제 금지. 전체 wipe 금지.
