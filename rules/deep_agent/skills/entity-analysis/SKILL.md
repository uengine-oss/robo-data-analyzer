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

DB 테이블/엔티티를 식별하고 READS/WRITES · FK 관계를 등록한다.

## 두 가지 동작 모드

user 메시지가 어느 쪽인지 보고 판단:

**모드 A — 후보 기반** (일반 OOP 프로젝트)
user 메시지에 CLASS/STRUCT/UNION 후보 목록이 주어진다.
→ 후보 중 엔티티 판별 → READS/WRITES 추적 → FK 등록 (3 단계 순차 수행).

**모드 B — 프로파일 지침 기반 수색** (엔티티 클래스가 AST 에 없는 레거시)
user 메시지에 "프레임워크/DB 접근 패턴 분석" 섹션이 포함되며 4 개 소섹션으로 구성:
  - **호출 방식**: 이 프로젝트에서 DB 접근 시 쓰는 실제 함수/메서드·호출 라인 원문
  - **식별 방식**: 테이블이 어디에 어떻게 적혀 있는가 (문자열 리터럴 / XML id / 어노테이션 등)
  - **명명 규칙**: 테이블명에서 제거/통합해야 할 패턴이 있으면 그 규칙
  - **관찰된 테이블 예시**: 분석자가 본 테이블 3~5 개 (복붙 금지, 참고만)

→ 엔티티 판별 단계 스킵. 아래 순서로 수행:

1. **호출부 수집** — "호출 방식" 의 키워드로 `bundle_search(kind="text")` 해 모든 호출부를 찾는다
2. **테이블 도출** — 각 호출부 코드를 `bundle_get_chunk` 로 읽어 "식별 방식" 이 정한 위치에서 식별자를 뽑고, "명명 규칙" 을 적용해 실제 테이블명을 얻는다
3. **테이블 → 사용 함수 묶기** — 각 테이블에 대해, 그 테이블을 쓰는 함수들을 모은다 (중복 제거)
4. **함수 단위 READS/WRITES 판별** — 각 (함수, 테이블) 쌍마다:
   - "명명 규칙" 이나 "식별 방식" 에 CRUD 판별 단서가 있으면 그 단서를 따른다
   - 없으면 해당 호출부의 실제 코드 문맥으로 판단 (관찰한 것만, 추측 금지)
5. **테이블 등록** — `graph_create_table_access_tool(table_name, access, evidence, source_function_id)` 호출
6. **컬럼 등록** — 각 테이블에 대해 호출 인자로 넘어가는 구조체/DTO 정의를 `bundle_search` + `bundle_get_chunk` 로 찾아, **실제로 관찰된 필드**만 `graph_create_column_tool(table_name, column_name, dtype, description)` 로 등록. 추측 금지. 타입·설명이 코드에 없으면 빈 문자열.
7. **FK 등록** — 관찰된 테이블 간 PK-FK 관계가 있으면 `graph_create_fk_tool(from_table, from_column, to_table, to_column, evidence)` 호출. DDL 이 없어 불확실하면 생략.

모드 A 에서 엔티티 0 개 → 종료. 모드 B 는 후보 유무와 무관하게 분석 지침만으로 수행.

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
   - 클래스명 · SQL 테이블명 · 프로젝트 고유 호출 패턴(있다면 user 메시지의 분석에서 확인) 을 단서로
     `bundle_search(kind="symbol" 또는 "text")` 로 사용처를 모두 찾는다
3. 각 함수의 코드를 `bundle_get_chunk`로 **반드시 직접 읽고** 접근 근거 확인

### ★ READS/WRITES 등록의 절대 규칙

코드에서 확인 가능한 접근 근거가 있을 때만 등록:

**[유형 1] SQL문** — 테이블명이 SQL에 직접 포함
  예: `INSERT INTO orders VALUES(...)` → WRITES

**[유형 2] 해당 테이블 전용 DB 함수 호출** — 함수명에 테이블명 포함
  예: `db_order_insert(&new_order)` → WRITES

**[유형 3] ORM/Repository API**
  예: `orderRepository.save(order)` → WRITES

**[유형 4] 프로젝트 고유 DB 접근 패턴** — user 메시지에 포함된 프레임워크/DB 접근 패턴 분석에 따름
  분석이 기술한 호출 방식·식별 방식·명명 규칙을 그대로 지침 삼아 코드를 탐색한다.
  무엇이 테이블명인지, 어떻게 추출하는지, READS/WRITES 판정 기준은 전부 분석 내용이 정한다.
  코드에서 관찰되지 않은 식별자는 쓰지 말 것.

❌ **등록 금지**: 추측, 함수명에서 유추, 같은 모듈의 다른 함수 기반 추론, 도메인 지식 기반 추론

### 자체 검증

`graph_create_table_access_tool` 호출 직전:
> "이 함수의 코드에서 이 테이블의 접근 근거를 확인했는가? 구체적 코드가 무엇인가?"
→ 즉시 대답 불가하면 등록 금지. evidence에 구체적 코드를 반드시 기재.

등록 시:
- `table_name`:
  - 모드 A (엔티티 클래스 기반): 엔티티 클래스명 그대로. SQL 물리 테이블명 변형 사용 금지.
  - 모드 B (프로파일 지침 기반): user 메시지 분석의 "명명 규칙" 적용 후 실제 테이블명.
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
