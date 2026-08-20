# spec 120 — callee summary 의존 소유권 정합

**Status**: Draft (2026-08-10)
**선행 문서**: `specs/131-cross-node-semantic-grounding/history/handovers/11-핵심압축-인계정본.md`
§5.1, `10-검증체계-정립-인계정본.md` F-C48
**대체 계약**: spec 023 FR-013 중 **step7 summary 분석순서를 프로시저 단위 parity로 정한다는 부분만** 대체한다.
그래프 CALLS 저장·컴포넌트·건강 지표 계약은 대체하지 않는다.
**헌법**: I · II · III · IV · VIII · IX · X

---

## 0. 한 문장

step7 summary 의존의 단일 진실을 실제 prompt 증거 수신자인 `AnalysisContext.callees`로 정하고,
구조 의존과 결합해 순환에서 제공 불가능한 callee summary만 제거함으로써 DBMS 내부 호출을
`대상 summary → CALL summary → 소유 루틴 summary` 순서로 분석한다.

## 1. 현재 결함 — 좌표로 확정된 사실

### 1.1 증상

DBMS run의 460개 summary target 전부에서 `callee_summaries_json == []`였다. 내부 호출도 예외가 아니다.

- 호출문: `PRC_DATA_ANALYSYS_TEST2.sql:227`
- CALL 노드: `...:CALL@227`
- 내부 대상: `PRC_INSERT_RDD01DD_TB_WATERSUISUL`
- 대상의 정확한 summary는 생성됐지만 CALL prompt 시점에는 아직 없었다.

### 1.2 생산 사슬

| 단계 | 확인 결과 |
|---|---|
| 원문 | A가 B를 직접 호출한다 |
| parser AST | DBMS는 호출문을 독립 `CALL` CodeNode로 보존한다 |
| CALLS 저장 | `CALL 노드 → B 루틴`이 단일 진실(spec 023) |
| context 조립 | `ctx.callees`도 CALL 노드가 소유한다 |
| step7 정렬 | `routine_call_pairs`로 `A 루틴 → B 루틴`으로 올린다 |
| 실제 prompt | 증거 수신자는 CALL인데 CALL이 round 0이라 B summary가 없다 |

현재 실측은 `B round 6 · CALL round 0 · A round 9`다. 정렬된 주체와 증거를 받는 주체가 다르다.

### 1.3 framework 대조

framework reader는 제어문 아래 `FUNCTION_CALL`도 소유 routine의 `call_sites.caller_id`로 귀속한다.
framework 분석 대상 역시 routine뿐이다. 따라서 framework에서는 정렬 주체·`ctx.callees` 소유자·
prompt target이 이미 같은 routine이다. `ctx.callees`를 DBMS routine으로 강제 승격하는 것은 공통
계약이 아니라 전략별 의미를 흐리는 변경이다.

### 1.4 역사적 원인

spec 023은 저장 진실을 proc→proc에서 CALL→대상으로 바로잡으면서, 컴포넌트·건강·분석순서의
pre-change parity를 보존하려 세 소비처 모두에 `routine_call_pairs`를 적용했다. 컴포넌트·건강은
프로시저 단위 지표라 맞지만, step7 summary 순서는 **프롬프트 증거 수신 분석 노드**의 순서라
같은 투영을 적용하면 안 된다.

## 2. User Stories

### US1 — 내부 CALL이 대상 summary를 받고 분석된다 (P1)

내부 프로시저 호출문은 호출 대상의 canonical summary가 확정된 뒤 분석되어, 이름만 보고 업무
의미를 추측하지 않는다.

**독립 검증**: `owner→CALL`, `CALL→target` 최소 그래프에서 round가
`target < CALL < owner`인지 확인하고, 실제 rwis 내부 호출 2건 모두 같은 순서인지 전수 계량한다.

### US2 — 순환에서는 없는 summary를 기다리지 않는다 (P1)

자기재귀·상호재귀는 선행 callee summary를 만들 수 없으므로 같은 summary SCC의 호출 의존만
제거한다. 그러나 부모가 자식보다 먼저 분석되어 compact가 깨지는 일은 없어야 한다.

**독립 검증**: 자기재귀 DBMS는 `CALL < owner`, 상호재귀 DBMS는 `CALL들 < routine들`,
상호재귀 framework는 두 routine이 같은 round인지 확인한다.

