# Tasks: 요구사항→레거시 코드 뭉치 검색 API

**Input**: [plan.md](plan.md) | **Spec**: [spec.md](spec.md)

> 상태 범례: `[x]` 완료·검증 / `[ ]` 미착수. 완료분은 `78a8c02` 커밋(현실 반영 — plan "이미 커밋된 것").

## Phase 1 — 임베딩 기반 (US3)

- [x] T001 `shared/config/env_catalog.py` — `EmbedSettings`(ROBO_EMBED_*) + `SearchSettings`
      (ROBO_SEARCH_LLM_* · DENSE_IMPL index|scan · FETCH_K) (FR-007) ✅`78a8c02`
- [x] T002 `search/embed_material.py` — `FETCH_MATERIAL` + `compose_embed_text` (놀이터 searchfield
      모드와 동형, 단일 진실) (FR-003) ✅`78a8c02`
- [x] T003 `shared/llm/embedder.py` — 사내 서버 HTTP·문자예산 배치·동시 세마포어·분할/백오프·
      순서 보존·`fingerprint()`·local 폴백 (FR-005·006) ✅`78a8c02`
- [ ] T003b (잔여·비차단) 공유 세마포어(FR-008)·dim 지문 — 실측 후 판단(T025에서 결론, 게이트 선행 아님)
- [x] T004 `embed_step.py` **재작성** — 코드노드=`run_read_query(FETCH_MATERIAL)`→`compose_embed_text`
      →`encode_texts`→`SET n.embedding, SET n:EMBEDDED` · `MERGE (:EMBED_META) SET fingerprint` ·
      `CREATE VECTOR INDEX embedded_code_vec IF NOT EXISTS`(1024 cosine)·`db.awaitIndexes` ·
      차원충돌 수동 DROP 안내 로그 · `processing_progress` 재사용(processing_event) ·
      Table/Column 현행 유지 · 커버리지/절단 로그 (FR-003·004·016, plan §B)
- [ ] T005 `analysis_pipelines.py` — `(build_glossary_step, embed_step)` 2층 병렬(양 전략).
      `ROBO_PIPELINE_EMBED_ENABLED` 는 기본 true(현행) (FR-008)
- [x] T006 `cli_scripts/reembed_graph.py` — 재분석 없이 저장 결과로 임베딩·마커·인덱스 재구성 (FR-011)
      [실행검증: 양 DB에서 embed_step 재사용, 파이프라인 토글 무시(명시적 임베딩)]
- [x] T007 ★판정: 재임베딩 CLI 로 양 코퍼스(test·neo4j) 임베딩 — 손실 0·차원 1024·
      EMBEDDED 수=대상 수·소요 기록 (SC-003)
      [실측 2026-07-11: neo4j 124/124 손실0 1.7s · test 38/38 손실0 0.9s · dim=1024 단일 · 인덱스 ONLINE · EMBED_META=sglang:BAAI/bge-m3]

## Phase 2 — 검색 코어 이식 (US2) ★관문

- [x] T008 `search/corpus.py` — **DB별 키 캐시**(전역 .npz 대체): 요청 DB 코퍼스 메타·bm25·
      structural·fan_in lazy 로드, 키=(uri,database), `:EMBED_META` 지문+EMBEDDED 수 서명 무효화,
      모델 지문 대조(불일치·임베딩없음 → SearchUnavailable). 전역 싱글턴 없음 (FR-014, plan §C)
      [실행검증: shopmall(124)·SKT(38) 각기 로드, 헤더로 요청별 전환]
- [~] T009 `search/` 코어 이식 — **base 결정론 코어 완료·실증**(recall dense=`queryNodes`+bm25+
      structural·RRF / rerank CE / fusion 타입힌트 / expand 1홉 / retriever `on_progress`·후보벡터 조회 /
      config·neo). `dense_impl=scan` 폴백 코드 포함. **잔여: judge·rewrite·glossary·llm(LLM 토글,
      기본 off) 미이식 → 게이트 52/53 재현(T012)은 이후.** rel_types 정합·CE GPU/원격은 별개.
      (FR-001·002·006·012·014, plan §C)
