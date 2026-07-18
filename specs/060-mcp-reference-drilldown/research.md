# Research

## 실측 기준선

- live streamable HTTP: 도구 `cluster_retrieve` 1개.
- smoke 질의: 1 cluster, 19 nodes, 12 rules, table evidence 2.
- 응답 104,602 bytes, code 보유 노드 18, code_text 77,413 bytes, TABLE columns 6.
- `detail`의 활성 코드 호출자는 MCP 함수 테스트 외 0. 과거 spec/docs는 역사 기록으로 유지한다.
- 코드 노드에는 `file_path/start_line/end_line/code_text`가 있으며 TABLE/RULE/EXAMPLE은 source 좌표가 없다.

## 결정

- 첫 응답에는 코드 0, 컬럼 0. 사용자가 과거에 남긴 “앵커만 코드인가?” 선택지는 목록+드릴다운 방향 확정으로 해소됐다.
- `node_detail`은 arbitrary id를 허용하되 source 가 없는 노드는 `available=false`를 명시한다.
- 상세 관계는 허용목록으로 일부만 숨기지 않고 실제 모든 관계를 반환하되 내부 식별자/embedding/search_text/owner는 제거한다.
- summary 중복은 후처리로 문장을 자르는 대신 생산 계약을 좁히고 실제 재분석으로 검증한다. 규칙 의미를 결정론 정규식으로 삭제하면 의미 손실이 생기므로 금지한다.

## test DB

현재 `test`는 1,725 shopmall 노드이며 과거 계약의 SKT 38노드가 아니다. Git에는 loader 스크립트만 남고 원본 데이터는 추적되지 않는다. 복구는 출처를 추가로 찾거나 골드 fixture를 재현 가능한 현재 프로젝트로 교체하는 별도 task로 수행하며, 오염 DB를 정답처럼 덮어쓰지 않는다.
