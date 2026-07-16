# Quickstart — 057 검증 러너 (단위 → 골드 A/B → 실API → e2e)

전제: Neo4j 7687 + analyzer(로컬 15502) 기동, shopmall 그래프 최신(FUNCTION 127).
실행 위치: `D:\work\robo\project\robo-data-analyzer` (venv python 사용).

## 1. 단위 테스트 (선정 게이트·계층화 변환)

```
.venv\Scripts\python.exe -m unittest search.test_global_anchor -v
.venv\Scripts\python.exe -m unittest api.test_mcp_tiered -v
```

기대: 전부 OK — 게이트(절벽/촘촘/플랫/하한1), 변환(V1~V7, data-model.md).

## 2. 골드 전수 A/B (선정 변경의 유일한 심판 — SC-001)

```
.venv\Scripts\python.exe -m search.eval.anchor_ab_eval   # 사용법은 파일 헤더 참조
```

기대: 채택 정책·Δ 에서 recall 이 기준선(현행 P0) **이상**(shopmall 30/30 포함).
스윕 결과 표를 research.md 말미에 추가 기록한다.

## 3. 실API — 검증 질의 노이즈·전문성 (SC-002·003)

```
POST http://127.0.0.1:15502/robo/search/semantic {"query":"주문 건의 배송 상태 조회와 배송 추적 이력"}
```

기대(선정 반영 후): 무관 도메인(가격·정산·쿠폰·회원등급·장바구니) 함수 0.
MCP 도구 직접 호출(또는 변환기 단위 호출)로: 모든 FUNCTION 에 code_text,
모든 TABLE 에 columns, search_text/element_id 없음, 직렬화 compact(SC-004: ≤원내용 1.1배).

## 4. e2e — 아키텍트까지 (SC-005)

1. analyzer 재시작 → architect-api 재시작(순차 — robo.ps1 동시 호출 금지).
2. `verify_052.py`(제안 생성→intent SSE→GET legacyReferences) 실행.
   기대: SSE `legacy_ref` nodes>0(유관만), 파일폴백 미발동, `⛓` 라인.
3. architect 웹(:15173) 최신 PRO 상세 — `.lref-chip` 칩+팝오버 스크린샷을 직접 확인.

## 5. 프론트 무회귀 (SC-006)

Analyzer UI(:3000) 그래프 탭 의미검색 — 동일 질의 실행, 결과 뭉치 표시·캔버스 매칭·
콘솔 오류 0 확인(소비 필드 id/name/label/relevance/role 불변).

## 롤백

- 선정: `ROBO_SEARCH_ANCHOR_GAP` env 로 게이트 완화, 정책은 config 상수 원복.
- 표현: `detail='full'` 호출로 즉시 원형 대조 가능(코드 원복 불필요).