- [x] T010 인덱스 정합 — **현 규모 동등성으로 종결**(Phase 0: 인덱스=전량코사인 53/53 일치 + index
      모드 양 DB 실질의 정상). **대규모 합성측정은 범위 제외**(사용자 결정 2026-07-11). scan 원복
      스위치는 코드 보존 (SC-002)
- [ ] T011 브리지 모드(`ROBO_SEARCH_IMPL=analyzer`) — 놀이터 eval 이 analyzer `search/` 를 쓰게
- [x] T012 ★★관문: 게이트 측정 — **51/53** (shopmall 30/30·SKT 21/23), 채택 config=rewrite
      novel_abbrev·judge **off**·ce_top15 (GPU Qwen). 놀이터 52/53과 −1(유일 미스 = SKT 코퍼스
      결제망 약어 케이스 1건). ★실측 결론: Qwen 심판은 과잉삭제(gpt 승자 무효)라 off /
      ce_top15가 30보다 우수(CE 비단조). 장부·골드=`search/eval/`(고객데이터라 gitignore) (SC-001)
      [잔여 TODO: 결제망 약어 케이스 부검 · gpt심판 복구/Qwen완화 · CE GPU서빙]

## Phase 3 — 노출 (US1·US4)

- [x] T013 `api/search_router.py` — **POST /robo/search/semantic** NDJSON 스트림: `set_override`
      (요청 DB)→`NDJSONStream`→`create_task`→`StreamingResponse`. progress(recall→rerank→expand)→
      result(clusters·meta·`id`+`element_id`)→stream_end / error. 400(빈 질의). CPU 코어 to_thread +
      progress 는 `call_soon_threadsafe`(스레드 안전) (FR-001·013·014·015)
      [실행검증 curl: progress→result→stream_end / 빈질의 400 / 잘못된 creds→error 이벤트 / 헤더로 SKT·shopmall 전환]
      [잔여: 취소(disconnect) 기전 실측 미완 · 503 매핑(현재 error 이벤트로 표면화)]
- [x] T014 `shared/log/ndjson_stream.py` — `_EVENT_LOGGER` 에 `progress`/`result`(검색) 추가(로그 미러)
- [x] T015 `api/mcp_app.py` — FastMCP streamable-http mount `/robo/mcp`(stateless) + main.py
      session_manager lifespan. cluster_retrieve(query, database?) = REST 와 같은 run_search 코어.
      [실증: curl JSON-RPC tools/call → 정답 뭉치(2026-07-11 재확인). 기동 워밍(CE 비차단 로드)은
      미구현 — 첫 질의가 모델 로드 흡수(수십 초), 후속 개선] (FR-002)
- [x] T016 gateway `application.yml` — `/api/gateway/robo/search/**` → analyzer(5502) 추가·커밋·**재시작
      적용 완료**. 실경로 검증: `9000/api/gateway/robo/search/semantic` → NDJSON 스트림 정상.
      프론트 vite 프록시 STREAM_PATTERNS `/search` 추가·커밋 완료. (FR-010) [gateway ca28a3b·frontend ec14416]
- [x] T017 놀이터 stdio MCP 폐기 — `server.py`→`.bak` 보존, README 를 HTTP URL 등록법으로 (SC-005)
- [x] T018 아키텍트 `.mcp.json` 주입 — setup-project(router.py)가 대상 프로젝트 .mcp.json 에
      robo-cluster http URL 기입(architect 4b0ebaf), 단일 출처 cluster_mcp_url() 정합(5466571)
- [x] T019 REST·MCP 스모크 — curl NDJSON + MCP JSON-RPC tools/call 정답 뭉치 실증(2026-07-11)
- [ ] T019b CE 성능 다이얼 검증 — ce_top15·ONNX INT8·디스크캐시 적용 상태로 게이트(SC-001) 통과
      + ONNX vs torch 점수 대조(순위 불변) + 속도 실측 기록 (plan §C 다이얼)
- [x] T019c ★아키텍트 배선 전수조사 완료(2026-07-11) — ★확정 사실: ① 인텐트 스폰(skill_runner)은
      cwd=robo-architect 루트인데 그곳에 .mcp.json 이 없어 **robo-cluster 가 스킬에 안 보였음**
      (router.py 주입은 Code탭 대상 프로젝트 전용) ② robo-proposals 스킬 전체에 cluster/레거시 지시 0
      ③ 정방향 Proposal→DDD 는 레거시 무참조(통째 브리프는 역방향 047 전용) (SC-009 전제)
