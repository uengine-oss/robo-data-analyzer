# 078 Gold Corpus — 청크 경계·키 정답지

- `fixtures/<format>/<name>.<ext>`: 입력 원문. 인코딩 특수 fixture(cp949/crlf)는
  `make_binary_fixtures.py`가 생성한다(저장소에는 생성 결과를 커밋).
- `expected/<name>.json`: 정답. 형식:
  - `format`: 어댑터 라우팅 형식
  - `fail_closed`: true면 청크 0개 + 실패 사유가 정답
  - `chunks`: 순서대로 `{text, keys, semantic_kind}` — `text`는 원문 슬라이스와
    **문자 단위로 동일**해야 하며 채점기는 span·hash·무중첩 불변식을 함께 검사한다.
- 채점기: `gold_loader.py` (S2 어댑터 테스트가 소비).
- 링킹 정답지: `../../step5_linking/link_external/gold/linking_cases.json`.

## Held-out 선언 (T105)

- train(개발 중 관찰 허용): 이 gold fixture 전체 + `분석대상모음/loan-ejb-app`
- **held-out(구현·튜닝 중 관찰 금지, S7 E2E에서만 실행)**: `분석대상모음/ukesa`,
  `분석대상모음/ofbiz-order-standalone`
- Agent skill의 held-out은 별도: 같은 형식 파일 집합을 제안용/검증용으로 분리
  (agent-verifier-contract 참조).
