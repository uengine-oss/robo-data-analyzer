# HANDOFF 2026-07-11 (2) — spec 044 프론트 계약 확정 · SDD 순서로 재정렬 · 착수점

> 이 채팅은 삭제된다. 이 문서 + `frontend-contract-reply.md` + `spec.md`/`plan.md` 로 이어간다.
> ★새 세션 첫 행동: 메모리 원문 정독(principles·speckit·no-happy-path·explain-before-act·
> trace-based-debugging·search-text-plumbing·req-cluster-retrieval·embedding-spaces) →
> `specs/044-cluster-search-api/{spec,plan,tasks}.md` → 이 문서 → §5 "다음 할 일(SDD 순서)".

## 0. 한 줄 현황 — 코드 0줄. 계약 확정 단계.

spec 044 = 놀이터 검색(52/53)을 정식 배선(임베딩 저장 + 검색 API + MCP)으로 옮기기.
**Phase 0(인덱스 배선 동등성) 실측 완료** — 나머지는 SDD(spec 개정 → plan → tasks → 구현)로.
직전 세션에서 구현 코드로 뛰어들다 사용자 제지 = **"계약 먼저, 코드 0줄" 원칙 복귀**. 코드 안 씀.

## 1. Phase 0 완료 — 벡터 인덱스 배선 동등성 (measure_log Session 20 부록3)

**증명됨**: Neo4j 벡터 인덱스 근사탐색(B) = `.npz` 전량 코사인(A). 정답 53개(쇼핑몰 30+SKT 23)
전부 A/B 순위 동일, B 놓침 0. 도구 `probes/vec_index_setup.py`·`index_equiv.py`(원복까지 완료,
양 DB PLAY_EMBEDDED 0). → **본체 이식 시 dense 를 인덱스로 바꿔도 뒷단 로직 그대로 유효.**
한계: 124/38 소규모라 HNSW 근사오차 미노출 — 합성 대규모 recall 은 SC-002 별도 실측.
부수: Neo4j 2026 은 `db.index.vector.queryNodes` deprecated(→`SEARCH`) — 본체는 신 API.

## 2. ★프론트 계약 (robo-data-frontend 핸드오프 — 이번 세션 수신, 확정)

프론트가 그래프 탭 Overview 에 "의미 기반 코드 검색"을 붙인다. 확정 계약:
- **경로**: `POST /robo/search/semantic` (사용자 확정). 게이트웨이 `/robo/search/**`→analyzer(5502),
  catalog 포괄(`/robo/**`→5503)보다 **앞**. (FR-010, api-gateway 담당 합의 필요.)
- **세션 헤더**: 매 요청 `X-Neo4j-*` → 그 DB 를 검색(analyze 규약). `set_override` 재사용.
- **스트림**: NDJSON(한 줄=한 JSON). `{"type":"progress",...}` × N → `{"type":"result","clusters":[...]}` →
  `{"type":"stream_end"}`. 에러=`{"type":"error","message":...}`. 결과0=`clusters:[]`(에러아님).
- **취소**: AbortController(연결 끊기면 작업 중단). 별도 control 없음.
- **동시성**: 검색은 동시 여러 건 허용(읽기전용). 프론트가 새 검색 시 이전 것 abort.
- **result.nodes[].id**: `node.id` 속성(catalog `/robo/graph` 의 id 와 동일 체계). `element_id` 병기 제공.
- **score**: 0~1(검색 코어 relevance 그대로). 노드별 relevance 병기.
- 6항목 확정 답 = `frontend-contract-reply.md`. **아직 스트림/임베딩 phase 두 절 미반영 — §4 참조.**

## 3. ★★핵심 아키텍처 긴장 (구현 전 반드시 해소 — plan 에 박을 것)

**per-request DB(프론트) ↔ 전역 캐시 임베더(놀이터)**:
- analyze 는 `X-Neo4j-*` 헤더로 요청마다 다른 DB(contextvar `set_override`). 검색도 그 DB 대상.
- 놀이터 `ClusterRetriever` = 기동 시 **한 DB 의 코퍼스·임베딩(.npz)을 전역 캐시**. 요청별 DB 전환 불가.
- **해법 방향(Phase 0 이 뒷받침)**: 검색 코어를 "전역 .npz 캐시"가 아니라 "요청 DB 의 벡터 인덱스
  조회"로 이식. 그러면 요청별 DB 전환이 공짜(각 DB 에 EMBEDDED 인덱스가 있으므로).
  → `layer2_recall.Recaller` 의 dense 채널이 `build_or_load()`(.npz) 대신 요청 DB 인덱스를 조회.
  코퍼스 메타(name·summary·search_text·stereotype·rels)도 요청 DB 에서 로드. **전역 싱글턴 금지.**
- 질의 임베딩 = 노드와 동일 모델(BGE-M3) 강제(FR-006), 불일치 거부.

## 4. 새 임베딩 phase 도 프론트에 알릴 것 (사용자 강조)

- 임베딩은 **후처리** — `embed_step` 이 파이프라인 맨 끝(`build_glossary_step` 뒤, 양 전략).
  모듈요약·테이블설명이 끝나야 재료가 생기므로 위치 정당. 이미 그 자리(analysis_pipelines.py).
- analyze **스트림에 새 단계로 뜬다** → 프론트가 phase 목록에 추가해야 함(analyze 진행 UI).
  embed_step 이 `cfg["stream"].emit({"type":"processing_event",...})` 로 진척 emit(예: `120/124`).
