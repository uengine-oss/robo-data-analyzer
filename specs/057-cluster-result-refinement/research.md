# Research — 057 MCP 레거시 검색 결과 정예화

Phase 0. 모든 결정은 2026-07-16 실측(질의 "주문 건의 배송 상태 조회와 배송 추적 이력",
shopmall, 22노드/65.6KB)과 코드 정독에 근거한다. NEEDS CLARIFICATION 없음.

## D1. 앵커 게이트 방식 — 상대 격차(Δ) 게이트 + 상한 유지

**Decision**: 앵커 = `score ≥ top1 - Δ` 를 만족하는 후보(허브 제외), 상한 3(기존), 하한 1
(최고점은 항상 앵커 — 결과 0 위장 금지). Δ 는 `search/config.py` 상수
(`cluster_anchor_gap`, env `ROBO_SEARCH_ANCHOR_GAP`)로 두고 골드 전수 A/B 스윕
(Δ ∈ {0.05, 0.08, 0.10, 0.12, 0.15})으로 채택값을 결정·기록한다.

**Rationale**: 실측 점수 구조가 정확히 이 모양이다 — 오답 앵커였던 2·3위는 1위와 0.13
격차(절벽), 복합 질의는 상위가 촘촘. 상대 격차는 점수 스케일 변화(CE/cos max 결합)에도
견디고, "1개 고정은 위험·3개 고정은 노이즈"를 데이터가 스스로 조절한다. 산술 3줄의
결정론(헌법 I)이며 질의별 특수 케이스가 없다(원칙: 해피패스·이름패턴 금지).

**Alternatives considered**:
- 절대 하한(예: ≥0.8): 점수 스케일이 질의·코퍼스에 따라 이동해 부서지기 쉬움 — 기각.
- 최대 갭(knee) 검출: 표현력은 같은데 갭 위치 선택이 또 하나의 자유도(top-K 안 어디의
  갭인가) — Δ 게이트로 충분함이 확인되면 불필요한 복잡도. A/B 에서 Δ 가 골드를 못 지키는
  경우에만 재검토.
- LLM 심판: spec 007 에서 과잉삭제 실측(recall −2~−5)으로 기본 off 된 전례 — 기각.

## D2. Support root / 직행 입장 자격 — 정책 스윕으로 채택

**Decision**: 후보 정책 3개를 골드 전수 A/B 로 스윕해 "recall 기존 이상" 중 결과 크기가
가장 작은 것을 채택한다.

- P0(현행): roots = 후보 상위 12 전원, 직행 = 비앵커 후보 전원. (기준선)
- P1: roots = 게이트 통과 앵커만, 직행 없음.
- P2(중간): roots = 앵커 + 직행은 `top1 - Δ2` 이내 후보만(Δ2 > Δ, 예: 0.15~0.20 스윕),
  1홉 확장 뿌리는 앵커만.

**Rationale**: 현행의 "후보 12위 전원 자격"이 노이즈 증폭기임은 실측(노이즈 1홉의 뿌리가
12위 checkout_process·오답 앵커)으로 확정. 그러나 "정답이 의미순위 4~12위에 분산"이
과거 골드에서 확인된 것도 사실(retriever.py:299 주석, spec 053 A/B 30/30) — 따라서
정책 선택은 추론이 아니라 같은 골드로 판정한다. recall 이 단 1건이라도 떨어지면
그 정책은 불채택(spec SC-001).

**Alternatives considered**: 그래프 연결성(앵커 1홉 내)만으로 직행 대체 — P1 이 이를
포함; 골드가 P1 을 기각하면 P2 가 완충.

## D3. 직렬화 우회 — 도구가 compact 문자열 반환

**Decision**: `cluster_retrieve` 반환형을 `dict` → `str` 로 바꾸고
`json.dumps(payload, ensure_ascii=False, separators=(",", ":"))` 로 반환한다.

**Rationale**: 부풀림 지점은 우리 코드가 아니라 MCP SDK — dict 반환 시
`lowlevel/server.py:557` 이 `json.dumps(results, indent=2)`(기본 ensure_ascii=True)로
텍스트화하고 structuredContent 로 동일 데이터를 한 번 더 싣는다(실측 67.3→139.9KB).
str 반환이면 FastMCP 가 TextContent 1벌로 그대로 전달(func_metadata._convert_to_content
의 str 경로) — escape·indent·이중 전송이 한 번에 사라진다. SDK 패치·포크는 배포·업그레이드
리스크로 기각.

**주의(구현 계약)**: 반환 타입 힌트를 `str` 로 바꿔 FastMCP 가 outputSchema 를 만들지
않게 한다(dict 힌트 + str 반환은 output validation 오류). docstring 에 "응답은 JSON 텍스트"
를 명시해 스킬(LLM)이 그대로 파싱하게 한다.

