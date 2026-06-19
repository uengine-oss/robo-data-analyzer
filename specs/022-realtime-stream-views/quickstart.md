# Quickstart: 실시간 스트림 + 일관 보기모드 검증

목표 = SC-001~007을 라이브로 확인. **자가검증 하니스(memory manual §3)**로 내가 직접 수행.

## 선결 조건

1. **게이트웨이↔카탈로그 복구**(범위 밖이나 필수): gateway 9000 → catalog 5503 모든 `/robo/*`가 현재 HTTP500. 라이브검증 전 복구(재기동/라우트 점검).
2. 풀스택 기동: Neo4j 7687 · antlr 8081 · gateway 9000 · catalog 5503 · analyzer 5502(새 코드) · frontend dev 3000(HMR).
3. 데이터: `project/data/{source,analysis,ddl}/RWIS`(dbms 9파일). framework 샘플 1종.

## 시나리오 A — 라이브 스트림 (US1 / SC-001·002·003)

1. frontend 3000에서 RWIS 재분석 시작(또는 `POST /api/gateway/robo/analyze {strategy:"dbms"}`).
2. **관찰**:
   - 첫 시스템 step(DDL) 완료 ~수초 내 트리/그래프에 TABLE·COLUMN 등장(분석 끝까지 안 기다림) → **SC-001**.
   - 콘솔에 step별 message가 한 줄씩 누적("✅ DDL 파싱 완료 — …") → **SC-002**.
   - 링킹 step 후 관계 수가 0이 아님 → **SC-003**.
3. Playwright 헤드리스로 분석 중 스샷 3장(초기/중반/완료) → Read로 확인.

## 시나리오 B — 보기모드 일관 (US2 / SC-005·006)

1. 분석 완료 후 "보기:모듈간" → 모듈+테이블 노드가 펼쳐져 보이고 가상 사용관계 그려짐(빈화면·뭉침 0) → **SC-005**.
2. "보기:영역" → component_id 노드가 색 경계로 감싸짐. "보기:건강" → risk_level 색 강조.
3. framework 샘플 재분석 후 같은 3보기 → dbms와 동작 동일(내용만 다름) → **SC-006**.
4. Playwright 스샷으로 각 보기 확인(NVL 캔버스는 헤드리스 한계 있으나 노드 펼침·헐·패널 확인 가능).

## 시나리오 C — 카운트 (US3 / SC-004)

1. 분석 후 진행 패널 "노드 N · 관계 M" vs 오버뷰 "총 X nodes · Y relationships" 일치 확인 → **SC-004**.

## 시나리오 D — 회귀 (SC-007)

1. 드래그-드롭 1노드 → 1-hop 확장 더블클릭 → 정상.
2. 노드 패널·일시정지/재개 정상.

## 합격 기준

SC-001~007 전부 라이브 확인 + 그래프 쿼리(neo4j/an1021402) 대조. NVL 캔버스 픽셀은 헤드리스 한계 — 노드 수/구조/콘솔/트리는 렌더 확인 가능.
