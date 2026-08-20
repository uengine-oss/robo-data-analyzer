# spec 118 — 조건 없는 행의 역할과 PL/SQL 분기 커버리지

**Status**: Draft (2026-08-08)
**선행 문서**: `specs/131-cross-node-semantic-grounding/history/handovers/10-검증체계-정립-인계정본.md`
§6.C-4 **F-C7 · F-C8 · F-C9** · §17 순서 **2-b**. spec 117(의무 행 소유권)의 후속 트랙이며
117 의 범위(소유권 배선)와 섞지 않는다.
**헌법**: II(분기 0) · III(단일 진실) · IV(빈 성공 금지) · IX(3층 검증) · X(원인 추적 순서)

---

## 0. 한 문장

**조건이 실제로 없는 행에는 조건을 요구하지 않는다**(언어 중립 규칙 1개)로 고치고,
PL/SQL 고유 철자(`ELSIF`·`EXCEPTION`·`EXIT`)를 온톨로지에 편입해 덜 생성되던 의무를 회수한다.

---

## 1. 현재 결함 (관측된 사실 — 추측 아님)

### 1.1 결함 ①: 조건 없는 제어 노드를 `decision` 으로 강제한다 (F-C9)

`control_roles.decision` 에 `LOOP` 이 있어 조건 유무와 무관하게 decision 행이 된다.
`rule_grading.py:550` 은 decision 행에 `cond` 가 null 이면 거절한다. 조건이 증거에 **없으므로**
모델이 고를 수 있는 답이 없다 — 04 §4.4-3 정보 충분성 판정으로 `needs_context` 이고
결함은 증거 생산(2·3단계)에 있다. **모델 잘못이 아니다.**

**언어 중립 결함이다.** 두 전략에서 같은 원인으로 발현한다.

| 전략 | 좌표 | 원문 | findings |
|---|---|---|---|
| dbms | `PRC_DATA_ANALYSYS_TEST2.sql:131`·`PRC_INSERT_WATERSISUL_MINUTE_2.sql:111`·`PRC_4RVR_SUPPLY_CALC_debug.sql:78,140,187` | `LOOP`(커서 루프, 탈출은 `EXIT WHEN`) | 5 |
| framework | `zapamcom10200.c:1948` (`c700_get_link_banner_info`) | `for(;;)`(탈출은 `if (rc == RC_NFD) break;`) | 1 |

근거: run `20260807_085619_d4e5633b96bb` · `20260807_020513_7ca69135e7ba` 의
`analysis-findings.jsonl` 전수. 두 run 의 findings 중 이 사유가 각각 5/5 · 1/14.

### 1.2 결함 ②: 같은 계약 안에서 채점기와 테스트가 서로 모순이다

`tests/pipeline/quality_gates/test_branch_inventory_ast.py:142-184` 는 Java `FINALLY` 를
**"조건 없는 decision"** 으로 못박고 `when: []` 을 단언한다(회귀 5호 계약).
그런데 `rule_grading.py:550` 은 decision 행에 cond 를 요구한다.
**Java 코퍼스가 들어오는 순간 모든 FINALLY 가 finding 이 된다.** 현재 코퍼스에 Java 가
없어 드러나지 않았을 뿐이다.

### 1.3 결함 ③: 온톨로지가 PL/SQL 고유 철자를 분류하지 않는다 (F-C7)

`control_structure_labels()`·`statement_labels()` 는 **모든 언어 값의 합집합**이므로
`IF`/`ELSE`/`LOOP`/`TRY`/`RETURN`/`ASSIGNMENT` 는 `sql` 키가 없어도 이미 집합에 있다.
그러나 다음 3종은 **어느 언어에도 선언돼 있지 않아 통째로 누락**된다.

| 라벨 | rwis 계수 | 원문에서의 정체 (직접 대조) | 조건 보존 |
|---|---:|---|---|
| `EXCEPTION` | 28 | `BEGIN … EXCEPTION WHEN OTHERS THEN … END` 의 **핸들러 섹션**. framework `TRY`/`CATCH` 쌍의 PL/SQL 철자다 (`PRC_CALC_RDD01DD_B2EN2_TEST.sql:28-80` 대조) | ✗ (`WHEN OTHERS` 미emit) |
| `ELSIF` | 3 | `ELSIF <조건> THEN` — **조건이 AST 에 정상 보존**된다 (`IN_DATAKIND IN ('15')` 등) | ✓ |
| `EXIT` | 5 | `EXIT WHEN <조건>;` — 커서 루프의 실제 탈출 조건 | ✗ (F-C8, parser 결함) |