- [~] T019d 아키텍트 레거시참조 스킬화 — **구현 완료(architect 5466571)**: skill_runner 가 모든 스킬
      스폰에 `--mcp-config`(robo-cluster) 주입 + robo-proposal-intent SKILL.md 에 "분해 전
      cluster_retrieve 필요분 참조(통째 금지)·narration [레거시 참조]" 지시. **실증(E2E)은 미완** —
      아키텍트 8001 을 Claude 세션 밖에서 재기동해야 신코드 적용+스폰 차단 해제(T023b). plan·discover
      등 타 스테이지 확장은 intent 실증 후 (SC-009)

## Phase 4 — 프론트 (FR-009·016 + UI)

- [x] T020 프론트 EMBEDDED 마커 제외 — nodeLabels.ts MARKER_LABELS/isHiddenMarker +
      nodeClassify·navigator ROLE_LABELS (frontend c826f86). GraphTab 통계 제외 1줄은 spec 046
      미커밋 뭉치 동승(같은 파일). 실물 확인(SC-006)은 T023 에서 (FR-009)
- [ ] T021 analyze 진행 UI — embed_step `processing_event`(step=`step7.embed`) phase 추가 (교차서비스, FR-016)
- [ ] T022 검색창 — 그래프 탭 Overview 질의 입력→NDJSON 스트림 소비(progress 표시→result 뭉치 리스트)
      →노드 상세 연결 + 새 검색 시 이전 요청 AbortController abort (plan §F)
- [ ] T023 Playwright 실물 — EMBEDDED 역할 비표시 + 검색창 end-to-end + progress 스트리밍 스크린샷.
      콘솔·페이지 오류 0까지 전부 수정(사용자 지시). vite 프록시 STREAM_PATTERNS `/search` 추가 (SC-006·004·008)
- [ ] T023b ★E2E DDD 완주 — 쇼핑몰+DDL 업로드→분석→아키텍트 Proposal 자연어 요구사항→스킬 참조
      →DDD 생성 끝까지, Playwright 실물·오류 전수 포착 (SC-009)
      [★전제(2026-07-11 확정): 아키텍트 8001 을 **Claude 세션 밖**(사용자 터미널)에서 5466571 이후
      코드로 재기동 — 세션 안 기동은 하니스가 nested claude(--dangerously-skip-permissions) 차단.
      검증 신호 = 인텐트 SSE tool_use `mcp__robo-cluster__cluster_retrieve` + narration [레거시 참조]]

## TODO (범위 밖 — 후속)

- **재채점(CE) GPU 서빙** — 현재 로컬 CPU(무거운 bge-reranker-v2-m3-ko 568M라 새 질의당 15~60초).
  **CPU 유지 확정**(사용자 결정 2026-07-11 — 레퍼런스 mpower-rag도 동일 계열 CPU 사용 실측). 후속
  속도개선: ⓐ 사내 GPU 서버에 리랭커 배포(sglang `/v1/rerank` 존재하나 리랭커 모델 미탑재 → 서버
  담당자 요청) ⓑ ONNX+INT8 양자화(2~4배, 재측정) ⓒ 경량 다국어 모델(Qwen3-Reranker-0.6B). [[embedding-spaces]]

## Phase 5 — 마무리

- [ ] T024 SC 실측: 모델 불일치 지문 요청 거부(SC-007) · 두 DB 번갈아 검색=각기 다른 코퍼스
      (전역 싱글턴 없음)·abort 중단(SC-008) · text2sql 인덱스 6종 무변화 · **robo-architect ingestion
      이 신규 라벨(EMBEDDED·EMBED_META) 무시하고 정상 동작**(그래프 스키마 계약 무영향) (SC-006)
- [ ] T025 병렬 이득 실측(전/후) — 무이득 시 순차 원복 + T003b(공유 세마포어) 결론 (FR-008)
- [ ] T026 measure_log 기록·메모리 갱신·커밋(analyzer/frontend/gateway)·서브모듈 bump
