# Implementation Plan: 요구사항→레거시 코드 뭉치 검색 API

**Branch**: `main` | **Date**: 2026-07-11 (프론트 계약 확정본으로 개정) | **Spec**: [spec.md](spec.md)

## Summary

놀이터(`module_test/임베딩서칭/`)에 갇힌 검색(게이트 52/53)을 정식 배선으로 옮긴다:
① 분석 끝에 코드노드 임베딩을 사내 GPU 서버(BGE-M3 1024d)로 생성해 Neo4j 에 저장 + `EMBEDDED`
마커 + 벡터 인덱스(`embedded_code_vec`) ② 놀이터 검색 코어를 analyzer `search/` 로 이식하되
**전역 싱글턴(.npz·전역 드라이버·로컬 임베더)을 요청별 DB 조회로 재배선**(핸드오프 §3 긴장 해소)
③ `POST /robo/search/semantic` 을 **NDJSON 스트림**(progress→result→stream_end)으로, 같은 코어를
MCP over HTTP 로 노출 ④ 게이트웨이 1곳·프론트(마커 유틸 1곳 + analyze 진행 UI 임베딩 phase)·
UI 검색창. **관문 = DB 벡터 인덱스로 두 골드셋 재현(SC-001: 위험질의 18/18 AND full gold 52/53)
전엔 API 공개 금지.**

## Technical Context

**Language/Version**: Python 3.12 (analyzer) / Vue3+TS (frontend: 마커 유틸 1곳 + 검색창 + analyze
진행 UI phase) / Spring Gateway yml 1곳

**Primary Dependencies**: 기존 FastAPI·neo4j async driver·`shared.llm.embedder`(httpx 원격, **완료**)·
sentence-transformers(리랭커 CE 용, `dragonkue/bge-reranker-v2-m3-ko`)·`mcp` SDK(FastMCP
streamable-http — analyzer venv 에 신규 추가)·rank_bm25(bm25 채널)

**Storage**: Neo4j — 코드노드 `embedding`(1024d)+`:EMBEDDED`, VECTOR INDEX `embedded_code_vec`
(`(:EMBEDDED).embedding`, 1024d cosine, IF NOT EXISTS·wipe 생존), 지문 메타노드 `:EMBED_META
{fingerprint}`. Table/Column `embedding` 은 현행 유지(description 기반, :EMBEDDED 안 붙임 —
검색대상 아님). text2sql/catalog 기존 벡터 인덱스 6종 무변화(SC-006).

**Testing**: 놀이터 골드·게이트 재사용(측정기 무수정) + 브리지 모드(`ROBO_SEARCH_IMPL=analyzer`)로
analyzer `search/` 를 같은 골드로 재현 판정 + 인덱스 vs 전량 코사인 대조 probe + Playwright 실물

**Performance Goals**: 임베딩 1만 노드 ≤ 1분(실측 **동시 8·문자예산 700 = 38초**; 동시 16 은 GPU
OOM 으로 오히려 333초 — 16 아님) · 검색 1요청 ≤ 3초(질의 임베딩 ~60ms + 인덱스 ms + CE + 심판)

**Constraints**: 손실 0(헌법 IV) · 모델 일치 불변식(fingerprint) · `labels[0]` 불변 · **전역 싱글턴
금지(요청별 DB)** · 측정무결성(게이트 재현 전 API 공개 금지)

**Scale/Scope**: analyzer 신규 패키지 1(`search/`) + 수정 3파일(embed_step·analysis_pipelines·main) +
신규 2(search_router·mcp_app) + CLI 1 · frontend 3(마커 유틸·검색창·analyze phase) · gateway 1 ·
놀이터 mcp 폐기

## Constitution Check