참고: `TRY` 26 은 PL/SQL 의 `BEGIN…END` 블록이며 이미 `controller` 로 분류돼 있다(정상).

### 1.4 결함 ④: parser 가 `EXIT WHEN` 조건을 버린다 (F-C8)

`antlr-code-parser` `PlSqlAstListener.java:556-558 enterExit_statement` 가 `expression` 을
채우지 않는다. 바로 위 `enterElsif_part`(:382-386)는 **"조건식 원문 보존(spec 016 FR-003)"**
주석과 함께 채운다 — **같은 계약을 한쪽만 지켰다.** rwis 의 EXIT 5건이 전부
`EXIT WHEN C_xxx%NOTFOUND` 인데 AST `expression` 이 전부 빈값이다.

### 1.5 결함 ⑤: 평면 `ELSIF` 사슬의 경로 조건이 끊긴다

PL/SQL parser 는 `ELSIF`/`ELSE` 를 `IF` 의 **형제가 아니라 자식(평면)** 으로 낸다.
C 는 `else { if … }` **중첩**이라 조상 사슬로 자동 처리되지만, 평면 사슬에서는
`_paired_decision`(`branch_inventory.py:300-324`)이 구조 부모 `IF` 를 즉시 소유자로
돌려주므로 **`ELSE` 가 선행 `ELSIF` 조건들을 부정하지 못한다.**

실측 좌표: `FN_EDIT_YN.sql:35(IF) / 41(ELSIF) / 45(ELSE)` — `ELSE` 의 참 조건은
`not(IN_DATAKIND IN ('60')) and not(IN_DATAKIND IN ('15'))` 인데 현재 계약으로는
`not(IN_DATAKIND IN ('60'))` 만 나온다. 같은 파일 `48(IF)/50(ELSIF)/52(ELSIF)` 도 동일.

**결함 ③만 고치고 ⑤를 두면 경로 조건이 틀린 규칙이 새로 생긴다.** 같은 슬라이스에서 고친다.

---

## 2. 채택한 설계

### 2.1 조건 없는 행 → `controller` (2026-08-08 사용자 확정)

`control_roles` 는 **의미 축**만 선언한다(decision = 조건으로 갈리는 자리,
controller = 감싸는 자리, complement = 여집합). 실제 조건 유무는 데이터가 아니라 노드의
사실이므로, `Branch.prompt_role()` 이 **언어별 분기 없이 규칙 하나**를 적용한다.

```
조건이 비어 있는 decision 노드 → controller
  단, CASE 의 caseless 라벨(default)은 문법상 여집합이므로 기존대로 complement 가 먼저다.
```

controller 행은 cond·then 을 요구하지 않고 `when` 은 조상 경로 그대로다 — 조건 없는
decision 이 지금 받는 것과 **동일한 증거**이며, 달라지는 것은 채점기가 없는 조건을
요구하지 않는다는 점뿐이다.

**폐기한 대안**

| 대안 | 폐기 이유 |
|---|---|
| 채점기 완화(`decision` 유지 + cond null 허용) | framework 4,100행의 decision 채점까지 같은 규칙을 공유하게 되어 진짜 누락을 놓친다 |
| `handler` 역할 신설 | prompt_role·채점기·증거 렌더에 새 축을 배선해야 하고, 현재 얻는 행 증거는 controller 와 동일하다 |
| LOOP 만 특수 처리 | 같은 결함이 `for(;;)`·`FINALLY`·`EXCEPTION` 에도 있다. 언어별 특례는 헌법 II 위반 |

### 2.2 온톨로지 편입은 **관측된 라벨만**

rwis AST 전수 조사에서 실재를 확인한 `ELSIF`·`EXCEPTION`·`EXIT` 만 선언한다.
PL/SQL 문법에 있으나 이 코퍼스에서 parser emit 을 확인하지 못한 라벨(`CASE` 문 등)은
**추측으로 넣지 않는다**(10 §17 순서 2 지시).

`EXCEPTION` 은 framework `CATCH` 와 같은 의미 축이므로 `control_roles.decision` 에 둔다.
오늘은 조건이 없어 §2.1 규칙이 controller 로 내리고, 훗날 parser 가 `WHEN <예외>` 를
`expression` 으로 내보내면 **선언을 고치지 않고** 자동으로 decision 이 된다.

### 2.3 평면 여집합 사슬

