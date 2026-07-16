# Contract — MCP 도구 `cluster_retrieve` (owner: robo-data-analyzer)

**소비자**: robo-architect 스킬 실행기(`api/platform/skill_runner.py` — LEGACYQ/LEGACYREF
마커) 및 `legacy_provenance._compact_nodes`(id/name/label/summary/relevance/rules 소비),
그리고 스킬(LLM) 자신.

## 요청

```
cluster_retrieve(query: str, database: str|None = None, detail: str = "tiered")
```

- `detail`: `"tiered"`(기본 — 계층화 프로파일, data-model.md) | `"full"`(코어 원형 전량,
  디버그·대조용).
- 뒤호환: 기존 기본값이던 `"full"` 을 명시 호출하는 소비자는 동작 불변.
  `"summary"` 는 삭제(호출자 전수 0 실측 — tiered 가 목적을 상위 호환 대체).

## 응답

**JSON 텍스트 1벌**(compact, `ensure_ascii=False`):

```
{"clusters":[{"score":0.89,"nodes":[<data-model.md 형태>]}],"meta":{...}}
```

- 오류: `{"error":"<메시지>"}` (기존과 동일 — 빈 질의, SearchUnavailable 등).
- **문자열 반환 이유(계약 고정)**: dict 반환 시 MCP SDK 가 escape+indent 텍스트화 +
  structuredContent 이중 전송으로 2.1배 부풀려 CLI 한도 초과(파일 치환)를 유발했다
  (2026-07-16 실측 67.3→139.9KB). 문자열은 TextContent 1벌로 그대로 전달된다.
  이 반환형을 dict 로 되돌리는 변경은 회귀다.

## 순서·오류·재시도

- 무상태 단발 호출(stateless-http). 재시도는 소비자 재량(멱등 — 읽기 전용).
- 초대형 결과로 CLI 가 파일 치환하는 경우는 여전히 가능(극단 케이스) —
  아키텍트 052 파일폴백이 전량 복원(안전망, 본 계약의 실패 아님).

## 버전·호환성

- 2026-07-16 (spec 057): `tiered` 신설·기본화, 응답 문자열화, `code_text`/`columns` 추가,
  `search_text`/`element_id` 제거(tiered 한정 — full 은 원형 유지).
- 아키텍트 측 소비 코드 변경 필요 0 — evidence/TABLE 의 `rules:[]` 유지로
  `_compact_nodes` 형태 안정. (검증 태스크에서 실증)

## 소유·연락

- 알고리즘(선정)의 단일 진실: `search/retriever.py` + `search/config.py` (REST 와 공유).
- 표현(계층화)의 단일 진실: `api/mcp_app.py` 변환기 — 다른 곳에 사본 금지.
