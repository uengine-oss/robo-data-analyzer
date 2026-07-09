# Data Model: 041-summary-search-split (Phase 1)

## 신설 필드: `search_text`

| 위치 | 정의 | 비고 |
|---|---|---|
| `data_models/code_analysis_output.py` `SummaryOutput.search_text` | `str = ""` (Field description: 검색 전용 서술) | framework 분할1 응답 |
| 〃 `BatchSummaryItem.search_text` | `str = ""` | dbms 배치 분할1 응답 |
| `data_models/graph_nodes.py` `CodeNode.search_text` | `Optional[str] = None` | 분석 결과 구역(summary 옆) |
| Neo4j 코드노드 속성 `search_text` | string, 빈값 미저장(`nonempty`) | additive — 계약 파급 0 |

**내용 규칙(프롬프트 [4]로 강제)**: 조건분기(조건→결과 쌍)·상태전이·상태/결과 코드 값 전부(IN 리스트 등 값 집합 누락 0)·R/W 테이블과 업무 맥락. 코드 문장 복붙 금지, 코드 어휘(테이블명·리터럴)는 자연어 설명과 병기. 관점 = "의미 검색에 걸리는 문장". 단순 노드(위임·COMMIT 등)는 빈 값 허용.

## 데이터 흐름

```
LLM 분할1 응답 (SummaryOutput / BatchSummaryItem)
  └─ fan-in: fn.search_text = out.search_text
       ├─ three_split_llm_fanout.analyze_code_three_split (framework)
       └─ batch_analyze._apply_node_analysis (dbms)
  └─ 저장: code_node.to_summary_item() → SET node += item  (빈값 미저장)
  └─ 소비:
       ├─ 놀이터 fetch_corpus → _compose_embed_text(searchfield 모드) [임베딩·BM25 재료]
       ├─ (후속) CE passage·용어집 생성 재료 — 이 스펙 범위 밖, 필드만 준비
       └─ 프론트: 소비 없음 — FallbackCard HIDDEN_KEYS로 차단
  └─ 상향 전달: 없음 (ctx.summary만 유지 — research D8)
```

## Phase C 변경 (summary 형식)

| 항목 | 현행 | 다이어트 후 |
|---|---|---|
| 형식 | `[핵심] 한 줄` + 빈 줄 + 자유 서술 | 마커 없는 1~3줄(비어있지 않은 줄 ≤ 3) |
| validator (SummaryOutput) | `[핵심]` 없으면 거부 | **정규화**(마커 벗김 + 3줄 초과 절단, 실패 없음 — research D7 개정) |
| validator (BatchSummaryItem) | 없음 | SummaryOutput과 동일 정규화(일관화) |
| 분기·코드값 세부 | summary 내용 섹션 | search_text로 이관(요약에서 중복 금지) |
| 프론트 표시 | headline=첫 문단, detail=나머지 | 동일 로직 그대로 — 전체가 headline이 되고 detail 빈 값(깨짐 없음) |
