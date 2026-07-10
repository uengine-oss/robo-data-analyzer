# Implementation Plan: 요구사항→레거시 코드 뭉치 검색 API

**Branch**: `main` | **Date**: 2026-07-10 | **Spec**: [spec.md](spec.md)

## Summary

놀이터에 갇힌 검색(게이트 18/18)을 정식 배선으로: ① 임베딩을 사내 GPU 서버(BGE-M3)로 생성해
Neo4j 에 저장 + `EMBEDDED` 마커 + 벡터 인덱스 ② 검색 코어를 analyzer 로 이식(dense=인덱스)
③ REST + MCP over HTTP 로 노출 ④ 게이트웨이·프론트(마커 제외 1곳)·UI 검색창. 관문 =
DB 벡터로 게이트 18/18 재현.

## Technical Context

**Language/Version**: Python 3.12 (analyzer) / Vue3+TS (frontend 1곳+검색창) / Spring Gateway yml 1곳

**Primary Dependencies**: 기존 FastAPI·neo4j driver·sentence-transformers(리랭커 CE 용)·
`mcp` SDK(FastMCP streamable-http — analyzer venv 에 추가) · httpx(임베딩 HTTP)

**Storage**: Neo4j — 코드노드 `embedding`(1024d)+`:EMBEDDED`, VECTOR INDEX `embedded_code_vec`
(EMBEDDED.embedding, 1024d cosine). Table/Column.embedding 도 동일 임베더로 1024d(소비처 없음
실측 — catalog 는 매 호출 재임베딩, text2sql 은 자체 `vector` 사용)

**Testing**: 놀이터 골드·게이트 재사용(검색기 무수정) + 인덱스 vs 전량 코사인 대조 probe +
Playwright 실물(검색창·마커 비표시)

**Performance Goals**: 임베딩 1만 노드 ≤ 1분(실측 43초: 예산 700자·동시 16·분할 백오프) ·
검색 1요청 ≤ 3초(질의 임베딩 61ms + 인덱스 ms + CE/심판)

**Constraints**: 손실 0(헌법 IV) · 모델 일치 불변식 · labels[0] 불변 · 총 동시수 공유 세마포어 ·
측정무결성(게이트 재현 전 API 공개 금지)

**Scale/Scope**: analyzer 신규 패키지 1(search/) + 수정 6파일 · frontend 2(유틸 1줄+검색창) ·
gateway 1 · 놀이터 mcp 폐기

## Constitution Check

| 원칙 | 판정 | 근거 |
|---|---|---|
| I. Determinism-First | ✅ | 인덱스 탐색·배치·라벨은 결정론. LLM 은 기존 검색 코어의 판단(번역·심판)뿐 |
| II. Zero-Branch Strategy | ✅ | embed_step 은 전략 무관(state 의 노드만 봄). `EMBEDDED` 는 라벨 열거 없이 "벡터 저장 시 부여" |
| III. Single Source of Truth | ✅ | 신규 env 는 env_catalog 에만. 임베딩 텍스트 조성 함수는 한 곳(검색·생성 공용) |
| IV. No Silent Failure | ✅ | 전송 실패=분할·백오프 후 raise. 임베딩 없음=명시 오류. 절단=로그 |
| V. KV-Cache-Friendly | ✅ | 생성 프롬프트 무변경(해시 불변) |
| VI. Schema Restraint | ✅ | LLM 출력 스키마 무변경 |
| VII. Submit-Tool Wrap | ✅ | 신규 LLM 경로 없음(검색 코어 기존 방식 이식) |
| VIII. Cohesion | ✅ | 검색 코어는 `search/` 패키지 응집. 임베딩 전송 규율은 embedder.py 한 곳 |
| Cross-Service | ⚠️ **flag** | frontend(nodeLabels 마커 1곳)·api-gateway(라우트 1곳)·아키텍트 `.mcp.json` 템플릿(URL 교체). graph_stream 라벨 계약 주석 갱신 |
| Cost & Safety | ✅ | 임베딩 사내(과금 0)·검색 LLM 은 기존 gpt-mini 경로(질의당 ≤2콜, budget 인자 유지) |

## Project Structure

