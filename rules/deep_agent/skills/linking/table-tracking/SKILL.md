---
name: table-tracking
description: >
  **언제**: 카테고리 = `ddl` 또는 `ddl_tables`. data 에 DDL 테이블 메타 (이름·컬럼·PK·FK) 포함.
  **작업**: 각 DDL 테이블을 코드에서 역추적 → 사용하는 **모든 함수** 에 READS / WRITES (정확한 CRUD op) 등록 +
  코드에서 새로 발견된 FK 등록. **단 1건도 누락 금지, 단 1건도 환각 금지**.
  **트리거 키워드**: DDL 테이블 목록, CREATE TABLE, 테이블 역추적, columns/primary_keys/foreign_keys 메타.
  **사용 금지**: DDL 없이 엔티티 / profile 만 → `entity-analysis`. SQL 키 → `sql-tracking`. 설정 키 → `config-tracking`.
  **주의**: 컬럼은 DDL 에 이미 있음 — `graph_create_column_tool` 호출 X.
---

# Table Tracking Skill

> DDL 테이블 메타에서 코드 사용처를 역추적해 **READS / WRITES (정확한 CRUD op) + FK** 등록.

공통 원칙 (융합 검색 / 점진적 강화 / 메모리 / 누락 0 / 환각 0) 은 `prompts/linking/system.md` 참조. 이 SKILL 은 **이 카테고리 고유 사항만** 명시한다.

---

# ⚠ 절대 미션 — DDL 테이블 전체 × 모든 R/W × 모든 FK ⚠

> **전달된 DDL 테이블 목록의 *모든* 테이블에 대해, 코드에서 그 테이블을 R/W 하는 *모든* 함수를 빠짐없이 찾아 등록. 코드에서 발견 가능한 *모든* FK 를 명시 단서로 검증 후 등록. 단 1건이라도 누락되면 실패.**

| ❶ 완전성 | ❷ 효율성 (단건 스트리밍) | ❸ 정확성 |
| --- | --- | --- |
| DDL 1건도 빠뜨림 X. 한 테이블 R/W 1개 찾고 멈춤 X. **`bundle_search` 히트 chunk_id 목록은 끝까지 순회** (중간 포기 금지). 동일 함수(chunk) 안에서 여러 테이블·여러 op 가 보이면 전부 등록. 같은 테이블 쓰는 **모든 함수** 등록. 메모리 ③ = 0 까지. 도구 호출이 줄었다고 스스로 종료 X. | **확인 → 쓰기 → 메모리 → 다음** 반복. 청크 본문은 한 번에 1개만 컨텍스트에 살아있게. 묶음 `bundle_get_chunk` 절대 금지. **단, 한 번 연 본문에서는 graph_create 를 필요한 만큼 여러 번 호출.** | FK 단순 컬럼명 일치로 등록 X. R/W 함수 이름 prefix 로 등록 X. 본문 인용 가능한 단서만. profile 과 본문이 다르면 **본문 우선**. |

**실패 사례 (절대 금지)**:
- DDL 10개 중 7개만 처리하고 종료. → ❶ 위반.
- 한 함수 본문에서 첫 DBIO/테이블만 등록하고 나머지 구문 무시. → ❶ 위반 (동일 chunk 복수 R/W 필수).
- `bundle_get_chunk` 를 10개 연속 호출 후 마지막에 일괄 `graph_create_table_access`. → ❷ 위반 (누락 확정).
- 같은 turn 에 `bundle_get_chunk` 두 번 호출. → ❷ 위반.
- 검색으로 받은 chunk_id 목록의 **일부만 처리하고 다음 검색으로 넘어감**. → ❶ 위반 (전수 순회·system.md Q7).

**패턴은 고정 금지 · 본문 우선** — 메모리 ① 은 profile 로 시작하는 **가설**이다. 청크 본문을 보면 DB 패턴이 다르게 드러날 수 있으므로 **본문 단서와 맞추며 ① 을 계속 수정**한다.