| 원칙 | 판정 | 근거 |
|---|---|---|
| I. Determinism-First | ✅ | 인덱스 탐색·배치·라벨·전송규율은 결정론. LLM 은 검색 코어의 판단(번역·심판)뿐 |
| II. Zero-Branch Strategy | ✅ | embed_step 은 전략 무관(state·그래프 노드만 봄). `EMBEDDED` 는 라벨 열거 없이 "벡터 저장 시 부여", 인덱스 1개 |
| III. Single Source of Truth | ✅ | 신규 env 는 env_catalog 에만(완료). 임베딩 텍스트 조성 `compose_embed_text` 한 곳(embed_step·검색 공용) |
| IV. No Silent Failure | ✅ | 전송 실패=분할·백오프 후 raise(완료). 임베딩 없음=`error` 이벤트. 절단=로그. 지문 불일치=요청 거부 |
| V. KV-Cache-Friendly | ✅ | 생성 프롬프트 무변경(해시 불변) |
| VI. Schema Restraint | ✅ | LLM 출력 스키마 무변경 |
| VII. Submit-Tool Wrap | ✅ | 신규 LLM 경로 없음(검색 코어 기존 방식 이식) |
| VIII. Cohesion | ✅ | 검색 코어는 `search/` 응집. 임베딩 전송 규율은 embedder.py 한 곳. progress 는 processing_progress 헬퍼 재사용 |
| Cross-Service | ⚠️ **flag** | **frontend 2곳**(nodeLabels 마커 + analyze 진행 UI 임베딩 phase)·**api-gateway 1곳**(라우트)·아키텍트 `.mcp.json`(URL 교체). graph_stream 라벨 계약 주석 갱신 |
| Cost & Safety | ✅ | 임베딩 사내(과금 0)·검색 LLM 은 별도 env(`ROBO_SEARCH_LLM_CONFIG`=gpt54_mini, 질의당 ≤2콜) |

## 이미 커밋된 것 (`78a8c02`) — 현실 반영 (추측 금지·실측)

이식이 코드 0줄이 아니다. 다음은 **완료·검증됨**:

- **`shared/config/env_catalog.py`**: `EmbedSettings`(ROBO_EMBED_PROVIDER/MODEL/API_BASE/API_KEY/
  CONCURRENCY=8/CHAR_BUDGET=700/MAX_RETRIES=6/TIMEOUT) + `SearchSettings`(ROBO_SEARCH_LLM_CONFIG=
  gpt54_mini_openai/LLM_API_KEY/**DENSE_IMPL=index|scan**/FETCH_K=100) 완비 → **T001 완료**.
- **`shared/llm/embedder.py`**: 사내 서버 HTTP·문자예산 동적배치·동시 세마포어·413/424/429 반분할·
  단건 지수백오프·최종 raise·순서보존·`fingerprint()`(=`provider:model`)·local 폴백(경고) → **T003 완료**.
  잔여 미세갭 2건(T003b 로 처리): ⓐ 세마포어를 `encode_texts` 호출 내부에서 생성 →
  파이프라인 공유 세마포어(FR-008) 미충족. 단 embed_step 은 파이프라인 **끝**이라 다른 GPU 단계와
  겹치지 않고, 병렬 짝인 build_glossary 는 **다른 서버(gpt)** 라 실경합 낮음 → 공유 세마포어는
  실측 후 판단(무이득 시 현행 유지). ⓑ fingerprint 에 dim 미포함 → provider·model 만으로 충분
  (같은 model=같은 dim). 불변식은 provider:model 대조로 성립.
- **`search/embed_material.py`**: `FETCH_MATERIAL`(summary 보유·컨테이너 제외·테이블관계 수집) +
  `compose_embed_text`(종류·역할·이름·쓰는/읽는테이블·요약·검색) → **T002 완료**. ★놀이터
  `_common._compose_embed_text` 의 `searchfield` 모드와 **동형**(이 파일이 이식 후 단일 진실).
  plan 옛 명칭 `search/embed_text.py` 는 오기 → 실제 `search/embed_material.py`.
- **`embed_step.py`**: `78a8c02` 는 import 한 줄만 — **본 재작성(§B)은 미완**.

## Project Structure

