# Phase 0 Research: DBMS CALL 노드 → 호출 대상 링킹

전수조사(코드 직접 대조)로 확정한 사실과 설계 결정. 추측 0.

## R1. 전략별 호출 링킹 라우팅

- **Decision**: 변경은 DBMS 경로(`link_calls_by_ast` → `CallLinker`)에만 국한한다.
- **Rationale**: `analysis_pipelines.py:94`는 FRAMEWORK 전용 `link_calls_by_name_step`(`call_classifier.py`, 함수↔함수 직접), `:129`는 DBMS 전용 `link_calls_by_ast_step`(`CallLinker`). 별개 파일·별개 파이프라인 분기. FRAMEWORK은 함수 아래로 안 내려가 CALL 노드 자체가 없다.
- **Alternatives**: 공통 링커로 통합 → 기각(전략 의미가 달라 분기 0 원칙 위배·불필요).

## R2. CALL 노드의 존재·저장·부모 연결

- **Decision**: CALL 노드는 이미 그래프 노드로 저장되고 PARENT_OF로 소유 루틴에 연결됨을 전제로 한다(신규 노드/엣지 생성 불필요).
- **Rationale**: `load_ast_step.py:99` `upsert_code_nodes`가 전 code_nodes(구문 포함) 저장, `:106` `upsert_parent_of`가 트리 저장. `CodeNode.own_ast_type()=="CALL"`로 CALL 식별(`call_linker.py:44`). DBMS id = `{parent}:{TYPE}:{line}` 구조라 부모 체인 추적 가능.
- **Alternatives**: 링킹 시 CALL 노드를 새로 만들기 → 기각(이미 존재).

## R3. 현재 결함의 정확한 위치

- **Decision**: `CallLinker.link()`가 `caller_id = _root_of(fn)`(`call_linker.py:50`)로 출발점을 프로시저 루트로 끌어올리는 것이 결함. 출발점을 `fn.id`(CALL 노드)로 바꾼다.
- **Rationale**: 현재 internal/external 모두 caller=루트루틴 → proc→proc 엣지. CALL 노드는 대상과 안 이어짐. self-loop drop(`:54`)·`(caller_root, target)` dedup(`:56`,`:68`)도 proc 단위 전제라 제거 대상.
- **Note**: external 분기의 스키마 폴백(`:72` 호출자 자기 스키마 = 루트의 package)은 유지 → `_root_of`는 external 경로에서 계속 필요.

## R4. stdlib/빌트인 필터 — 이미 동작

- **Decision**: 변경하지 않는다.
- **Rationale**: `call_linker.py:23` `_BUILTIN_PREFIXES`(`DBMS_`/`UTL_`/`SYS_`/`ORA_`/`OWA_`/`STANDARD`), `:47`에서 드롭. FRAMEWORK은 `call_classifier.py:201` `_stdlib_set`. 양쪽 이미 필터링.

## R5. calls_edges 소비처 전수 (회귀 blast radius)

`grep calls_edges|find_call_cycles|caller_id` 전수 결과, `(caller_id, callee_id)`를 proc→proc로 가정하는 소비처:

| 소비처 | 위치 | 전략 | 영향 |
|---|---|---|---|
| `_cluster`(Leiden 군집) | build_components_step.py:138 | 공통 | ★수정 — caller가 CALL 노드면 탈락→컴포넌트 0 |
| `_compute_metrics`(fan/cycle) | health_metrics_step.py:112 | 공통 | ★수정 — fan_out 0·fan_in 의미왜곡 |
| DBMS 바텀업 정렬 | batch_analyze.py:82-84 | DBMS | ★수정 — CALL 노드가 analysis_ids 밖이면 호출 의존 정렬 소실 |
| per-node 바텀업 정렬 | analyze_code_step.py:145 | **Framework 전용** | 무영향 — DBMS는 137행 early-return, Framework calls는 함수↔함수 그대로 |

- **Decision**: 위 ★3곳을 단일 해소기 `routine_call_pairs`로 교체. analyze_code_step.py per-node 경로는 무변경.
- **Rationale**: 전수 확인으로 5개 후보 중 3개만 DBMS CALL→target 엣지를 본다.

## R6. parity 보장 메커니즘 (핵심)

- **Decision**: `routine_call_pairs(code_nodes, calls)` = internal Calls마다 `(_root_of(caller=CALL노드), callee)`, dedup, self(root==callee) 제외.
- **Rationale**: 옛 CallLinker도 caller=`_root_of(CALL)`, callee=대상 루트, self drop, dedup이었다. → **두 집합이 동일**. cycle(Tarjan)·ordering(Kahn)·Leiden·fan은 동일 입력 그래프를 받아 결과 동등.
- **세부 검증**:
  - health `_compute_metrics`는 pre-change에 self·dedup 필터가 없었지만, 옛 calls에 self 엣지가 애초 없고(드롭됨) 중복도 없어(dedup됨) 동일. fan은 set 기반이라 중복 무관.
  - build_components vertices = 전 code_nodes id 유지(구문은 고립 singleton → MIN_COMPONENT_SIZE 필터). 엣지만 routine_call_pairs로 교체 → Leiden 입력 동일.
  - find_call_cycles는 `caller in nodes and callee in nodes`로 자체 가드 → 안전.

## R7. 해소기 위치 (단일 진실·응집)

- **Decision**: `routine_call_pairs`를 `call_linker.py`에 public으로 신설(`_root_of` 재사용).
- **Rationale**: 호출 그래프 토폴로지 권위 모듈. 헌법 III(단일 진실)·VIII(응집). 신규 모듈·중복 0. step6/step7가 step4에서 import(이미 step간 import 관례 — health가 step6 call_cycles import).
- **Alternatives**: `shared/lang/` 새 모듈 → 기각(`_root_of`와 분리되어 DRY 약화).

## R8. silent fail 표면화

- **Decision**: `routine_call_pairs`에서 소유 루틴 해소 실패(부모 체인 끊김 등)·callee 부재 건수를 호출 측이 로그/집계할 수 있게 한다(드롭하되 침묵 금지).
- **Rationale**: 헌법 IV. 빈 결과를 성공처럼 반환하던 과거 사고 방지.

## Cross-Service Impact

- **CALLS 끝점 의미 변경** = ↓출력 계약(robo-architect/프론트) 변경. **프론트 무변경(라이브 검증)**: 보기모드는 끝점을 소유 모듈·루틴으로 해소(resolveModule/resolveRoutine)해 강건, 일반 탐색기는 원본 표시가 정상(CALL 노드 노출 = 의도). catalog·antlr 입력은 무영향(R1·R2 근거).