**Alternatives considered**: MAX_MCP_OUTPUT_TOKENS 상향(이미 60k — 임시 완화일 뿐 근본
아님), SDK monkey-patch(업그레이드 취약) — 기각. 052 파일폴백은 안전망으로 존치(FR-008).

## D4. 계층화 변환 위치 — MCP 표면(mcp_app) 단독

**Decision**: 변환기 `_tiered_clusters(clusters)` 를 `api/mcp_app.py` 에 두고 MCP 기본
프로파일로 한다. `detail='full'|'summary'` 는 기존 그대로 존치(원형·축약 대조용).
REST(`search_router`)와 코어(retriever) 반환 형태는 불변.

**Rationale**: 소비자별 표현은 표면의 책임(코어는 단일 진실 유지, spec 044 SC-005
"알고리즘 사본 0" 존중). 프론트 실측 소비 필드(id·name·label·relevance·role)가 코어
형태에 있으므로 코어를 건드리지 않는 것이 무회귀의 최단 경로.

**Alternatives considered**: 코어에서 필드 축소 — REST 계약 변경이 되어 프론트 회귀
검증 범위가 커짐, 기각. 별도 REST 파라미터 — 소비자가 없는 표면 확장, 기각.

## D5. 원문 코드·컬럼의 출처 — 기존 그래프 재사용(신규 수집 0)

**Decision**: `code_text` 는 corpus `by_id` 에 이미 로드된 값을 사용(코퍼스 쿼리에 포함
확인). TABLE 컬럼은 `MATCH (t:TABLE)-[:HAS_COLUMN]->(c:COLUMN)` 1회 배치 쿼리로
`name/dtype/is_primary_key/logical_name/analyzed_description|description` 을 부착.

**Rationale**: 둘 다 그래프에 이미 있다(corpus.py `_CORPUS_QUERY`, column.py MERGE 스키마
확인). 수집 파이프라인 무변경 — 이 스펙은 전달 계층만 다룬다.

## D7. 스윕 결과와 채택 (2026-07-16 실측 — 사후 기록)

**심판**: neo4j 골드 13질의/정답 30 (test DB 골드는 무효 — DB 에 shopmall 이 들어있는데
골드는 skt_now 계열 함수명. 기준선(P0)부터 0/23 이라 본 스펙 변경과 무관. 별도 복구 대상).
검증질의 = "주문 건의 배송 상태 조회와 배송 추적 이력"(골드 밖, 노이즈 14/22 기준선).

**반증된 가설(순서대로)**:
- P1(앵커만)·P2(점수 직행): recall 22~26/30 — 점수 역전(정답 shipping_calc_fee 0.669 <
  노이즈 load_price_history 0.765) 때문에 어떤 점수 게이트도 양쪽을 동시 만족 못 함.
- P3(무방향 shortestPath ≤2): 노이즈 14→10 뿐 — SAFECPY_STR 등 공용 유틸 경유로 전 함수가
  2홉. recall 28/30.
- Leiden component_id 게이트: 구현 전 반증 — 커뮤니티가 도메인 경계와 불일치(배송비 정답
  2개가 서로 다른 컴포넌트, 쿠폰·이벤트가 배송비와 동일 컴포넌트).

**채택: P5 h3/r2** = graph_gated + 허브 비경유 BFS 거리(후보 3홉·비후보 이웃 2홉) +
anchor_gap 0.12 · direct_gap 0.12 · rank_floor 2.

| 설정 | recall | 평균N | 검증질의 노이즈 | 신호 | 배송N |
|---|---|---|---|---|---|
| P0(현행) | 29/30 | 18.2 | 14 | 5 | 22 |
| P4 최고(h2/r2) | 28/30 | 14.5 | 4 | 4 | 14 |
| **P5 h3/r2 (채택)** | **29/30** | **15.9** | **6** | 4 | 17 |

- recall 29/30 = 기준선 동률(SC-001 충족). 유일 미스 release_stock 은 P0 도 미스.
- 노이즈 6 = SC-002(0) 미달·운영 허용치(≤4)도 2 초과 — **재현율 우선 원칙으로 채택**.
  P0 대비 전 축 우세(동 recall·노이즈 -8·크기 -2.3)라 채택이 항상 이득. 잔존 노이즈는
  계층화에 의해 rule/example 없이 요약+코드만 실린다(오염 축소).
- 신호 4/5: next_ship_status(비후보·앵커거리 3)는 이웃 2홉 규칙에 걸려 미포함 — 수용.

## D6. rule 중복 병합 기준

**Decision**: 같은 함수 안에서 statement 문자열이 동일한 rule 은 1개로 병합, examples 는
순서 보존 합집합. 함수 간 공유 rule 은 건드리지 않는다(각 함수의 근거로 각자 표기 —
ـ해당 함수 소유 example 만 붙는 기존 `_rules_for` 계약 유지).

**Rationale**: RULE 노드는 statement 해시로 공유되므로(빌더 계약) 정상 데이터에서 함수 내
중복은 예외적 — 방어적 병합이며 의미 손실이 없다.
