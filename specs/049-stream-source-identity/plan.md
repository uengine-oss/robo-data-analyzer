# Implementation Plan: 라이브 노드 원본 파일 식별

`AST Module.file_path/end_line → graph_stream node_event.props → frontend nodeMap → 식별 패널`

- 공용 `graph_stream._DISPLAY_PROP_KEYS`에 기존 필드를 추가한다.
- FILE뿐 아니라 동일 계약을 쓰는 모든 코드 노드에 일관되게 적용한다.
- fake stream 회귀 테스트로 wire 값을 검증한다.
