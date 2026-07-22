# 078 Frontend Contract

## Backend이 제공하는 것 (graph-contract 요약)

- 노드: `PROJECT_FILE`, `EXTERNAL_CHUNK` (필드: `name`, `keys`, `semantic_kind`,
  `text`, `file_path`, `start_line`, `end_line`, `provenance`, `char_start`, `char_end`)
- 관계: `HAS_CHUNK`(파일→청크), `REFER_TO`(METHOD/FUNCTION→청크, `matched_key` 포함)

## Frontend 의무

1. `graphVisibility.ts`(단일 visibility owner):
   - 기본 숨김 라벨: `PROJECT_FILE`, `EXTERNAL_CHUNK` (구 라벨 7종 항목 제거)
   - `REFER_TO`는 **endpoint가 EXTERNAL_CHUNK일 때만** 기본 숨김 — Catalog의
     TABLE-target REFER_TO 표시를 깨지 않는다
   - showHidden 시 외부 체인 표시
2. 카드: 외부 청크 카드는 `text`(원문)·`keys`·`file_path:start_line-end_line`·
   `provenance`를 표시한다. `code_text`/`sql_text` 등 구 필드 참조 제거.
   `SqlStatementCard`의 SQL_STATEMENT 라벨 라우팅은 폐기 라벨이므로 정리.
3. `tests/external-graph-contract.mjs`를 새 계약으로 갱신.

## 검증

- build + contract 테스트 + 1920×1080 실화면(기본 숨김/showHidden/카드 원문)·콘솔
  오류 0·network 확인.
- 사용자 WIP 6건(package.json, record-scenes.mjs, scripts/, specs/057-*,
  panelGraphProjection.ts, tsconfig.panel-projection.json) 비접촉.