```text
robo-data-analyzer/
├── shared/llm/embedder.py            # ✅완료 (원격 HTTP·전송규율·fingerprint)
├── shared/config/env_catalog.py      # ✅완료 (Embed/Search Settings)
├── search/
│   ├── embed_material.py             # ✅완료 (FETCH_MATERIAL + compose_embed_text — 단일 진실)
│   ├── corpus.py                     # ★신설 — DB별 코퍼스·bm25·fan_in 캐시(전역 .npz 대체, 지문 무효화)
│   ├── recall.py                     # ★이식 — dense=벡터 인덱스 질의(요청 DB) / scan 폴백 · bm25 · structural
│   ├── rerank.py / fusion.py / expand.py / judge.py / rewrite.py / glossary.py
│   ├── retriever.py                  # ★이식 — ClusterRetriever (stage 오케스트레이션 + on_progress 콜백)
│   └── config.py                     # 검색 토글·임계 (놀이터 config 이식)
├── analysis_flow/step7_post_processing/embed_step.py
│                                     # ★재작성(§B) — 코드노드=compose_embed_text·:EMBEDDED·인덱스·
│                                     #   :EMBED_META·processing_event · Table/Column 은 현행 유지
├── analysis_pipelines.py             # (build_glossary ∥ embed) 2층 병렬 튜플 (양 전략)
├── cli_scripts/reembed_graph.py      # ★신설 — 재분석 없이 임베딩·마커·인덱스 재구성 (FR-011)
├── api/search_router.py              # ★신설 — POST /robo/search/semantic (NDJSON 스트림·per-request DB)
├── api/mcp_app.py                    # ★신설 — FastMCP streamable-http 를 /robo/mcp 에 mount
└── main.py                           # router 등록 + 기동 워밍(CE 로드·비차단)

robo-data-frontend/
├── src/shared/utils/nodeLabels.ts    # MARKER_LABELS {EXTERNAL, EMBEDDED} (교차서비스 1곳)
├── (analyze 진행 UI)                 # embed_step processing_event phase 추가 (교차서비스 1곳)
└── (그래프 탭 Overview) 검색창       # 질의 입력 → NDJSON 스트림 소비 → 뭉치 결과 → 노드 상세 연결

api-gateway/ application.yml          # robo-analyzer Path 에 /robo/search/**,/robo/mcp/** (catalog 포괄보다 앞)

module_test/임베딩서칭/mcp/           # stdio 서버 폐기(README 를 URL 등록법으로)
```

**Structure Decision**: 검색 코어는 `search/` 신설 패키지(응집). 임베딩 텍스트 조성은
`search/embed_material.py` 단일 함수로 두고 embed_step·검색 양쪽이 import(글자 단위 동일 보장 —
사본 금지). 전역 상태는 `search/corpus.py` 의 **DB별 키 캐시**로만 존재(전역 싱글턴 아님).

## 설계 상세 (구현 계약)

### A. 임베딩 전송 (embedder.py — 완료, 잔여 T003b)

완료분은 위 "이미 커밋된 것" 참조. 잔여는 공유 세마포어(FR-008)·dim 지문 — 둘 다 실측 후 판단이며
**게이트 재현(SC-001)의 선행조건 아님**. 기본 동작으로 진행하고 병렬 이득 실측(T022)에서 결론.

### B. embed_step 재작성 (FR-003·004·016)

