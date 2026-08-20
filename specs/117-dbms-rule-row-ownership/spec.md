# spec 117 — DBMS rule 의무 행 소유권: 루틴이 소유하고 배치에 배분한다

**Status**: Draft (2026-08-07)
**선행 문서**: `specs/131-cross-node-semantic-grounding/history/handovers/10-검증체계-정립-인계정본.md`
§6.C **F-C5**(원인 정정) · §17 순서 2. 09 §"DBMS 미해결 2건"에도 같은 정정을 적었다.
**헌법**: II(분기 0) · III(단일 진실) · IV(빈 성공 금지) · IX(3층 검증) · X(원인 추적 순서)

---

## 0. 한 문장

DBMS 전략에서 rule 의무 행의 소유자를 **루틴**으로 확정하고, 입력 본문을 좁히려고 하강한
배치 노드에는 **자기 줄 범위의 행만 배분**한다. 입력 크기와 의무 소유권을 분리한다.

---

## 1. 현재 결함 (관측된 사실 — 추측 아님)

### 1.1 증상

DBMS run 의 rule 프롬프트가 전부 빈 증거로 나간다. 2026-08-07 rwis run
`logs/20260807_020253_2e64d2e6bb22` 의 `code_rules_examples_batch.jsonl` 전수:

```
RULE_TARGET 블록 34개 · rule_evidence_json 이 비어있지 않은 것 0개
전부 {"conditions":{},"effects":{},"rows":{}}
응답도 전부 {"nodes":{"<key>":{"rules":{}}}}
findings 0 으로 조용히 통과 → 헌법 IV(빈 성공 금지) 위반 상태
```

대상 라벨 분포(34건): `SELECT 6 · OPEN_CURSOR 5 · CLOSE_CURSOR 5 · CALL 5 · EXCEPTION 3 ·
CURSOR_VARIABLE 3 · RETURN 2 · ASSIGNMENT 1 · UNION_ALL 1 · INSERT 1 · UPDATE 1 · COMMIT 1`.
**루틴 라벨은 0건이다.**

### 1.2 원인 — 생산자와 소비자가 다른 키를 쓴다

| 쪽 | 무엇을 키로 쓰나 | 근거 |
|---|---|---|
| 생산자 | **소유 루틴 id** | `step6_build_context/control_subtree.py:57` `build_statement_facts` 가 parent 사슬을 올라가 `ROUTINE_LABELS` 를 찾아 그 id 로 집계. `build_control_subtrees`(:11) 도 동일 |
| 소비자 | **루틴의 톱레벨 자식 노드** | `step7_analyze_code/batch_analyze.py:130-133` `_plan_rule_units` 가 `for child in eff_children[routine.id]: descend(child)` — 루틴 자신은 단위가 되지 않는다 |

그래서 모든 rule 단위가 `ctx.extra["ast_statements"] == ()` 와 `control_subtree` 부재를 받고,
`branch_inventory.build_inventory`(:712 독스트링 "``control_subtree`` 가 없으면 분기 0으로 본다")
가 계약대로 0행을 낸다.

### 1.3 최소 재현 (실행 없이 결정론 조립만으로 증명)

PROCEDURE 하나에 IF/ASSIGNMENT/RETURN 을 둔 트리:

```
build_statement_facts 키 : ['code:p.sql:p@1']        ← 루틴 하나
build_control_subtrees 키: ['code:p.sql:p@1']        ← 루틴 하나
rule 단위(units)         : ['...:IF@3', '...:RETURN@6']
루틴이 단위인가            : False

분기표 행수  PROCEDURE  code:p.sql:p@1            -> 2   ← 결정론 조립은 정상
분기표 행수  IF         code:p.sql:p@1:IF@3       -> 0
분기표 행수  RETURN     code:p.sql:p@1:RETURN@6   -> 0
```

### 1.4 폐기된 진단 (10 §6.C F-C2 ①②)

- ❌ "ontology `roles.statement` 에 `sql` 키가 없어서" — `languages.py:223 statement_labels()` 와
  `:192 control_structure_labels()` 는 **모든 언어 값의 합집합**이다. `sql` 키 유무와 무관하게
  `RETURN`/`ASSIGNMENT`/`IF` 는 이미 집합에 있다.
- ❌ "`dbms_reader` 가 statement 를 소비하지 않아서" — `dbms_reader.py:106-150 _walk_dbms` 는
  `variable`/`constant` 만 leaf 로 빼고 **나머지를 전부 CodeNode 로** 만들며,
  `node_builders.py:107-110` 이 `expression`/`target`/`operator` 를 그대로 옮긴다.

**이 두 항목은 이 spec 의 범위가 아니다.** 온톨로지 `sql` 키 추가는 이 결함과 무관하므로 하지 않는다.

---

## 2. 채택한 설계 — C2 (2026-08-07 사용자 확정)

**입력 본문 크기와 의무 소유권을 분리한다.**