```text
robo-data-analyzer/
├── shared/llm/embedder.py            # ★재작성 — 사내 서버 HTTP(예산 배치·동시·분할 백오프·모델지문)
├── shared/config/env_catalog.py      # ROBO_EMBED_*(provider/model/api_base/concurrency/char_budget)
│                                     # + ROBO_SEARCH_LLM_CONFIG/API_KEY
├── analysis_flow/step7_post_processing/embed_step.py
│                                     # 텍스트 조성=검색과 동일(compose_embed_text 공용) + :EMBEDDED
│                                     # + VECTOR INDEX IF NOT EXISTS + 커버리지 로그
├── analysis_pipelines.py             # (build_glossary ∥ embed) 2층 병렬 튜플
├── cli_scripts/reembed_graph.py      # ★신설 — 재분석 없이 임베딩·마커·인덱스 재구성 (FR-011)
├── search/                           # ★신설 — 놀이터 engine 이식(검색 코어 단일 사본)
│   ├── embed_text.py                 #   compose_embed_text (embed_step 과 공용 — 단일 진실)
│   ├── recall.py                     #   dense=벡터 인덱스 질의, bm25·식별자=기동 시 코퍼스 캐시
│   ├── rerank.py / fusion.py / expand.py / judge.py / rewrite.py / glossary.py
│   └── retriever.py                  #   ClusterRetriever (config 포함)
├── api/search_router.py              # ★신설 — POST /robo/search/cluster (+커버리지 메타)
├── api/mcp_app.py                    # ★신설 — FastMCP streamable-http 를 FastAPI 에 mount (/robo/mcp)
└── main.py                           # router 등록 + 기동 워밍(임베더 지문·리랭커 로드·모델 일치 검증)

robo-data-frontend/
├── src/shared/utils/nodeLabels.ts    # STUB_MARKER → MARKER_LABELS {EXTERNAL, EMBEDDED}
└── (code-analysis) 검색창            # 검색 입력 + 뭉치 결과 리스트(노드 클릭=기존 상세)

api-gateway/ application.yml          # robo-analyzer Path 에 /robo/search/**,/robo/mcp/** 추가

module_test/임베딩서칭/mcp/           # stdio 서버 폐기(README 에 URL 등록법으로 대체)
```

**Structure Decision**: 검색 코어는 `search/` 신설 패키지(응집). 임베딩 텍스트 조성은
`search/embed_text.py` 단일 함수로 두고 embed_step·검색 양쪽이 import(글자 단위 동일 보장 —
사본 금지).

## 설계 상세 (구현 계약)

### A. embedder.py 재작성 (FR-005·006·007)
- env: `ROBO_EMBED_PROVIDER=sglang | local`(기본 sglang) / `ROBO_EMBED_MODEL=BAAI/bge-m3` /
  `ROBO_EMBED_API_BASE`(기본 분석 LLM api_base 재사용) / `ROBO_EMBED_CONCURRENCY=16` /
  `ROBO_EMBED_CHAR_BUDGET=700`.
- `encode_texts(texts)`: 문자예산 동적 청크 → asyncio+httpx 동시 전송(세마포어 인자 수용) →
  413/424/429 반분할, 단건은 지수 백오프 ≤4회, 최종 실패 raise. 반환 순서 보존.
- `fingerprint()` → `"{provider}:{model}:{dim}"` — 검색 기동 시 저장측 지문과 대조(FR-006).
  저장측 지문은 GLOSSARY 처럼 단일 메타 노드(`:EMBED_META {fingerprint}`)에 embed_step 이 기록.
- local 폴백은 두되 기본 미사용(개발 오프라인용) — 성능 경고 로그.

### B. embed_step (FR-003·004·008)
- 코드노드 텍스트 = `compose_embed_text(node, tables)` — 놀이터 `_compose_embed_text`
  (searchfield 모드)와 **글자 단위 동일**(이식이 곧 원본, 놀이터는 이후 이 함수를 참조).
- 저장: `SET n.embedding=$vec, n:EMBEDDED` (UNWIND 배치) + `:EMBED_META` 지문 upsert.
- 인덱스: `CREATE VECTOR INDEX embedded_code_vec IF NOT EXISTS FOR (n:EMBEDDED)
  ON (n.embedding) OPTIONS {...1024, cosine}`.
