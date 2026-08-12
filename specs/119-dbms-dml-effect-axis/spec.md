# spec 119 — DBMS DML 효과 축: 규칙의 `then` 이 비어 있지 않게 한다

**Status**: Draft (2026-08-08)
**선행**: spec 118(분기 커버리지)이 의무 행을 25 → 59 로 회수한 뒤 드러난 다음 결함.
`docs/handovers/analysis-performance-validation-canonical/10-…-인계정본.md` §6.C-5.
**헌법**: I(결정론 우선) · II(분기 0) · III(단일 진실) · IV(빈 성공 금지) · IX · X

---

## 0. 한 문장

DBMS 규칙의 **효과(`then`)** 가 거의 비어 있다. 이미 결정론으로 계산된 "이 줄이 어느 테이블을
어떤 DML 로 건드리는가"를 rule 증거까지 **이어 주기만** 한다 — 새 판정 기계를 만들지 않는다.

---

## 1. 현재 결함 (실측)

| 축 | 직접 효과(`direct`)가 있는 의무 행 |
|---|---|
| framework (run `20260807_020513`, 저장 프롬프트 전수) | **3,398 / 4,100 (83%)** — entry 372/372 · exit 475/475 · decision 1,960/2,538 · complement 591/648 · handler 0/67 |
| **DBMS (rwis, spec 118 수리 후 오프라인 실측)** | **19 / 59 (32%)** |

원인: 효과 축이 `roles.statement` 만 본다. rwis 에서 그것은 `ASSIGNMENT 19 · RETURN 1` 뿐이고,
실제 업무 효과인 **`SELECT 155 · CALL 59 · INSERT 38 · COMMIT 30 · UPDATE 26 · MERGE 24 ·
FETCH 5 · OPEN_CURSOR 5 · CLOSE_CURSOR 5` (합 347개)** 는 효과 축에 존재하지 않는다.
framework 는 대입 + `FUNCTION_CALL` 좌표로 효과를 채우는데, `dbms_reader` 는 `CALL` 을
call_site 가 아니라 일반 CodeNode 로 만들어 그 경로마저 비어 있다.

**결과**: "조건은 맞는데 무엇을 하는지 비어 있는 규칙"이 대량 생산된다. 완료 기준
("framework 동급 정확도")과 정면으로 어긋난다.

**이것이 우연한 누락이 아니라는 근거**: `pipeline/models/rule_expression.py:20`
`EFFECT_FUNCTIONS` 에 `select, insert, update, delete, upsert` 가 **이미 선언돼 있다.**
DBMS DML 을 효과로 쓰도록 설계돼 있고 배선만 없다.

---

## 2. 채택한 설계 — 이미 있는 사실을 잇는다

`step5_linking/link_tables/link_dbms_tables_step.py` 는 **모든 CodeNode(=DML 노드 포함)에
대해 자기 줄을 스캔**해 `ReadsWrites(source_id=DML 노드 id, target_id=테이블,
dml_type=구문 종류)` 를 이미 만든다. 좌표는 그 노드의 `start_line` 이다.
즉 **"몇 번째 줄이 어떤 테이블을 어떤 DML 로 건드리는가"는 이미 결정론으로 존재한다.**

framework 의 `ast_call_lines` 와 **같은 경로**로 잇는다(새 기계 0 — 헌법 II).

```
step5 ReadsWrites(DML 노드, 테이블, dml_type)
  → step6 ctx.extra["ast_table_ops"] = ((루틴 상대 줄, op, 테이블), …)
  → branch_inventory._ast_line_facts 가 소유 행에 귀속
  → rule_prompt_evidence 가 insert("T") / select("T") 보기로 노출
  → 모델은 고를 뿐 창작하지 않는다
```

### 2.1 `dml_type` → 효과 op 는 **데이터**다

`pipeline/ontology_roles.yaml` 의 `dml_effect_ops` 가 단일 정의처다. 미선언 `dml_type` 은
**조용히 버리지 않고 좌표와 함께 실패**한다(헌법 IV) — 새 DML 지원은 데이터 1줄이다.

### 2.2 `CALL` 은 이 spec 의 범위가 아니다

DBMS `CALL` 노드를 callee 축에 잇는 것은 R/W 가 아니라 호출 그래프 문제다.
같은 슬라이스에 섞지 않는다(§6).

---

## 3. 계약

1. `ast_table_ops` 는 **루틴 상대 1-based 줄** + op + 테이블명이며 좌표 계약은
   `ast_call_lines` 와 동일하다.
2. 한 행이 같은 (op, 테이블) 을 두 번 갖지 않는다(중복 0).
3. 소유 귀속은 기존 `_ast_line_facts` 규칙 그대로다 — 자기 범위, 자식 범위 제외.
4. **framework 산출물은 한 글자도 달라지지 않는다.** framework 경로는 `ast_table_ops` 를
   제공하지 않으며 기본값은 빈 튜플이다.
5. 미선언 `dml_type` 은 좌표(파일·줄·타입)를 담은 명시적 실패다.
6. 효과 보기는 **결정론 증거일 뿐** 채점 통과를 보장하지 않는다. 권위 table op 계약
   (F-D2: 테이블의 권위 op 는 관계가 결정한다)과 충돌하는지 오프라인으로 먼저 잰다.

---

## 4. 시나리오

| # | 시나리오 | 기대 |
|---|---|---|
| S1 | 분기 안 `INSERT INTO T` | 그 분기 행의 `direct` 에 `insert("T")` 보기 |
| S2 | 분기 밖 최상위 `INSERT` | entry 행이 소유 |
| S3 | 같은 줄이 두 테이블을 읽음 | 두 보기, 중복 0 |
| S4 | 자식 범위의 DML | 부모 행이 아니라 자식 행이 소유 |
| S5 | framework 경로 | `ast_table_ops` 미제공 → 증거 **완전 동일** |
| S6 | 미선언 `dml_type` | 좌표를 담은 `ValueError` |

---

## 5. 완료 증거

| 층 | 증거 |
|---|---|
| 1 | S1~S6 green · 전체 회귀 907+ OK · 28 byte-identical |
| 2 | 오프라인: DBMS `direct` 보유율 **19/59(32%) → framework 수준**, framework 증거 대조 차이 불변(spec 118 의 1행 외 0). 이어 DBMS run 1회에서 채점 통과율·회계 잔차 |
| 3 | 주 에이전트 직접 의미 판정. **미실행 축은 `미검증`** |

---

## 6. 비범위

- DBMS `CALL` 노드 → callee/CALLS 축 (별도 트랙)
- `COMMIT`/`FETCH`/`OPEN_CURSOR`/`CLOSE_CURSOR` 를 효과로 볼지 — 테이블 대상이 없어
  현 어휘(`op("테이블")`)에 담기지 않는다. 별도 판단이 필요하다
- framework 효과 축 변경