```
루틴 ── 분기표를 1회 생성 (when 은 루틴 전체 경로 문맥에서 계산)
      ├─ 단위 A  ← A 줄 범위의 행만 배분 + A 범위로 좁힌 본문
      └─ 단위 B  ← B 줄 범위의 행만 배분 + B 범위로 좁힌 본문
      합계 = 루틴 분기표 전체. 잔차 0.
```

- **분기표 소유자는 루틴 하나다**(헌법 III). 노드마다 따로 만들지 않는다.
- **출력 분할은 기존 기계가 담당한다** — `rule_row_batching.plan_batches` 가 이미 한 스코프의
  행을 8행씩 나눈다. 새 분할 기계를 만들지 않는다(헌법 II).
- **노드 하강은 입력 본문을 좁히는 용도로만 남는다.** 의무를 옮기지 않는다.

### 폐기한 대안

| 대안 | 폐기 이유 |
|---|---|
| C1 — 루틴을 항상 단위로(하강 없음) | rwis 최대 341행 ≈ 3,000토큰인데 `ROBO_PIPELINE_BATCH_MAX_TOKENS` 기본 1500. 이 코퍼스는 돌지만 대형 패키지 본문에서 입력이 터진다 |
| B — 단위 노드별로 분기표 생성 | 경로 조건이 끊긴다. framework 는 `when:["C12","!C15"]` 인데 DBMS 는 `when:[]` 가 되어 **같은 축으로 채점이 불가**해진다. 완료 조건(framework 동급 정확도 증명)과 모순 |

---

## 3. 계약

### 3.1 행 배분 — 정확히 한 번 (04 §3.4)

루틴의 required row 각각은 **자기를 포함하는 가장 안쪽 단위/게이트 하나**에 배분된다.

- 포함 판정은 `unit.start_line <= row_line <= unit.end_line`.
- 여러 노드가 포함하면 **범위가 가장 좁은 노드**가 소유한다(게이트는 자손이 가져가고 남은 것만).
- 어느 단위도 포함하지 않는 행은 **소유자 없음**이며 조용히 버리지 않는다 — §3.3 참조.
- 회계 불변식: `Σ(배치별 배분 행) == 루틴 required rows`, 중복 0, 잔차 0.

### 3.2 빈 의무 배치는 LLM 을 호출하지 않는다 (헌법 IV)

배분된 행이 0개인 노드는 rule 요청을 만들지 않는다. 지금은 빈 의무로 호출이 나가고
findings 0 으로 통과한다. 호출 없음은 **정상**이며 회계에서 `0행 배정`으로 드러난다.

### 3.3 소유자 없는 행은 실패다

루틴 required row 중 어느 단위도 포함하지 않는 행이 있으면 **좌표(루틴 id·행 번호)를 담은
명시적 오류로 실패**한다. 조용한 폴백·드롭 금지(헌법 IV, no-fallback).

### 3.4 framework 무변경

framework 의 분석 대상은 루틴뿐이므로(`framework_analysis_targets`) 소유 루틴 = 자기 자신이고
배분 결과는 전체 행이다. **framework 산출물은 바이트 단위로 달라지지 않아야 한다.**

---

## 4. 시나리오

| # | 시나리오 | 기대 |
|---|---|---|
| S1 | DBMS 루틴 1개(IF+ASSIGNMENT+RETURN), 단위 = IF·RETURN | 두 단위 배분 합 == 루틴 분기표 행, 중복 0 |
| S2 | 예산 초과로 게이트가 생긴 중첩 구조 | 게이트는 자손이 안 가져간 행만, 중복 0 |
| S3 | 배분 행 0개인 노드 | rule 요청 생성 안 함(호출 0), 예외 없음 |
| S4 | 소유자 없는 행이 생기는 조작된 트리 | 좌표를 담은 `ValueError` |
| S5 | framework 루틴 | 배분 == 전체 행 (기존과 동일) |
| S6 | 경로 조건 보존 | 단위에 배분된 행의 `when` 이 루틴 문맥의 상위 조건을 포함 |

---

## 5. 완료 증거 (헌법 IX 3층)

| 층 | 증거 |
|---|---|
| 1 | 신규 계약 테스트 S1~S6 green · 전체 회귀 885+ OK · 프롬프트 28 byte-identical |
| 2 | DBMS run 에서 `judge_run.py [A]` 의무 행 **> 0** 이고 회계 잔차 0, cond/then 채점 통과율이 framework 수준. rwis gold 구조 사실 대조 |
| 3 | 주 에이전트가 DBMS 표본을 원문과 직접 대조(04 §4). **미실행 축은 `미검증`으로 보고** |

**재실행 상한**: DBMS 4회 중 4회 남음. 실행은 `--database neo4j` 로만.

---

## 6. 비범위

- 온톨로지 `roles.statement` 의 `sql` 키 추가 (§1.4 — 이 결함의 원인이 아니다)
- summary·insights 축 (정상 동작 중)
- framework 계약 변경
- 결함 A(위임형 summary) · 결함 B(테이블 설명 내부 메타) — 별도 트랙