`complement_owner_types` 의 소유자 판정은 **구조 부모 우선**을 유지하되, 소유자와 자기 사이의
**선행 형제 decision 들을 함께 부정**한다. 중첩(C) 형상은 조상 사슬이 이미 담당하므로
동작이 달라지지 않는다.

---

## 3. 계약

1. `Branch.prompt_role()` 은 조건이 빈 `decision` 을 `controller` 로 돌려준다. CASE default →
   complement 판정이 먼저다.
2. `roles.control_structure.sql` 은 rwis AST 에서 실측한 `[IF, ELSE, ELSIF, LOOP, TRY, EXCEPTION]`
   을 선언한다.
3. `control_roles.decision` 에 `ELSIF`·`EXCEPTION` 을 더한다. `FINALLY` 선언은 유지하되
   주석의 "조건 없는 decision" 서술을 §2.1 규칙으로 고친다.
4. parser `enterExit_statement` 가 `EXIT WHEN <조건>` 의 조건식 원문을 `expression` 에
   보존한다(spec 016 FR-003, `enterElsif_part` 와 같은 계약).
5. 조건을 확보한 `EXIT` 는 `control_roles.decision` 이며 `roles.control_structure.sql` 에
   포함된다. 조건 없는 `EXIT`(무조건 탈출)는 §2.1 규칙으로 controller 가 된다.
6. 평면 사슬의 complement 행 `when` 은 소유자 조건과 **선행 형제 decision 조건 전부**의
   부정을 포함한다.
7. **framework 산출물 변화는 §1.1 의 1행(`zapamcom10200.c:1948`)뿐이어야 한다.** 그 외
   행이 달라지면 회귀다.

---

## 4. 시나리오

| # | 시나리오 | 기대 |
|---|---|---|
| S1 | sql bare `LOOP`(expression 없음) | 행 role = `controller`, cond 요구 없음 |
| S2 | c `for(;;)`(expression 없음) | 행 role = `controller` — 언어 중립 |
| S3 | java `FINALLY` | 행 role = `controller`, `when: []` 유지(회귀 5호 증거 보존) |
| S4 | 조건 있는 `LOOP`(`while (i < n)`) | 행 role = `decision` — 기존 동작 불변 |
| S5 | CASE caseless(default) | 행 role = `complement` — 기존 동작 불변(§2.1 보다 우선) |
| S6 | sql `EXCEPTION` 핸들러 | 행이 **생성**되고 role = `controller` |
| S7 | sql `ELSIF`(조건 보존) | 행이 생성되고 role = `decision`, cond = 자기 조건 |
| S8 | sql `IF/ELSIF/ELSE` 평면 사슬 | `ELSE` 의 `when` 이 IF 와 **모든 선행 ELSIF** 를 부정 |
| S9 | c `if/else if/else` 중첩 사슬 | 기존 `when` 과 **완전 동일**(회귀 없음) |
| S10 | parser `EXIT WHEN c%NOTFOUND` | AST `expression == "c%NOTFOUND"` |
| S11 | parser 무조건 `EXIT` | `expression == ""`(빈 값이 사실) |

---

## 5. 완료 증거 (헌법 IX 3층)

| 층 | 증거 |
|---|---|
| 1 | S1~S11 green · 전체 회귀 897+ OK · 프롬프트 28 byte-identical · parser `mvnw test` |
| 2 | **오프라인 결정론 하네스**로 의무 행 전후 대조(run 예산 0) → DBMS run 1회에서 `judge_run [A]` 실측, 회계 잔차 0, `cond null` finding **0**, rwis gold 구조 사실 대조 |
| 3 | 주 에이전트가 DBMS 표본을 원문과 직접 대조(04 §4). **미실행 축은 `미검증`** |

**재실행 상한**: DBMS 4회 중 **3회 남음**(1회는 spec 117 T9 가 소진). `--database neo4j` 로만.
framework 는 2회 중 2회 남았으나 이 spec 은 framework 산출물을 1행만 바꾸므로
결함 A·B(별도 트랙)와 **같은 cold 에 묶어 1회로** 처리한다.

---

## 6. 비범위

- rule 의무 행 소유권 (spec 117 — 이미 닫힘)
- 결함 A(위임형 summary) · 결함 B(`analyzed_description` 이 `description` 복사본) — 별도 트랙
- PL/SQL `CASE` 문 등 이 코퍼스에서 emit 을 확인하지 못한 라벨
- `EXCEPTION` 의 `WHEN <예외>` 조건 emit (parser 개선 후보로만 기록)