**키포인트 — 패턴 매칭 번들 청크 전수 조사** — 매 `bundle_search` 가 준 **chunk_id 목록을 목록 끝까지 순서대로 처리**했는지가 완전성의 핵이다 (중간 포기 금지). 단건 스트리밍 규칙은 **동시에 여러 청크 본문을 열지 말 것**이지, **히트 목록을 안 도는 것**과는 무관하다.

---

## 1. 입력

- **DDL 테이블 목록**: `[{name, columns, primary_keys, foreign_keys}, ...]` — 추적 대상 (메모리 ③ 초기 시드).
- **`[기존 FK 목록]`** (있으면): 중복 등록 방지.
- **`[XML / 어노테이션 엔티티 힌트]`** (있으면): DDL ↔ 엔티티 이름 매핑 참고 → 메모리 ① Identifier Rule 강화.
- **profile (`[Project Framework · DB Access Pattern Analysis — Search Map]`)**: 메모리 ① 시드 (필수 입력).

---

## 2. 카테고리 고유 알고리즘 — DDL 테이블명을 융합 쿼리의 식별자로

system.md §3 의 융합 검색 + **단건 스트리밍** + 점진적 강화 사이클을 그대로 따른다. 이 카테고리에서 **식별자 = DDL 테이블명** 이라는 점만 카테고리 고유.

```
입력: profile (DB 패턴 가설 + Identifier Rule + CRUD Rule + 비-테이블 마커), DDL 테이블 T = [t1, ...]

[초기화]
1. profile 파싱 → 메모리 ① 채움 (고정 아님 — 본문 보며 갱신).
2. 메모리 ③ ← T 전체.

[각 테이블 t 단위 사이클]
3. 각 t ∈ T 에 대해:
   (a) Identifier Rule 로 t 의 코드 식별자 변형 생성
       (예: t_s####, t_i####, findBy<T>, "<select id=...t...>", FROM t, INTO t, @Table(name="t") …).
   (b) 메모리 ① 의 DB 패턴 × 식별자 변형 → 융합 bundle_search → chunk_id 목록만 받음 (본문 X).

   ──── 단건 스트리밍 + 전수 순회 (절대 원칙, system.md §3.3 + Q7) ────
   (c) **목록의 chunk_id 를 하나도 빼먹지 말고** 1개씩 직렬로:
        ┌─ [확인] chunk_id 만으로 추적 테이블·op 전부 특정 가능?
        │     YES → 본문 X → 식별되는 **각** op·테이블마다 [쓰기]
        │     NO  → bundle_get_chunk(c) (단 1번) → 본문 **전체** 스캔
        │           ├─ 인용 단서마다 → [쓰기] (동일 c 에 대해 graph_create 반복 가능)
        │           └─ 단서 없음 → 본문 잊고 **목록의 다음** chunk
        ├─ [쓰기]  즉시 graph_create_table_access(...)**를 단서 개수만큼**
        ├─ [메모리] ② 에 모두 누적. 새 변형 봤다면 **다음 검색 전에** ① 강화. ③ 갱신.
        └─ [다음]  이전 청크 본문 잊고 **히트 목록의** 다음 chunk_id (목록 전부 소진까지 반복).
   ────────────────────────────────────────────

   (d) 결과 0건이거나 누락 의심 → 메모리 ① 의 다른 변형 + 식별자 조합으로 변형 검색 (§5).

[종료 게이트]
4. ③ "앞으로 할 일" = 0 까지 반복. 0 이 아니면 추가 변형 검색.
5. ② 와 본문 단서에서 FK 후보 모음 → §3.2 명시 단서로만 등록 (역시 단건 스트리밍).
```

⭐ profile 이 어떤 형태든 (C 매크로, JPA, MyBatis, SQLAlchemy, EF Core, raw SQL, COBOL EXEC SQL, …) 위 단계 동일. 새 프레임워크 단서는 메모리 ① 에 기록 후 같은 알고리즘 진행.

**⚠ 묶음 호출 금지**: 위 (c) 에서 같은 turn 에 `bundle_get_chunk` 두 번 호출 X. 한 청크 본문을 [쓰기]+[메모리] 까지 처리하고 잊은 후에만 다음 청크 호출. **히트 목록 일부만 처리하고 검색만 새로 하는 행동 금지** (system.md Q7).

