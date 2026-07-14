# Feature Specification: 라이브 노드 원본 파일 식별

**Created**: 2026-07-14
**Status**: Implementing

## Requirements

- FILE/코드 노드의 라이브 `node_event.props`는 기존 `file_path`, `start_line`, `end_line`, `owner_id`를 보존한다.
- 소비자는 id나 표시명을 분해해 원본 파일을 추측하지 않는다.
- Neo4j 재조회 전에도 라이브 노드와 최종 노드의 식별 정보가 동일해야 한다.
- embedding, search_text, domain_terms 등 내부 분석 재료는 표시 wire에 싣지 않는다.