- 프론트 대사(§ 아래 두 번째)에 이 phase 안내 포함.

## 5. 다음 할 일 (SDD 순서 — 코드는 tasks 후에)

1. **spec.md 개정**: 프론트 계약 병합 — 스트림(NDJSON progress/result/stream_end)·per-request DB·
   취소(Abort)·동시성·경로 `/robo/search/semantic`·새 임베딩 phase 스트림. `speckit-specify` 또는 직접.
2. **plan.md 개정**: §3 아키텍처 긴장 해소 배선 확정(Recaller per-request DB 인덱스 조회 구조),
   embed_step 재작성 설계(아래 §6), 교차서비스(프론트 1곳·게이트웨이 1곳). `speckit-plan`.
3. **tasks.md**: 의존순 분해. `speckit-tasks`.
4. **구현**: §6 순서. 관문 SC-001(DB 벡터 게이트 18/18) 전엔 API 공개 금지.
5. **프론트 대사 최종본** 회신 + `frontend-contract-reply.md` 에 스트림/임베딩 절 추가.

## 6. embed_step 재작성 설계 (조사 완료 — plan 에 옮길 것)

- **현 상태**: 코드노드를 `summary`만 임베딩. `:EMBEDDED` 없음. 인덱스 없음. 스트림 없음.
- **재작성**(FR-003/004/005):
  a. embed_step 은 파이프라인 끝 = 그래프 완성 시점 → 검색측과 **똑같은 `search/embed_material.py`
     의 `FETCH_MATERIAL` + `compose_embed_text`** 로 코드노드 텍스트 생성(글자단위 동일 구조 보장·DRY).
     `neo.run_read_query(FETCH_MATERIAL)` → `compose_embed_text(row)` → `embedder.encode_texts`.
  b. `UNWIND ... SET n.embedding=item.vector SET n:EMBEDDED` (id 무라벨 매치 유지).
  c. **벡터 인덱스**: `CREATE VECTOR INDEX embedded_code_vec IF NOT EXISTS FOR (n:EMBEDDED)
     ON (n.embedding) OPTIONS {indexConfig:{`vector.dimensions`:1024,`vector.similarity_function`:'cosine'}}`.
     ★주의(사용자 강조): **IF NOT EXISTS 필수**(재생성 오류 방지). 인덱스=스키마라 wipe_graph
     (DETACH DELETE=데이터만) 견딤. run 은 `db.awaitIndexes` 로 ONLINE 대기 후 종료(FR-004).
     차원 불일치 옛 인덱스 있으면 CREATE IF NOT EXISTS 가 no-op → 수동 DROP 안내(눈대중 금지).
  d. 테이블·컬럼 임베딩은 **현행 유지**(description 기반, :EMBEDDED 안 붙임 — 검색대상 아님).
  e. 스트림 emit(processing_event) 로 진척.
- **모델 일치 불변식(FR-006)**: 노드 임베딩 시 `embedder.fingerprint()` 를 그래프 메타(예:
  `:EMBEDDED` 인덱스 관련 노드나 별도 메타노드)에 기록 → 검색 시 질의 임베더와 대조, 불일치 거부.
- **재임베딩 CLI(FR-011)**: `cli_scripts/reembed_graph.py` — 재분석 없이 embed_step 로직만 재실행.

## 7. 이식 대상·소비자 (전수 census 완료 = 직전 세션 데드스택 폐기 후)

- 놀이터 활성 = engine/{cluster_retriever·cluster_judge·query_rewrite·layer2_recall·layer3_rerank(CE)
  ·layer5_fusion(타입힌트)·glossary·_common·config·text2cypher} + mcp/server.py(stdio, 폐기 대상).
  오늘 고친 3결함(심판 1-based·합집합 게이트·범주어 병기) 포함 — 이식 시 함께.
- 본체 이식처 = analyzer `search/`(사용자 확정). 이미 `search/embed_material.py` 커밋됨(`78a8c02`).
- MCP over HTTP = analyzer 가 FastMCP streamable-http 로 같은 코어 노출, stdio 서버 폐기(SC-005).

## 8. 이번 세션 커밋 상태

- `78a8c02` spec 044 Phase1 부분(search/embed_material·embedder·env_catalog·embed_step 한줄).
- `081476a` 프롬프트 재설계. 둘 다 origin/main 푸시됨.
- 놀이터 수정(3결함·데드스택 폐기·probes)은 gitignore = 디스크만. 이식 시 본체로.
- 미커밋 신규: `specs/044/frontend-contract-reply.md`·이 핸드오프 (git add 안 함 — specs 는 추적됨,
  다음 세션이 spec 개정과 함께 커밋).

## 9. 원칙 리마인드 (이번 세션 실제로 걸린 것)

- **SDD**: 계약(spec) 먼저, 코드 0줄. 프론트 계약 들어왔으면 spec 에 병합 후 구현. ← 직전 위반·복귀.
- **[[trace-based-debugging]]**: 배선 이해 못한 채 구현 금지. per-request DB × 캐시 임베더 긴장(§3)
  해소가 그 예 — plan 에서 배선 확정 후 코드.
- **벡터 인덱스 재생성 주의**(사용자): IF NOT EXISTS·wipe 생존·차원충돌 수동처리.
- 해피패스 금지(질의 길이 분기 금지 — 두 단어 골드 실재)·explain-before-act·answer-first.
