---
name: entity-analysis
description: >
  DDL 없이 CLASS/STRUCT/UNION 후보에서 DB 엔티티를 판별하고,
  엔티티 테이블에 접근하는 함수를 추적하여 READS/WRITES 관계를 등록하고,
  엔티티 간 FK 관계를 등록한다.
  DDL이 없고 외부 엔티티 정의(XML/어노테이션)도 없을 때 사용.
allowed-tools: bundle_search_tool, bundle_get_chunk_tool, graph_create_table_access_tool, graph_create_fk_tool
---

# Entity Analysis Skill

CLASS/STRUCT/UNION 후보에서 DB 엔티티를 식별하고 관계를 등록한다.

## 목표 (3단계 순서대로 수행)

1. **엔티티 판별**: 후보 중 DB 테이블에 매핑되는 클래스/구조체 식별
2. **READS/WRITES 추적**: 식별된 엔티티 테이블에 접근하는 모든 함수 찾아 등록
3. **FK 등록**: 엔티티 간 외래키 관계를 빠짐없이 등록

엔티티가 하나도 발견되지 않으면 아무것도 등록하지 말고 즉시 종료.

---

## 1단계: 엔티티 판단 기준

✅ **엔티티 시그널** (하나 이상 해당하면 엔티티):
- SQL문(INSERT/UPDATE/DELETE/SELECT)에서 대응되는 테이블이 사용됨
- ORM/DB 함수의 인자로 사용됨 (save, persist, find, query, db_insert 등)
- 필드가 DB 컬럼과 1:1 대응 (id, created_at, status 등)
- 데이터 클래스 어노테이션 (@Entity, @Table, @dataclass, typedef struct 등)
- DB 매핑 메서드 보유 (from_row, to_dict, toEntity, fromResultSet 등)

❌ **엔티티가 아닌 것** (반드시 스킵):
- 열거형(Enum) — OrderStatus, PaymentType 등 상태값만 나열
- 예외 클래스 (Error/Exception)
- 설정 클래스 (Config, Settings, Properties)
- 서비스/로직 클래스 — Service, Manager, Controller, Repository, Handler, Dao, Helper, Util, Factory 접미사 포함 시 무조건 스킵
- 추상 클래스 / 인터페이스

★ **핵심 구분**: 엔티티 = "데이터 그 자체" (테이블 한 행에 대응), 서비스 = "데이터를 조작하는 로직"

---

## 2단계: READS/WRITES 추적

각 엔티티에 대해:

1. 엔티티 코드를 `bundle_search` / `bundle_get_chunk`로 조회하여 판단
2. 엔티티 확정 시:
   - 클래스명으로 `bundle_search(kind="symbol")` → 사용처 탐색
   - SQL 테이블명(복수형 등)으로 `bundle_search(kind="text")` → 추가 탐색
   - DBIO 호출(`mpfmdbio` 등)으로 `bundle_search(kind="text")` → DBIO 탐색
3. 각 함수의 코드를 `bundle_get_chunk`로 **반드시 직접 읽고** 접근 근거 확인

### ★ READS/WRITES 등록의 절대 규칙

코드에서 확인 가능한 접근 근거가 있을 때만 등록:

**[유형 1] SQL문** — 테이블명이 SQL에 직접 포함
  예: `INSERT INTO orders VALUES(...)` → WRITES

**[유형 2] 해당 테이블 전용 DB 함수 호출** — 함수명에 테이블명 포함
  예: `db_order_insert(&new_order)` → WRITES

**[유형 3] ORM/Repository API**
  예: `orderRepository.save(order)` → WRITES

**[유형 4] DBIO 프레임워크** — `mpfmdbio()` 호출
  접미사: `_s`=SELECT(READS), `_i`=INSERT, `_u`=UPDATE, `_d`=DELETE(WRITES)

❌ **등록 금지**: 추측, 함수명에서 유추, 같은 모듈의 다른 함수 기반 추론, 도메인 지식 기반 추론

### 자체 검증

`graph_create_table_access_tool` 호출 직전:
> "이 함수의 코드에서 이 테이블의 접근 근거를 확인했는가? 구체적 코드가 무엇인가?"
→ 즉시 대답 불가하면 등록 금지. evidence에 구체적 코드를 반드시 기재.

등록 시:
- `table_name`: 엔티티 클래스명 그대로 (예: Order). SQL 테이블명(orders) 사용 금지.
  DBIO의 경우 mpfmdbio 인자에서 추출한 테이블명 그대로 사용.
- `source_function_id`: 반드시 bundle_search에서 발견한 실제 함수의 chunk_id
- `evidence`: 구체적 호출문 필수

---

## 3단계: FK 등록

모든 READS/WRITES 처리 후, 아래 3가지 소스를 전부 탐색:

**[소스 ①] CREATE TABLE / DDL 내 REFERENCES 절**
- `bundle_search("CREATE TABLE", kind="text")` → 코드 내 DDL 검색
- `bundle_search("REFERENCES", kind="text")` → 추가 검색
- `column ... REFERENCES other_table(pk)` 패턴 → FK 확정

**[소스 ②] SQL JOIN 패턴**
- `JOIN ... ON a.col = b.col` → FK 후보

**[소스 ③] 필드명 관례**
- 모든 엔티티의 필드를 `bundle_get_chunk`로 확인
- `xxx_id` 패턴 필드 → 대응하는 xxx 엔티티가 후보에 있으면 FK 등록

### FK 등록 규칙
- from_table, to_table 모두 반드시 엔티티일 것
- 방향: from_table = FK 컬럼 쪽(자식), to_table = PK 쪽(부모)
- 자기 자신 FK 금지, 중복 등록 금지
- 양쪽 중 최소 한쪽이 후보이면 등록 가능

---

## 공통 규칙

- 하나의 함수를 찾았다고 멈추지 말고, 해당 테이블을 사용하는 **모든 함수**를 빠짐없이 찾을 것
- 검색 결과가 0건이면 검색어를 바꿔서 최소 2~3회 재시도
- 근거가 부족한 관계는 등록하지 말 것
