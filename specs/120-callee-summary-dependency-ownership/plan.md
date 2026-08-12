# plan 120 — callee summary 의존 소유권 정합

**spec**: `spec.md` · **research**: `research.md` · **Status**: Implemented, validation in progress (2026-08-10)

## 1. 현재 흐름 → 목표 흐름

### 현재

```text
step6 context:       CALL.ctx.callees = [target]
step7 DBMS schedule: routine_call_pairs → owner → target
prompt receiver:     CALL

결과: CALL이 target보다 먼저 실행되어 callee_summaries=[]
```

### 목표

```text
summary_dependency_plan(contexts, structural_edges)
  call edges       = ctx.id → ctx.callees
  structural edges = parent → child
  combined SCC     = summary를 선행 제공할 수 없는 순환
  schedule edges   = structural 전부 + SCC 밖 call edges

비순환: target → CALL → owner
순환:   SCC 안 call-summary 의존 없음, child → owner 구조 순서 유지
```

## 2. 책임 경계

| 책임 | 소유자 |
|---|---|
| CALLS 저장·internal/external 해소 | 기존 link_calls 모듈, 무변경 |
| context가 받는 callee id | `build_context_step`, 무변경 |
| **summary 의존 call/structure/cycle 계획** | `analysis_order.py` 단일 함수 |
| framework dataflow 소비 | `framework_per_node.py` |
| DBMS batch dataflow 소비 | `batch_analyze.py` |
| 프로시저 단위 component/health | `routine_call_pairs`, 무변경 |

## 3. 구현 슬라이스

1. red 계약 테스트로 비순환 DBMS·자기재귀·상호재귀·framework cycle을 고정한다.
2. `analysis_order.py`에 typed summary dependency plan을 추가한다.
3. framework engine의 raw `calls_edges`/별도 cycle 조립을 plan 소비로 교체한다.
4. DBMS engine의 `routine_call_pairs` schedule 소비를 plan으로 교체한다.
5. 죽은 import와 낡은 docstring/spec 023 소비처 표를 현행 계약 문서에서 정정한다.
6. rwis 전수 오프라인 계측을 상설 테스트/도구에 고정한다.

## 3.1 영향도 상시 게이트

| 시점 | 측정 |
|---|---|
| 변경 전 | `ctx.callees` 생산자 1곳, prompt 소비자 3곳, schedule 소비자 2곳, `routine_call_pairs` 소비자 전수 |
| 설계 선택 | 기존 두 engine별 조립 대비 공통 plan의 경로·분기·맵 수, cycle/external/containment 경계 |
| 구현 후 | framework call dependency 집합 동등, DBMS 460 target·10 rounds·2 internal 좌표 변화, component/health 무변경 |
| 실행 후 | internal CALL prompt callee summary, calls/input/output/latency, RULE/graph/의미 축 delta |
| 종료 감사 | 옛 step7 raw `calls_edges`/`routine_call_pairs` 참조 0, 중복 summary dependency 경로 0, 사용자 WIP 보존 |

기존 배선을 보존하는 것이 목표가 아니다. 공통 plan이 두 engine의 중복 조립을 실제로 제거하지
못하거나 더 많은 분기·맵을 요구하면 구현을 멈추고 책임 경계를 다시 설계한다.

## 4. Constitution Check

| 원칙 | 준수 |
|---|---|
| I 결정론 우선 | graph/SCC/Kahn만 사용, LLM 0 |
| II 분기 0 | strategy 이름 분기 없음. 두 engine이 같은 plan을 소비 |
| III 단일 진실 | prompt receiver인 `ctx.callees`가 schedule call edge의 유일한 입력 |
| IV silent fail 금지 | bottom-up 잔차는 기존 오류 표면화 유지, cycle은 metadata로 보존 |
| V~VII | prompt body·schema·channel 무변경 |
| VIII 소유권 | summary ordering은 step7 `analysis_order.py`; routine graph는 linker에 유지 |
| IX 3층 | tasks에 1·2·3층과 미검증 분리 |
| X 원인 순서 | source→AST→context→실제 prompt에서 결정론 조립 결함으로 확정 |

Gate: PASS. 새 전략 분기·외부 계약·저장 마이그레이션 없음.

## 5. 회귀·경계

- external callee는 contexts 밖이라 dependency 없음.
- cycle 내부 call summary는 제공 불가가 정답이며 이름 추측 금지 계약은 별도 summary contract가 처리한다.
- parent→child는 어떤 cycle에서도 제거하지 않는다.
- framework acyclic call edge 집합은 기존과 동등해야 한다.
- DBMS component/health의 routine graph는 바뀌지 않는다.

## 6. 검증 계획

1. 신규 contract tests red→green
2. 표적 step7 tests
3. analyzer 전체 914+·prompt 28
4. parser 135 회귀(소스 무변경 확인용)
5. `evidence_preflight.py measure`
6. rwis offline round 전수: SC-001~004
7. 최종 DBMS run 전 보존 부작용 계산, 이후 최소 1회
8. `judge_run` 2층과 봉인본 3층 판정. 안 잰 축은 `미검증`

## 7. 비파괴성

코드와 문서만 국소 변경한다. 데이터·그래프·logs는 구현 단계에서 건드리지 않는다. 최종 run은
기존 허가 범위에서 preflight와 보존 점검 뒤 수행한다.