현 결함: 코드노드를 **`summary` 만** 임베딩([embed_step.py:155](../../analysis_flow/step7_post_processing/embed_step.py#L155))
→ 검색이 기대하는 `compose_embed_text`(강화텍스트)와 다른 세상. `:EMBEDDED`·인덱스·지문·스트림 전무.

재작성(파이프라인 **끝** = 그래프 완성 시점이므로 테이블관계까지 걷힘):

- **코드노드**: state 가 아니라 **그래프에서** 재료 조회 —
  `neo.run_read_query(FETCH_MATERIAL)` → 각 row `compose_embed_text(row)` → `embedder.encode_texts` →
  `UNWIND $items AS item MATCH (n {id:item.id}) SET n.embedding=item.vector SET n:EMBEDDED`
  (라벨 무명 매치 유지 — 코드 라벨은 antlr 열린집합).
- **지문**: `MERGE (m:EMBED_META) SET m.fingerprint=$fp` ($fp=`embedder.fingerprint()`). 검색 기동/요청이
  이 값을 질의 임베더 지문과 대조(FR-006).
- **벡터 인덱스**: `CREATE VECTOR INDEX embedded_code_vec IF NOT EXISTS FOR (n:EMBEDDED)
  ON (n.embedding) OPTIONS {indexConfig:{`vector.dimensions`:1024,`vector.similarity_function`:'cosine'}}`.
  ★IF NOT EXISTS 필수(재생성 오류 방지). 인덱스=스키마 → `wipe_graph`(DETACH DELETE=데이터만) 생존.
  run 은 `CALL db.awaitIndexes()` 로 ONLINE 대기 후 종료(FR-004 — 검색은 분석 후에만 발생).
  차원 불일치 옛 인덱스 존재 시 CREATE IF NOT EXISTS 는 no-op → **수동 DROP 안내 로그**(눈대중 금지).
- **Table/Column**: 현행 `_collect_table_texts`/`_collect_column_texts` 유지(description 기반, :EMBEDDED
  안 붙임, 임베더는 이미 원격 1024d 로 교체됨).
- **진척 스트림**: `stream = cfg.get("stream")` 후 `shared/log/processing_progress.py` 의
  `track_processing`/`emit_processing_event` 재사용(step6 선례, entity_kind="code_node", 예 120/124).
  DRY — 손수 emit dict 만들지 않음.
- **커버리지·절단 로그**: 대상 수 vs EMBEDDED 수, 서버 한도 초과 노드 절단 시 로그(조용한 절단 금지).

### C. 검색 코어 이식 + per-request DB (FR-001·002·012·014) ★핵심 — §3 긴장 해소

놀이터는 **전역 싱글턴 3종**에 갇혀 요청별 DB 전환이 불가하다. 이식 = 전역을 **요청 DB 조회**로 재배선.
전역 상태의 유일한 잔존은 `search/corpus.py` 의 **DB별 키 캐시**(싱글턴 아님).

| 놀이터 (전역 싱글턴) | analyzer 이식 (요청별 DB) |
|---|---|
| `_common.get_driver()` — .env 전역 드라이버 | `Neo4jClient()` — `get_override()` contextvar → **요청 DB**(analyze 규약) |
| `_common.get_embedder()` — 로컬 SentenceTransformer | `shared.llm.embedder.encode_texts` — 원격 bge-m3(질의 1건은 가벼움) |
| `Recaller.dense` = `self.vecs @ qv` 전량 코사인 | `CALL db.index.vector.queryNodes('embedded_code_vec', fetch_k, $qv)` (요청 DB) — `dense_impl=scan` 시 EMBEDDED 벡터 로드+numpy 폴백(SC-002) |
| `build_or_load()` — 전역 `.npz` 코퍼스+벡터 | `search/corpus.py` — DB별 캐시(키=uri+database), 코퍼스 메타·bm25·structural 텍스트 lazy 로드 |
| `ClusterRetriever._load_fan_in()` 1회 | 같은 DB별 캐시에 fan_in 포함 |
| 용어집.{db}.json 전역 로드(rewrite) | 요청 DB별 용어집 로드(rewrite·판단은 검색 LLM) |

- **캐시 무효화**: analyze 는 매 run wipe+rebuild → 캐시 키(uri+database)에 **:EMBED_META 지문 +
  코퍼스 서명(노드 수 등)** 을 함께 저장, 불일치 시 재로드. "요청별 DB 전환 공짜"(각 DB 캐시 엔트리 선택).
- **모델 불변식(FR-006)**: corpus 로드 시 요청 DB `:EMBED_META.fingerprint` 를 `embedder.fingerprint()`
  와 대조, 불일치 → 요청 거부(503, `error` 이벤트). EMBEDDED 인덱스 부재 → "임베딩 없음" `error`.
- **CE 리랭커·config**: CE 모델(`dragonkue/bge-reranker-v2-m3-ko`)은 DB 무관 → 프로세스 1회 로드
  (전역 허용, 모델이지 DB 상태 아님). 검색 토글·임계는 `search/config.py`(놀이터 config 이식).
- **판단 LLM 분리**: judge·rewrite 는 `ROBO_SEARCH_LLM_CONFIG`(gpt54_mini) — 분석 LLM 과 별도 env.
  캐시 파일은 `data/` 아닌 전용 캐시 디렉토리.
- **결정론 코어 불변**: 이식은 알고리즘 동형(같은 채널·게이트·심판). progress 콜백은 **side-channel** —
  코어 반환값에 영향 0(SC-001 게이트가 progress 유무와 무관하게 성립).
- **bm25/structural 인메모리 유지**: 게이트 재현 우선. fulltext 인덱스 이관은 **후속 스펙**(범위 밖 명시).
- **CE 성능 다이얼(2026-07-11 추가 — CPU 확정에 따른 속도 개선, 단일 스위치·분기 증식 없음)**:
  ⓐ `cluster_ce_top`(기본 15, env `ROBO_SEARCH_CE_TOP`) — CE 채점을 RRF 상위 N만. 미채점 꼬리는
  합집합 게이트의 **코사인 축으로만** 통과 가능(알고리즘 변경 아님 — CE=0 취급, 게이트 공식 불변).
  ⓑ CE 백엔드 `ROBO_CE_BACKEND=torch|onnx-int8`(기본 onnx-int8, 실패 시 torch 폴백 경고) —
  ONNX INT8(avx2) 동적 양자화, 점수 근사 동일(대조 검증 필수). ⓒ CE 점수 디스크 캐시
  (`.search_cache/ce_scores.json`, 결정론·모델키 포함). **셋 다 게이트(SC-001)로 검증** — 성적
  하락 시 해당 다이얼 원복(눈대중 금지). 근본해결(GPU 리랭커 서빙)은 tasks TODO 절.

### D. 스트림 검색 API (FR-001·013·015)

- **`POST /robo/search/semantic {query, top_n?}`** → analyze 와 동일 배선:
  `set_override(Neo4jOverride.from_headers(request.headers))` → `NDJSONStream` → `asyncio.create_task(run)`
  → `StreamingResponse(stream.iter(), media_type="application/x-ndjson")`.
- **run**: 단계별 async 오케스트레이션 — 각 단계 시작에 `stream.emit({"type":"progress","stage":...,
  "label":<한국어>,"detail":...,"done":false})`(rewrite→recall→rerank→expand→judge, FR-013 어휘) →
  완료 시 `stream.emit({"type":"result","clusters":[...],"meta":{queries_used,coverage}})` →
  `await stream.close()`(=stream_end). 예외 → `stream.emit({"type":"error","message":...})` 후 close.
  결과 0건 → `result` with `clusters:[]`(에러 아님).
- **스레드 안전(progress emit)**: CE 추론·numpy 는 CPU 바운드 → 단계별 `asyncio.to_thread` 로 감싸고
  progress emit 은 **루프에서**(단계 경계) 수행 → CPU 스레드가 asyncio.Queue 를 직접 만지는
  스레드 비안전 회피. retriever 는 `retrieve()`(콜백 None=게이트/eval)와 스트림 경로 공용.
- **동시성·취소(FR-015)**: analyze 의 "동시 1건 registry" **미적용**(검색=읽기전용·동시 허용).
  클라이언트 disconnect → StreamingResponse 소비 중단 → task 취소(analyze 와 동일 기전, 구현 시 실측).
- **로그 미러**: `progress`/`result`/`error` 를 `ndjson_stream._EVENT_LOGGER` 에 추가(관측성 —
  현재 미등록이라 미러 누락). `id`+`element_id` 병기는 result 조립 Cypher 에서 `elementId(n)` 동시 반환.

### E. MCP over HTTP (FR-002)

- `mcp` SDK(FastMCP) streamable-http 앱을 `/robo/mcp` 에 mount. 도구 = `cluster_retrieve`(같은
  `retriever.retrieve()` 코어 호출, non-stream — MCP 는 최종 결과만). `search_relational`(text2cypher)은
  1차 범위 밖(cluster 만).
- 기동 워밍: CE 모델 로드(비차단 백그라운드, 준비 전 503). 지문 검증은 **요청 시점**(요청 DB 별로
  다르므로 기동 시 단정 불가).

### F. 프론트 (FR-009·016 + UI)

- `nodeLabels.ts`: `MARKER_LABELS = new Set(['EXTERNAL','EMBEDDED'])` — rolesOf/isStubMarker 일반화
  (`labels[0]` 위치 불변, EMBEDDED 를 역할로 표시 안 함).
- **analyze 진행 UI**: embed_step `processing_event`(step=`step7.embed`) 를 phase 목록에 추가(FR-016).
- **검색창**(그래프 탭 Overview): 질의 입력 → `/api/gateway/robo/search/semantic` **NDJSON 스트림 소비**
  (progress 표시 → result 뭉치 리스트: 이름·역할·요약·관련도) → 노드 클릭 시 기존 상세로 연결.
  새 검색 시 이전 요청 AbortController abort. 실물 Playwright 검증.

### G. 게이트웨이·아키텍트 (FR-010, F)

- gateway `application.yml`: robo-analyzer Path 에 `/api/gateway/robo/search/**,/api/gateway/robo/mcp/**`
  추가(catalog 포괄 `/robo/**`→5503 보다 **앞** — 현 선언 순서 확인 후).
- 아키텍트 `.mcp.json` 템플릿: robo-cluster 항목 stdio → `{"type":"http","url":
  "http://127.0.0.1:5502/robo/mcp"}`(템플릿 위치 T 단계에서 grep 특정).

## 검증 계획 (SC 매핑)

1. 재임베딩 CLI 로 양 코퍼스(test·neo4j) 임베딩 → 손실 0·차원 1024·EMBEDDED 수=대상 수·소요(SC-003)
2. 인덱스 vs 전량 코사인 상위 100 대조 probe — 골드 정답 누락 0(SC-002). 대규모 recall 은 합성 실측.
3. 브리지 모드 게이트(두 골드셋): 위험질의 SKT ≥7/7·쇼핑몰 ≥11/11(18/18) **AND** full cluster
   gold 합산 ≥52/53 (SC-001) — **통과 전 D·E·F 진행 금지**
4. 스트림 실물: `/robo/search/semantic` NDJSON progress→result→stream_end, 결과0=`clusters:[]`,
   임베딩없음=`error`, **두 DB 번갈아 검색해 각기 다른 코퍼스 반환**(전역 싱글턴 없음 확인), abort 중단(SC-008)
5. REST·MCP 호출 실물(SC-004) + `.mcp.json` URL 등록 스모크 + 프론트 검색창·EMBEDDED 역할 비표시(SC-006)
6. 모델 불일치 지문 시 요청 거부(SC-007) — env 바꿔 재임베딩 후 검색 시도
7. text2sql 인덱스 6종 무변화 재확인(SC-006) · 병렬 이득 실측(T022, 무이득 시 순차 원복)

## Complexity Tracking

위반 없음. bm25/structural 인메모리(DB별 캐시) 유지는 "게이트 재현 우선, fulltext 이관은 후속 스펙"
으로 명시된 staging(숨은 캡 아님). DB별 캐시는 다수 DB 시 메모리 누적 가능 → LRU 축출은 비차단
후속(현재 소비 DB 소수라 무영향, `log()` 로 로드 가시화).
