# Tasks: 클러스터 규칙/예시/테이블 전달 + AFFECTS_TABLE 적재 수정

**Input**: `spec.md`. Tests 포함. 커밋/푸시는 사용자 요청 시에만. 기존 WIP 보존(무관 변경 되돌리기 금지).

## US1 — 검색 코어 규칙 전달 (구현완료)

- [x] T001 `search/retriever.py` `_rules_for(cid)` 추가 — HAS_RULE→HAS_EXAMPLE→AFFECTS_TABLE, `e.id STARTS WITH
      '<cid>::'` 스코프, flat row → Python 그룹핑. `_node_row` 반환에 `"rules"` 추가, `_table_row` 에 `"rules": []`.
      증거: git diff `search/retriever.py`.
- [x] T002 `search/test_rules_traversal.py` 신규 — 그룹핑/prefix스코프/빈규칙/null테이블/무규칙 5개.
      증거: `.venv\Scripts\python.exe -m unittest search.test_rules_traversal` → 5 OK.
- [x] T003 회귀 — `python -m unittest discover -s search` 6 OK(test_global_anchor 포함). additive 필드라 회귀 0.
- [x] T004 라이브 검증 — 실제 그래프에서 정확한 Cypher 실행: stl_make_settle_key rule 1·example 2, 교차오염 0.
      증거: `goal-verify/graph_probe.py` STEP7 LIVE 블록.

## US2 — AFFECTS_TABLE 적재 결함 수정

- [x] T005 근본원인 라이브 확정 — 스트림 269 AFFECTS_TABLE 이벤트 vs 그래프 0. table_name='db:order_chk_hst'
      (TABLE id), 현재 매칭 0 / 콜론스트립 매칭 1. 증거: 세션 로그.
- [x] T006 `shared/neo4j/edges/affects_table.py` upsert 의 TABLE 매칭에 콜론 접두사 스트립 추가
      (`last(split(last(split(item.table_name, ':')), '.'))`). docstring 갱신.
- [x] T007 재분석 후 검증 — 그래프 AFFECTS_TABLE 엣지 **275**(수정 전 0), EXAMPLE 716 중 235 연결(32.8%,
      나머지는 순수로직 함수라 정상 무테이블). 증거: `goal-verify/diff_runner.py` 재실행 결과 + diff-report.json.
- [x] T008 REST `/robo/search/semantic`(=MCP cluster_retrieve 공유코어) "주문 확인 이력 기록" → 21노드 중
      18 rules·16 tables (예: promotion_apply_coupon → member_grade_pol READ). 증거: 세션 로그 + verify_step7_search.py.
- [x] T009 표면오류 수정(재점검에서 발견) — `_rules_for` ORDER BY 가 example id 꼬리(`::10`)를 문자열
      정렬해 10개 초과 시 순서 어긋남 → `size(e.id)` 선행 정렬로 숫자 순서 보장. 테스트에 정렬 계약 어서션
      추가(12 green). `goal-verify/graph_probe.py` 는 Cypher 사본 대신 실코드 `_rules_for` 호출로 교체(DRY).

## Dependencies

- US1 은 US2 와 독립(트래버스 자체는 엣지 유무와 무관, 엣지 있으면 tables 채워짐).
- T007/T008 은 affects_table 수정 후 재분석 1회 필요(사용자 재분석 승인함).