### US3 — 프로시저 지표와 외부 그래프는 변하지 않는다 (P2)

CALLS 저장 끝점, 컴포넌트, fan-in/out, 외부 stub, 프론트·catalog 계약은 이 변경으로 달라지지 않는다.

**독립 검증**: `routine_call_pairs` 소비처가 컴포넌트·건강 두 곳뿐인지 검색하고 기존 계약 테스트를
통과시킨다. step7에는 `routine_call_pairs`와 raw `calls_edges`를 나란히 두지 않는다.

## 3. Functional Requirements

- **FR-001**: `AnalysisContext.callees`는 현재 분석 노드가 직접 받는 callee summary 대상 id의 단일 진실이어야 한다.
- **FR-002**: framework와 DBMS step7 모두 summary 호출 의존을 `ctx.callees`에서 같은 함수로 파생해야 한다.
- **FR-003**: DBMS 구조 의존 `parent→child`는 callee 의존과 별도로 항상 보존해야 한다.
- **FR-004**: summary 의존 SCC 안에서는 제공 불가능한 **call 의존만** 제거하고 구조 의존은 유지해야 한다.
- **FR-005**: 외부 callee처럼 현재 analysis contexts에 없는 대상은 스케줄 의존으로 만들지 않아야 한다.
- **FR-006**: 비순환 DBMS 내부 호출은 `target < CALL < owner` 순서를 만족해야 한다.
- **FR-007**: 자기재귀·상호재귀에서 위상정렬 누락·교착·부모 선행이 없어야 한다.
- **FR-008**: step7에서 `routine_call_pairs`와 직접 CALL 경로를 병존시키지 않아야 한다.
- **FR-009**: `routine_call_pairs`는 프로시저 단위 소비처인 컴포넌트·건강 지표에만 유지해야 한다.
- **FR-010**: Neo4j CALLS 저장, API/stream, 외부 서비스 계약은 변경하지 않아야 한다.
- **FR-011**: summary dependency plan은 입력이 같으면 순서·cycle 표식이 같은 결정론 함수여야 한다(LLM 0).
- **FR-012**: cycle 때문에 callee summary를 제공하지 못한 노드는 cycle metadata로 표면화해야 한다.

## 4. Edge Cases

| 좌표 | 기대 |
|---|---|
| 비순환 DBMS `owner→CALL→target` | `target`, `CALL`, `owner` 순 |
| DBMS 자기재귀 `owner→CALL→owner` | call 의존 제거, 구조 의존 유지 → `CALL`, `owner` 순 |
| DBMS 상호재귀 | 같은 SCC call 의존 제거, 각 CALL들이 각 owner보다 먼저 |
| framework 상호재귀 | 같은 round, callee summary 없음(기존 계약) |
| external callee | contexts 밖이므로 의존 없음, prompt 계약이 근거 공백을 처리 |
| 같은 callee 여러 번 호출 | 호출노드마다 의존하되 정렬 결과는 결정적, 그래프 다중성 무변경 |
| 끊어진 context id | 조용히 가짜 의존을 만들지 않고 contexts 밖 대상으로 제외 |

## 5. Success Criteria

- **SC-001**: rwis internal CALL 2건 모두 `target round 6 < CALL round 7 < owner round 9`.
- **SC-002**: rwis 전체 round 수 10, cycle 0, analysis target 460이 유지된다.
- **SC-003**: 자기재귀·상호재귀 계약 테스트에서 위상정렬 잔차 0, child-before-parent 위반 0.
- **SC-004**: framework summary dependency edge 집합이 변경 전 internal CALLS와 동등하다.
- **SC-005**: 전체 analyzer 테스트, prompt 28 byte identity, parser 회귀가 통과한다.
- **SC-006**: DBMS 최종 run에서 내부 CALL prompt 2건의 `callee_summaries_json`이 비지 않는다.
- **SC-007**: 안 잰 의미 정확도 축은 3층 판정 전까지 `미검증`으로 남긴다.

## 6. 비파괴성·비범위

- 그래프 wipe, corpus 이동·덮어쓰기, 로그 삭제 없음.
- CALLS 저장 형태와 외부 서비스 계약 변경 없음.
- external callee의 의미 공백은 summary contract 보강 트랙이 소유한다.
- 자명 DBMS 노드의 LLM 제거는 §5.6 리서치 트랙이 소유한다.

