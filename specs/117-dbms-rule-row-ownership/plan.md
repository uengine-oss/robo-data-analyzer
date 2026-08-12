# plan 117 — DBMS rule 의무 행 소유권

**spec**: `spec.md` · **Status**: Draft (2026-08-07)

---

## 1. 현재 흐름 → 목표 흐름

### 현재 (깨진 상태)

```
step6 build_context
  build_statement_facts(code_nodes)   → {루틴 id: (StatementFact…)}
  build_control_subtrees(code_nodes)  → {루틴 id: (ControlSubtreeNode…)}
  ctx.extra["ast_statements"] = facts.get(fn.id, ())      ← 비루틴이면 ()
  ctx.extra["control_subtree"] = subtrees[fn.id]          ← 비루틴이면 미설정

step7 batch_analyze
  _plan_rule_units → 루틴의 자식부터 하강 → units/gates (전부 비루틴)
  각 단위마다 build_control_inventory(단위 노드, 단위 ctx) → 분기 0 → rows {}
```

### 목표

```
step7 batch_analyze
  _plan_rule_units → units/gates (그대로. 입력 본문을 좁히는 역할만)
  ★ rule_row_ownership.assign_rows(routine, units|gates, inventory)
      routine 분기표 1회 생성 → 행마다 "가장 안쪽 포함 노드" 로 배분
  각 단위 = (루틴 분기표, 자기 몫 행) 으로 프롬프트·채점
```

## 2. 책임 경계

| 무엇 | 소유자 | 비고 |
|---|---|---|
| statement/control 사실 생산 | `step6/control_subtree.py` | **무변경** — 이미 루틴 키가 옳다 |
| 분기표 생성 | `quality_gates/branch_inventory.build_inventory` | **무변경** |
| 행 배분 규칙 | **신규 `step7/rule_row_ownership.py`** | 이 spec 이 도입하는 유일한 새 책임 |
| 분기표 조회 진입점 | `step7/control_prompt.py` | 소유 루틴 해소를 여기서 |
| 출력(행) 분할 | `step7/rule_row_batching.plan_batches` | **무변경** — 이미 존재 |
| 단위 선정(입력 예산) | `step7/batch_analyze._plan_rule_units` | **무변경** |

## 3. 변경 지점 (파일:행)

| # | 파일 | 변경 |
|---|---|---|
| 1 | `pipeline/steps/step7_analyze_code/rule_row_ownership.py` | **신규.** `owning_routine()` · `assign_rows()` |
| 2 | `pipeline/steps/step7_analyze_code/control_prompt.py:13` | `build_control_inventory` 옆에 `routine_inventory()` 추가 — 소유 루틴의 fn/ctx 로 위임 |
| 3 | `batch_analyze.py:598-607` (`concern == "rules"`) | 노드별 inventory → 루틴 inventory + 배분 행 |
| 4 | `batch_analyze.py:450-463` (ModelOutputError 경로) | 같은 배분 행으로 finding 좌표 생성 |
| 5 | `batch_analyze.py:779-780` (`_persist`, 채점) | 같은 루틴 inventory + 배분 행으로 grading |
| 6 | `batch_analyze.py` rule 배치 구성 | **배분 행 0개면 rule 요청을 만들지 않는다** (§3.2) |

**3·4·5 는 반드시 같은 답을 봐야 한다** — 프롬프트가 요구한 행과 채점이 기대한 행이 다르면
전부 finding 이 된다. 그래서 배분은 한 함수가 소유하고 세 곳이 그것만 부른다(헌법 III).

## 4. 영향도

| 축 | 확인 결과 |
|---|---|
| 생산자 | `build_statement_facts`/`build_control_subtrees` — 호출부 `build_context_step.py:101-104` 뿐. 변경 없음 |
| 소비자 | `build_control_inventory` 호출부 3곳(위 3·4·5) + `control_index()`. `control_index` 는 프롬프트 렌더용 — 루틴 분기표를 봐야 하므로 같이 전환 |
| framework | `framework_per_node.py` 는 `build_control_inventory` 를 별도로 부른다. 소유 루틴 = 자기 자신이므로 **결과 불변**. 회귀로 증명 |
| 그래프 | RULE/EXAMPLE 노드 생성 경로 무변경. anchor_line 은 그대로 절대 행 |
| 프롬프트 | 바이트 계약 무변경 — 증거 내용만 채워진다. `check_byte_identical.py` 28 유지 |
| 설정 | 신규 env 없음 |

## 5. Constitution Check

| 원칙 | 준수 근거 |
|---|---|
| I 결정론 우선 | 배분은 좌표 산술이다. LLM 0 |
| **II 분기 0** | strategy `if` 를 넣지 않는다. 배분 함수는 전략을 모르고, framework 는 "루틴 = 자기 자신"으로 자연 수렴 |
| **III 단일 진실** | 배분 규칙이 한 함수에만 있고 프롬프트·오류·채점 세 경로가 그것을 부른다 |
| **IV 빈 성공 금지** | 빈 의무 배치는 호출하지 않고(§3.2), 소유자 없는 행은 좌표와 함께 실패(§3.3) |
| V KV 캐시 | 프롬프트 고정부 무변경 |
| VI 스키마 절제 | 응답 스키마는 배분된 행 키로 그대로 생성 |
| VII 구조화 출력 채널 | 무변경 |
| VIII 소유권·의존 방향 | 신규 모듈은 step7 안. step6 를 역참조하지 않는다 |
| **IX 3층 검증** | spec §5. 안 잰 축은 `미검증` |
| **X 원인 추적 순서** | 원인을 원문→AST→결정론 조립 순으로 좁혀 3단계(결정론 조립)에서 확정했다. 모델·프롬프트는 건드리지 않는다 |

## 6. 마이그레이션·롤백

- 데이터 마이그레이션 없음. 매 run 은 전체 재빌드다.
- 롤백 = 커밋 되돌리기. 그래프는 다음 run 이 덮는다.

## 7. 위험

| 위험 | 완화 |
|---|---|
| 게이트/단위 범위가 겹쳐 행이 중복 배분 | "가장 안쪽 하나" 규칙 + 회계 불변식 테스트(S1·S2) |
| framework 회귀 | S5 + 전체 회귀 885 + 프롬프트 해시 |
| 배분은 됐는데 정보 부족으로 모델이 못 푼다 | 04 §4.4-3 정보 충분성 판정. `needs_context` 면 결함은 증거 생산 쪽 |
| DBMS 재실행 상한 4회 | 코드 검증(1층)과 결정론 재현을 먼저 끝내고 run 은 1회만 쓴다 |
