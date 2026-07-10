# 프론트엔드 회신 — 그래프 탭 "의미 기반 코드 검색" 백엔드 계약 확정

> robo-data-frontend 핸드오프 §7 "회신해줄 것" 6항목에 대한 확정 답변.
> 근거: analyzer `api/analyze_router.py`(스트림·헤더 템플릿) · `shared/neo4j/connection_context.py`
> (per-request DB) · `shared/log/ndjson_stream.py`(NDJSON) · 놀이터 게이트 52/53 실측.

## 1) 엔드포인트 경로 + 게이트웨이 라우트

- **`POST {ROBO_BASE_URL}/search/semantic`** (= analyzer `POST /robo/search/semantic`, 5502).
- **게이트웨이**: `/robo/search/**` → analyzer(5502) 규칙을 catalog 포괄(`/robo/**`→5503)보다 **앞**에.
  (analyze 처럼 5502 전용. api-gateway 담당 합의 필요 — spec 044 FR-010.)
- 취소용 보조 경로는 **두지 않음** (아래 5 참조 — AbortController 방식).

## 2) progress 이벤트 stage/label (실제 cluster_retrieve 파이프라인 기준)

파이프라인 = 질의보강 → 다채널 리콜 → CE 리랭크 → 관계확장 → LLM 심판. 실제 단계에 맞춘 확정 목록:

| 순서 | stage | label(한국어) | detail 예 |
|---|---|---|---|
| 1 | `rewrite` | 질의 이해·용어 보강 중 | `배송망(약어·표준어 병기)` |
| 2 | `recall` | 관련 코드 후보 찾는 중 | `후보 112개` |
| 3 | `rerank` | 관련도 채점 중 | `30개 정밀 채점` |
| 4 | `expand` | 호출관계로 뭉치 넓히는 중 | `+8개` |
| 5 | `judge` | 관련 없는 것 걸러내는 중 | `33→22` |

`{"type":"progress","stage":"recall","label":"관련 코드 후보 찾는 중","detail":"후보 112개","done":false}`
- 단계 시작마다 1개. `rewrite` 는 보강 미발동 시 생략 가능(임의 개수 허용 계약대로).

## 3) result.nodes[].id = 어떤 키인가

- 검색 코어 내부 id = **`node.id` 속성**(예: `<package>.<module>.<function>` 형태).
  이는 catalog `/robo/graph` 응답 노드의 `id` **속성과 동일 체계**(elementId 아님).
- **응답에는 `id`(=node.id 속성)를 준다.** 프론트가 캔버스 매칭을 elementId 로 한다면,
  같은 조회에서 `elementId(n)` 도 함께 실어 `element_id` 필드로 병기한다(둘 다 제공 →
  프론트가 택). **회신 확정 필요**: 캔버스가 `node.id` 속성으로 매칭 가능한가? 아니면
  `element_id` 를 써야 하는가? (both 제공하므로 프론트 무변경 가능.)

## 4) score 정규화

- `score` = **0~1**. 뭉치 대표 relevance 를 그대로 노출(검색 코어의 relevance 는 이미 0~1 정규화 —
  rerank 순위 기반 `(n-i)/n` 융합). 뭉치 내 최상위 노드 relevance 를 뭉치 score 로.
- 노드별 `relevance`(0~1)도 각 노드에 병기.

## 5) 취소 방식

- **(a) AbortController.** 프론트가 fetch 를 끊으면 서버가 연결 종료를 감지해 백그라운드 작업
  중단(analyze 와 동일 — StreamingResponse + create_task, 클라이언트 disconnect 시 task 취소).
  별도 control 엔드포인트 없음(검색은 단발성).

## 6) 동시성 정책

- **검색은 동시 여러 건 허용**(analyze 의 "동시 1건"과 다름 — 검색은 읽기전용·독립). 프론트가
  새 검색 시작 시 이전 것을 abort 하는 계약을 서버가 존중(5-a). 임베딩 서버 호출은 spec 044
  공유 세마포어로 총 동시수 통제(질의 임베딩 1건은 가벼움).

---

## ★백엔드 내부 결정 (프론트 무관, 기록용)

- **per-request DB**: 검색도 `X-Neo4j-*` 헤더의 DB 를 대상으로 조회(analyze 규약). 검색 코어는
  전역 캐시 임베더가 아니라 **요청 DB 의 벡터 인덱스**를 조회하도록 이식(Phase 0 실측: 인덱스
  조회 = 전량 코사인 동치, 53/53 정답 일치). → 요청별 DB 전환이 공짜.
- **질의 임베딩 = 노드와 동일 모델(BGE-M3)** 강제(FR-006). 불일치 시 요청 거부.
- **임베딩 없는 그래프**(EMBEDDED 인덱스 부재) → `{"type":"error","message":"이 그래프에는
  검색 임베딩이 없습니다 (분석을 다시 실행하세요)"}` (빈 결과를 성공으로 위장 금지 — US1 AC3).
- **결과 0건** → `{"type":"result","clusters":[],...}` 정상 전송(에러 아님).