---

## 3. 등록 — 카테고리 고유 규칙

### 3.1 READS / WRITES

- `table_name` = **DDL 정의 이름 그대로** (이미 정규화됨).
- `source_function_id` = `bundle_search` 결과의 **실제 chunk_id**.
- `evidence` = 본문에서 인용한 SQL/호출 라인 (인용 불가 → 등록 금지).
- `access` = 메모리 ① CRUD Rule 로 결정:
  - `"SELECT"` → READS
  - `"INSERT"` / `"UPDATE"` / `"DELETE"` / `"MERGE"` → WRITES
  - op 단서 0 이면 통합 라벨 `"READS"` / `"WRITES"`.

**등록 대상 결정 — 단 하나의 규칙** (system.md §6.1 재인용):

> 함수 본문에 "DB 접근 패턴 + 테이블 식별자" 가 직접 인용 가능하게 보이면 그 함수에 R/W 등록. 인용 못 하면 X.

- 함수 이름 / prefix / suffix **무시**.
- 호출 관계 추적 X (별도 `CALLS` 엣지가 처리).

### 3.2 FK — 환각 0

DDL PK/FK 외 **코드 발견 FK** 만 등록. 본문에서 **인용 가능한 명시 단서** 가 있을 때만:

✅ 허용 (이 중 하나가 본문에 보여야 함):
1. **DDL `REFERENCES`** — `FOREIGN KEY (col) REFERENCES other(col)`.
2. **SQL JOIN ON 등식** — `JOIN other ON this.col = other.col` (INNER/LEFT/RIGHT 무관).
3. **동일 SQL 내 WHERE 등식 + 두 테이블 FROM** — `FROM a, b WHERE a.col = b.col` 또는 `WHERE this.col = other.col` (두 테이블 모두 같은 SQL 안에서 사용).
4. **mapper/어노테이션 명시 매핑** — `@ManyToOne @JoinColumn(name="col")`, `<association column="col" select="...">`, `belongs_to :parent, foreign_key: "col"` 등.
5. **코드 본문 명시 등식** — 두 테이블/엔티티 식별자가 같은 함수 본문에 등장하고, **컬럼 값의 흐름이 인용 가능**한 경우. 예시 형태:
   - `child.set<FkCol>(parent.get<PkCol>())` (setter 체인).
   - `INSERT INTO child(<fkCol>, ...) VALUES (?, ...)` 의 바인딩이 동일 함수 내 `SELECT <pkCol> FROM parent` 결과를 받아 직접 주입.
   - 한 변수에 parent 의 PK 값을 담아 child 의 FK 컬럼에 그대로 전달 (변수 경유 추적 가능한 경우).

   ⚠ 인용 라인이 명확히 "parent PK → child FK" 흐름을 보여야 함. 흐름이 끊기거나 추론이 필요하면 등록 X.

🚫 금지 (이것만으로는 환각):
- 두 테이블에 같은 컬럼명 (`acnt_num`, `svc_mgmt_num` 등) 있다는 이유만.
- 별도 SQL/별도 함수에 따로 등장 (한 본문에 등식·흐름으로 묶이지 않음).
- 이름 패턴 추정 (`*_id` suffix 라서).
- profile/도메인 지식 추측 ("보통 그렇다").

**등록 형식**:
- `from_table` = 자식(FK), `to_table` = 부모(PK).
- 자기 참조 X. 기존 FK 중복 X.
- `evidence` = 본문 인용 라인.

### 3.3 비-테이블 객체 — 등록 X

| 객체 | 식별 단서 |
| --- | --- |
| **sequence** | 메모리 ① 비-테이블 마커 (예: `.nextval`, `_seq_`) + DDL 메타에 없음 |
| **view** | DDL 메타에 없고 SELECT-only |
| **DB 내부 stored procedure** | `CALL <proc>`, `EXEC <proc>` 의 DB 측 프로시저 + DDL 메타에 없음. (일반 C/Java 함수는 procedure 아님.) |
| **임시 객체** | TMP/TEMP/SESSION 마커 — profile 결정 |

