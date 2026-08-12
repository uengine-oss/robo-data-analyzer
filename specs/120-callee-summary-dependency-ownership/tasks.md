# tasks 120 — callee summary 의존 소유권 정합

각 task는 `red 반증 → 구현 → 표적 검증 → 반대 사례 → 전체 회귀 → diff 감사`로 끝낸다.

- [x] **T1. red — 증거 수신자와 정렬 주체 불일치 고정**
  `tests/pipeline/step7_analyze_code/test_summary_dependency_plan.py`에 비순환 DBMS
  `target < CALL < owner` 계약을 먼저 실패시킨다.

- [x] **T2. red — 순환 경계 고정**
  같은 파일에 자기재귀 DBMS, 상호재귀 DBMS, 상호재귀 framework 시나리오를 추가한다.
  구조 child-before-parent와 위상 잔차 0을 단언한다.

- [x] **T3. 단일 summary dependency plan 구현**
  `analysis_order.py`에 context call edges·structural edges·cycle metadata·schedule edges를
  한 번에 반환하는 결정론 함수를 구현한다. SCC 내부 call만 제거하고 structural은 보존한다.

- [x] **T4. framework 소비자 전환**
  `framework_per_node.py`가 raw state CALLS 대신 plan의 schedule edges를 사용하게 한다.
  기존 acyclic/cycle 조기 summary release 테스트를 유지한다.

- [x] **T5. DBMS 소비자 전환**
  `batch_analyze.py`에서 step7의 `routine_call_pairs`를 제거하고 같은 plan을 사용한다.
  routine resolver를 옆에 남겨 이중 경로를 만들지 않는다.

- [x] **T6. 실제 rwis 오프라인 전수**
  495/460/5 좌표와 internal 두 건의 `6→7→9`, rounds 10, cycles 0을 확인한다.
  재현 가능한 검사를 테스트 또는 `evidence_preflight` 상설 축으로 남긴다.

- [ ] **T7. 1층 전체 회귀**
  analyzer unittest `-t .`, prompt byte identity 28, parser Maven 135를 실행한다.

- [ ] **T8. 결정론 preflight와 실행 부작용 점검**
  `scripts/evidence_preflight.py measure`를 실행하고 로그 보존이 framework/DBMS 각 2개 계약에서
  무엇을 지우는지 계산한다. 파괴되는 기준선이 있으면 실행 전에 사용자에게 길고 쉽게 보고한다.

- [ ] **T9. 최종 DBMS run 최소 1회**
  T1~T8 완료 후에만 실행한다. 내부 CALL prompt 2건의 callee summary 존재와 F-C39 end-to-end를 판정한다.

- [ ] **T10. 2층·3층 및 문서 누적**
  `judge_run` 전수 축과 봉인된 DBMS 의미판정표를 갱신한다. 검증하지 않은 축은 `미검증`으로 남기고
  새 사실·실패는 11과 10에 즉시 기록한다.

## 진행 원장

| 시각 | task | 결과 |
|---|---|---|
| 2026-08-10 | 원인·방향 | framework/DBMS 소유자 계약과 spec 023 도입 이력 확인, (다) 채택 |
| 2026-08-10 | cycle research | SCC 내부 call만 제거하고 structural 보존하는 일반 알고리즘 역실험 통과 |
| 2026-08-10 | T1~T2 red | `ImportError: plan_summary_dependencies`로 신규 계약이 현행 코드에 없음을 고정. 비순환·재귀·상호재귀·external·결정성·구조 cycle fatal·context key/id 불변식 포함 |
| 2026-08-10 | T3~T5 green | 두 엔진이 `ctx.callees` 기반 공용 plan을 소비. step7의 raw CALLS·`routine_call_pairs`·두 번째 SCC 축약 경로 제거. 표적 13 OK |
| 2026-08-10 | T6 rwis | LLM/Neo4j 0회 preflight: 495/460/CALLS 5(2/3), rounds 10, cycle 0, internal 두 좌표 모두 `6→7→9` |