- Table/Column: 기존 수집 로직 유지, 임베더만 교체(1024d).
- 파이프라인: framework `(summarize, enrich_table, components, health)` →
  `(build_glossary, embed_step)` / dbms 동일 패턴. 병렬 이득은 전/후 실측, 무이득 시 순차 원복.

### C. 검색 코어 이식 (FR-001·002·012)
- dense 채널: `db.index.vector.queryNodes('embedded_code_vec', fetch_k, $qv)` (+동일 Cypher 에서
  1홉 CALLS·READS/WRITES 수집 — 왕복 1회).
- bm25·식별자 채널: 기동 시 코퍼스 1회 로드·캐시(현행 재현 우선). ★후속 스펙 후보: fulltext
  인덱스 이관(범위 밖 명시 — 게이트 재현이 먼저).
- 심판·번역 LLM: `ROBO_SEARCH_LLM_CONFIG`(기본 gpt54_mini) — 분석 LLM 과 분리. 캐시 파일은
  analyzer `data/` 아님 → `logs/` 밖 전용 캐시 디렉토리.
- 게이트 하니스: 놀이터 eval 은 그대로 두고, engine import 경로만 analyzer `search/` 로 바꾼
  **브리지 모드**(`ROBO_SEARCH_IMPL=analyzer`)로 같은 골드·같은 측정기로 재현 판정(SC-001·002).

### D. API·MCP (FR-001·002·010)
- REST: `POST /robo/search/cluster {query, top_n}` → 뭉치 + meta(queries_used, coverage).
  400(빈 질의)·503(임베딩 없음/지문 불일치).
- MCP: FastMCP(`mcp` SDK) streamable-http 앱을 `/robo/mcp` 에 mount. 도구 = cluster_retrieve
  (+search_relational 은 text2cypher 이식 여부에 따라 — 1차 범위 밖, cluster 만).
- 기동 워밍: 임베더 지문 검증 + CE 모델 로드 + 코퍼스 캐시(비차단 백그라운드, 준비 전 503).
- gateway: `Path=...,/api/gateway/robo/search/**,/api/gateway/robo/mcp/**` 를 robo-analyzer
  라우트에 추가(catalog 포괄보다 앞 — 현 선언 순서 확인됨).

### E. 프론트 (FR-009 + UI)
- `nodeLabels.ts`: `MARKER_LABELS = new Set(['EXTERNAL','EMBEDDED'])` — rolesOf/isStubMarker 일반화.
- 검색창: code-analysis 화면에 질의 입력 → `/api/gateway/robo/search/cluster` → 결과 노드
  리스트(이름·역할·요약·관련도) + 클릭 시 기존 노드 상세로 연결. 실물 Playwright 검증.

### F. .mcp.json 경로 (아키텍트)
- 아키텍트가 뿌리는 `.mcp.json` 템플릿에서 robo-cluster 항목을 stdio → `{"type":"http",
  "url":"http://127.0.0.1:5502/robo/mcp"}` 로 교체(템플릿 위치는 T 단계에서 grep 특정).

## 검증 계획 (SC 매핑)

1. 재임베딩 CLI 로 양 코퍼스 임베딩 생성 → 손실 0·차원 1024·소요(SC-003)
2. 인덱스 vs 전량 코사인 상위 100 대조 probe — 골드 정답 누락 0(SC-002)
3. 브리지 모드 게이트: SKT 7/7·쇼핑몰 11/11(SC-001) — **통과 전 D·E 진행 금지**
4. REST·MCP 호출 실물(SC-004) + `.mcp.json` URL 등록 스모크
5. 프론트: EMBEDDED 역할 비표시 + 검색창 동작 스크린샷(SC-006·004)
6. 모델 불일치 기동 거부(SC-007) — env 바꿔 기동 시도
7. text2sql 인덱스 6종 무변화 재확인(SC-006)

## Complexity Tracking

위반 없음. (bm25 인메모리 유지는 "재현 우선, fulltext 이관은 후속 스펙" 으로 명시된 staging —
숨은 캡 아님.)