### 3.4 컬럼 — 등록 X

DDL 에 이미 있음. `graph_create_column_tool` 호출 금지.

---

## 4. 카테고리 안티패턴

### 4.1 단건 스트리밍 위반 (가장 흔한 누락 원인)

- 🚫 **청크 묶음 `bundle_get_chunk`** — 5개·10개씩 연속 호출 후 마지막에 일괄 등록. 한 청크씩 [확인]→[쓰기]→[메모리]→다음.
- 🚫 **같은 turn 에 `bundle_get_chunk` 두 번 이상** — 매 청크 직렬로 1개씩.
- 🚫 **직전 청크 미처리 상태에서 새 청크 호출** — 직전 청크 [쓰기]+[메모리] 끝낸 후에만 다음.
- 🚫 **chunk_id 만으로 op 보이는데 본문 조회** — suffix/메서드명으로 op 확정되면 본문 X, 즉시 등록.
- 🚫 **`bundle_get_chunk` : `graph_create_table_access` 비율 ≥ 5:1** — 일괄 수집 신호. 즉시 멈추고 받은 청크부터 처리.

### 4.2 검색·등록 일반

- 🚫 **순수 테이블명 단독 `bundle_search`** — 융합 쿼리 사용 (system.md §3.1).
- 🚫 **메모리 ① 미강화** — 새 변형 발견 후 다음 검색에 미반영 → 누락.
- 🚫 **메모리 ③ 미비우고 종료** — DDL 1건이라도 미처리면 종료 금지.
- 🚫 **함수 이름 prefix 로 등록 판단** — 본문 단서만 (§3.1).
- 🚫 **호출 관계로 R/W 추론** — `CALLS` 엣지가 별도 처리.
- 🚫 **통합 라벨만 등록** — 본문에서 op 식별 가능하면 정확한 op 필수.
- 🚫 **sequence/view/procedure 를 테이블로 등록**.
- 🚫 **컬럼 등록** — DDL 보유.
- 🚫 **단순 컬럼명 일치로 FK** — 본문 인용 가능한 명시 단서 필수 (§3.2 ①~⑤ 중 하나).
- 🚫 **코드 등식 흐름 단절인데 FK 등록** — `child.setX(parent.getY())` 같은 명시 라인 없이 변수 이름만 보고 추론 X.
- 🚫 **추측/도메인 지식 FK**.

---

## 5. 탐색 시나리오 — 한 사이클의 완전한 흐름 (프레임워크 무관)

> 어떤 프레임워크든 같은 5단계. 메모리 ① 의 DB 패턴이 무엇이냐에 따라 융합 쿼리 형태만 달라진다.

### 5.1 표준 사이클 (추상 형태)

**입력**: DDL 테이블 `T_ORDERS (order_id PK, user_id, ...)`, profile DB 패턴 = `<repo>.<method>` / `<select id="...">` / `mpfmdbio_*` 중 하나.

