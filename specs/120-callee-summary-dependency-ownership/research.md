# research 120 — callee summary 의존 소유권

## R1. framework 소유자 계약

`framework_reader._walk_framework`는 구조 부모와 소유 routine을 별도로 전달하며,
`_collect_internal`은 `caller_id=owner_routine_id`를 기록한다. `call_classifier`는 이 id에서
함수→함수 CALLS를 만든다. framework 분석 대상도 `ROUTINE_LABELS`뿐이다.

**결론**: framework는 context 소유자와 prompt target이 동일하다.

## R2. DBMS 소유자 계약

`dbms_reader`는 CALL을 독립 CodeNode로 보존하고 `CallLinker`는 CALL→대상 엣지를 만든다.
`build_context_step`은 저장 엣지의 caller id에 `ctx.callees`를 배치한다. DBMS 분석 대상에는
CALL이 포함된다.

**결론**: DBMS에서 CALL이 callee summary의 직접 수신자다.

## R3. spec 023 도입 이력

commit `3c47ed16`은 CALLS 저장을 CALL→대상으로 정정하면서 step7을 direct edge에서
`routine_call_pairs`로 바꿨다. 목적은 pre-change proc→proc 순서 parity였다. 이때 prompt
evidence receiver와 schedule receiver가 다르다는 축은 검증하지 않았다.

**결론**: spec 023 FR-013은 summary 입력 계약에 한해 새 사실로 대체돼야 한다.

## R4. 폐기 대안

| 대안 | 판정 |
|---|---|
| (가) DBMS `ctx.callees`를 routine으로 이동 | CALL 노드가 계속 target summary를 못 받고 공통 필드 의미가 흐려져 폐기 |
| (나) routine pair 옆에 raw CALL 경로 추가 | 동일 사실의 두 경로·이전 세션에서 되돌린 땜빵이라 폐기 |
| raw CALL로 단순 한 줄 교체 | 비순환은 맞지만 자기재귀에서 구조 SCC가 부모/자식을 같은 round로 묶어 폐기 |
| **채택: typed summary dependency plan** | context call 의존과 구조 의존을 한 곳에서 계획하고 SCC 내부 call만 제거 |

## R5. 순환 알고리즘 역실험

1. `call_edges = {(ctx.id, callee) | callee in contexts}`
2. `structural_edges = parent→child`
3. 합친 그래프의 SCC를 구한다.
4. 같은 SCC 안의 call edge만 제거한다. structural edge는 유지한다.
5. 남은 DAG를 bottom-up 정렬한다.

역실험 결과:

| 그래프 | 결과 rounds |
|---|---|
| 비순환 DBMS | `[target] → [CALL] → [owner]` |
| 자기재귀 DBMS | `[CALL] → [owner]` |
| 상호재귀 DBMS | `[CALL_A,CALL_B] → [A,B]` |
| 상호재귀 framework | `[A,B]` |

## R6. rwis 전수 역실험

- CodeNode 495, analysis target 460
- CALLS 5: internal 2, external 3
- parent dependency 451
- 현행 internal 두 건: target 6, CALL 0, owner 9
- 채택안: target 6, CALL 7, owner 9
- rounds 10, cycles 0 불변

## R7. 영향도

- 변경: step7 summary dependency 계획과 두 analyzer engine의 소비 방식
- 유지: CallLinker, graph CALLS writer, `routine_call_pairs`, components, health, API, stream
- prompt 텍스트/스키마: 무변경. prompt에 들어가는 callee summary 값의 적시성만 정정
- cross-service: 없음

