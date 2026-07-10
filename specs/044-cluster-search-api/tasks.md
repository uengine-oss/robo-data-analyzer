# Tasks: 요구사항→레거시 코드 뭉치 검색 API

**Input**: [plan.md](plan.md) | **Spec**: [spec.md](spec.md)

## Phase 1 — 임베딩 기반 (US3)

- [ ] T001 `shared/config/env_catalog.py` — `ROBO_EMBED_PROVIDER/MODEL/API_BASE/CONCURRENCY/
      CHAR_BUDGET` + `ROBO_SEARCH_LLM_CONFIG/API_KEY` (FR-007)
- [ ] T002 `search/embed_text.py` — `compose_embed_text` (놀이터 `_compose_embed_text`
      searchfield 모드와 글자 단위 동일, 단일 진실) (FR-003)
- [ ] T003 `shared/llm/embedder.py` 재작성 — 사내 서버 HTTP·문자예산 배치·동시 16·
      분할/백오프·순서 보존·`fingerprint()`·local 폴백(경고) (FR-005·006, A)
- [ ] T004 `embed_step.py` — compose_embed_text 사용·`:EMBEDDED`·`:EMBED_META` 지문·
      VECTOR INDEX IF NOT EXISTS·커버리지/절단 로그·Table/Column 임베더 교체 (FR-003·004, B)
- [ ] T005 `analysis_pipelines.py` — `(build_glossary_step, embed_step)` 2층 병렬(양 전략)·
      `.env` `ROBO_PIPELINE_EMBED_ENABLED=true` (FR-008)
- [ ] T006 `cli_scripts/reembed_graph.py` — 재분석 없이 임베딩·마커·인덱스 재구성 (FR-011)
- [ ] T007 ★판정: 재임베딩 CLI 로 양 코퍼스(test·neo4j) 임베딩 — 손실 0·차원 1024·
      EMBEDDED 수=대상 수·소요 기록 (SC-003)

## Phase 2 — 검색 코어 이식 (US2) ★관문

- [ ] T008 `search/` 패키지 이식 — recall(dense=`db.index.vector.queryNodes`+1홉 동일 Cypher,
      bm25/식별자=기동 캐시)·rerank·fusion·expand·judge·rewrite·glossary·retriever·config
      (검색 LLM=`ROBO_SEARCH_LLM_CONFIG`) (FR-001·002·012, C)
- [ ] T009 인덱스 정합 probe — 인덱스 vs 전량 코사인 상위 100 대조, 골드 정답 누락 0 (SC-002)
- [ ] T010 브리지 모드(`ROBO_SEARCH_IMPL=analyzer`) — 놀이터 eval 이 analyzer search/ 를 쓰게
- [ ] T011 ★★관문: 게이트 재현 SKT 7/7 · 쇼핑몰 11/11 (SC-001) — 실패 시 Phase 3 진행 금지

## Phase 3 — 노출 (US1·US4)

- [ ] T012 `api/search_router.py` — POST /robo/search/cluster (400/503·coverage meta) (FR-001)
- [ ] T013 `api/mcp_app.py` — FastMCP streamable-http mount `/robo/mcp` + `mcp` SDK 의존성 +
      기동 워밍(지문 검증·CE 로드·코퍼스 캐시, 준비 전 503) (FR-002·006)
- [ ] T014 gateway `application.yml` — robo-analyzer Path 에 `/robo/search/**,/robo/mcp/**`
      (교차서비스, FR-010)
- [ ] T015 놀이터 stdio MCP 폐기 — mcp/server.py 제거, README 를 URL 등록법으로 (SC-005)
- [ ] T016 아키텍트 `.mcp.json` 템플릿 grep·URL 방식 교체 (교차서비스, F)
- [ ] T017 REST·MCP 스모크 — 직접 호출 + `claude mcp add --transport http` 등록 호출 (SC-004)

## Phase 4 — 프론트 (FR-009 + UI)

- [ ] T018 `nodeLabels.ts` — MARKER_LABELS {EXTERNAL, EMBEDDED} (교차서비스 1곳)
- [ ] T019 검색창 — code-analysis 화면 질의 입력→뭉치 리스트→노드 상세 연결
- [ ] T020 Playwright 실물 — EMBEDDED 역할 비표시 + 검색창 end-to-end 스크린샷 (SC-006·004)

## Phase 5 — 마무리

- [ ] T021 모델 불일치 기동 거부 실측(SC-007) · text2sql 인덱스 6종 무변화 재확인(SC-006)
- [ ] T022 병렬 이득 실측(전/후) — 무이득 시 순차 원복 (FR-008)
- [ ] T023 measure_log 기록·메모리 갱신·커밋(analyzer/frontend/gateway)·서브모듈 bump