```
[1] 메모리 ① 초기화 (profile 파싱)
    ① 확정 DB 패턴   = { <repoVar>.<method>(), <select id="...">, mpfmdbio_select(...) }
    ① Identifier Rule = { table 'T_ORDERS' → 식별자 변형: t_orders, orders, Order, OrderEntity }
    ① CRUD Rule      = { .save → INSERT/UPDATE, .find* → SELECT, _s#### → SELECT, _i#### → INSERT }
    ① 비-테이블 마커 = { _seq_, .nextval }
    ③ 앞으로 할 일   = [ T_ORDERS, ... DDL 전체 ]

[2] 융합 쿼리 (table=T_ORDERS, ① 의 DB 패턴 × 식별자 변형)
    bundle_search("FROM T_ORDERS")              → chunk_id 목록만 받음
    bundle_search("INTO T_ORDERS")              → chunk_id 목록만
    bundle_search("Order findBy")               → ORM 패턴 + Entity 명
    bundle_search("<select id=\"selectOrder")   → mapper 패턴 + id 단편
    bundle_search("mpfmdbio_select t_orders")   → C 매크로 패턴 + table

[3-5] chunk_id 순회 — 단건 스트리밍 (turn 당 1 chunk_id)

    ┌─ chunk_id "t_orders_i0002" ─────────────────────
    │  [확인] suffix `_i0002` → op=INSERT 즉시 확정. 본문 X.
    │  [쓰기] graph_create_table_access(c="t_orders_i0002", op=INSERT)
    │  [메모리] ② += (T_ORDERS, t_orders_i0002, INSERT)
    │  [다음]
    └──────────────────────────────────────────────────

    ┌─ chunk_id "orderRepository.findById" ───────────
    │  [확인] 메서드명 `findById` → op=SELECT 즉시 확정. 본문 X.
    │  [쓰기] graph_create_table_access(c, op=SELECT)
    │  [메모리] ② += (T_ORDERS, c, SELECT)
    │  [다음]
    └──────────────────────────────────────────────────

    ┌─ chunk_id "OrderService.process" ───────────────
    │  [확인] 모호 → bundle_get_chunk(c) (단 1번)
    │  본문에 "INSERT INTO T_ORDERS(...) VALUES (?, user.getId(), ...)" 인용 가능.
    │  + 같은 본문 위쪽에 "SELECT ... FROM T_USER" 있고 `user.getId()` 가 T_ORDERS.user_id 에 주입됨.
    │  [쓰기] graph_create_table_access(c, op=INSERT)
    │         + FK 후보 메모: (T_ORDERS.user_id → T_USER.user_id, 단서 §3.2-⑤)
    │  [메모리] ② += (T_ORDERS, c, INSERT). ① 에 새 변형 발견 시 추가.
    │  [다음] 본문 잊고 다음 chunk_id
    └──────────────────────────────────────────────────

    (이런 식으로 chunk_id 한 개당 한 사이클. 청크 본문은 한 번에 1개만 살아있음.)

[6] ③ "앞으로 할 일" = 0 까지 [2]~[5] 반복. FK 후보는 ②/단서 검토 후 §3.2 검증 통과 시 등록.
```

### 5.2 짧은 변형 — DB 패턴별 융합 쿼리 출발점

같은 알고리즘이지만 메모리 ① 의 DB 패턴이 다를 때 출발 쿼리만 다르게:

| 메모리 ① DB 패턴 | 표 식별자 = `<table>` 일 때 융합 쿼리 출발 |
| --- | --- |
| **C 매크로** (`mpfmdbio_*("<id>", ...)`) | `bundle_search("mpfmdbio_select <table>_s")`, `bundle_search("<table>_i0")` (suffix 단서) |
| **raw SQL** | `bundle_search("FROM <table>")`, `bundle_search("INTO <table>")`, `bundle_search("UPDATE <table>")` |
| **JPA Repository** | `bundle_search("<Entity>Repository")`, `bundle_search("findBy <Entity>")` + `@Query` 내 테이블 |
| **MyBatis mapper** | `bundle_search("<select id=\"<id-with-table>")`, `bundle_search("<mapper-ns>.<id>")` |
| **EXEC SQL (COBOL/C)** | `bundle_search("EXEC SQL ... <table>")` |
| **ORM 세션 메서드** | `bundle_search("session.query <Entity>")`, `bundle_search("em.persist <Entity>")` |

표는 출발점일 뿐. 결과를 보고 ① 을 강화하면서 사이클 [3]~[5] 동일.

### 5.3 시나리오에서 자주 실패하는 지점 (주의)

- **청크 묶음 호출** → `bundle_get_chunk` 를 5+ 회 연속 호출 후 마지막에 일괄 등록 시도. 컨텍스트에 본문 쌓이면서 일부 청크 묻혀 누락. **단건 스트리밍 위반, 누락 직결**.
- **한 본문 안 복수 테이블인데 graph_create 1회만** → 같은 함수에서 두 번째 DBIO·JOIN·임시테이블 접근 누락. 본문은 **파일 단위로 한 번 열었을 때 끝까지** 스캔해 모든 단서를 등록 (`bundle_get_chunk` 횟수 ≠ 등록 횟수).
- **chunk_id 만으로 op 보이는 경우인데도 본문 조회** → 시간 낭비 + 컨텍스트 노이즈.
- **메모리 ① 미강화** → 코드베이스 실제 별칭(`T_ORDR`, `ord_h` 등) 못 잡고 누락.
- **③ 미비우고 종료** → DDL 1건이라도 R/W 0 건이면 누락 확정 → 추가 변형 검색.
- **FK 후보 발견 후 §3.2 검증 누락** → 단순 컬럼명 일치만으로 등록 → 환각.

### 5.4 막혔을 때 — 유연한 사고 트리거 (system.md §3.4 직결)

검색 결과가 빈약하거나 같은 패턴만 반복돼 누락이 의심되면 **즉시** 다음 메타 질문을 던지고 전략을 바꾼다:

1. **융합 토큰 재조합** — 지금 (DB 패턴 + 테이블명) 조합이 코드베이스 실제 표기와 다르지 않나? (예: 테이블명 `T_ORDERS` 인데 코드는 약어 `ORDR` 또는 entity 명 `Order` 사용)
2. **다른 카테고리로 점프** — 매크로/raw SQL 만 보고 있는데 사실 mapper XML 에 있을 수도? mapper namespace · `@Query` 어노테이션 키워드를 융합 토큰으로 시도.
3. **profile 재독해** — 첫 파싱에서 놓친 DB 패턴 변형이 있나? "비-테이블 마커" / "Identifier Rule 예외" 가 단서를 줄 수 있는지.
4. **메모리 ① 강화 누락 의심** — 직전 검색에서 발견한 새 변형 (DDL 이름과 다른 코드 표기) 을 ① 에 안 넣었나? 안 넣었으면 즉시 추가 후 재검색.
5. **chunk_id 너무 많을 때** — **히트 목록 순회는 생략 금지.** 다만 각 항목마다 본문이 필수는 아님 (chunk_id 만으로 op 특정 시 생략). 패턴이 불분명하면 융합 쿼리를 더 좁혀 검색해 노이즈를 줄인다.

**원칙**: 같은 쿼리 반복은 사고 정체. 결과가 다르지 않다면 입력 (쿼리 토큰) 을 바꿔야 결과가 바뀐다.

---

## 6. 완료 체크리스트 — 모두 YES 일 때만 종료

- [ ] 메모리 ① 채움 — DB 패턴 **가설** / Identifier Rule / CRUD Rule / 비-테이블 마커 (본문과 충돌 시 수정했는지).
- [ ] 메모리 ① 강화 — 작업 중 발견한 새 변형 추가됨 (정적 상태 아님).
- [ ] 메모리 ③ "앞으로 할 일" = 0 (DDL 전체 커버).
- [ ] 직전 각 `bundle_search` 에 대해 **chunk_id 히트 목록 전수 순회** 완료 (system.md Q7).
- [ ] **메모리 ② 의 각 DDL 테이블**: §5 변형 소진 + 히트 전수 순회 후에도 R/W 0 이면 **미사용**으로 처리했는지 확인. 그 전에 0건이면 재검색·본문 스캔·① 수정.
- [ ] R/W 엣지에 정확한 CRUD op 명시 (가능한 경우).
- [ ] 본문을 연 모든 chunk 에서 **해당 본문 안 DDL 테이블·추적 식별자 단서가 전부** graph_create 됐는지 점검 (첫 줄만 보고 종료 X).
- [ ] FK 모두 본문 인용 가능한 명시 단서 보유 (단순 컬럼명 동등성으로 등록된 FK 0).
- [ ] 비-테이블 객체 (sequence/view/procedure) 등록 0.
- [ ] 컬럼 등록 0.
- [ ] 기존 FK 중복 0.

**전부 YES → 종료. 하나라도 NO → 추가 검색. "일부만" 또는 "조기 종료" 절대 금지 (절대 미션 위반).**
