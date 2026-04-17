---
name: entity-analysis
description: >
  startpoint(테이블명/엔티티 FQN/클래스 후보/프로파일 지침)에서 출발해
  실제 DB 접근 함수를 추적하여 READS/WRITES · FK · 컬럼을 등록한다.
allowed-tools: bundle_search_tool, bundle_get_chunk_tool, graph_create_table_access_tool, graph_create_column_tool, graph_create_fk_tool
---

# Entity Analysis Skill

user message의 [시작점] 섹션을 읽고 추적을 시작한다.
시작점 유형은 DDL 테이블명, 엔티티 FQN, 클래스/구조체 후보, 프로파일 지침 중 하나이다.

---

## 체인 추적 절차

각 시작점에서 출발해 **실제 DB 접근을 실행하는 함수**에 도달하고,
그 함수에 직접 `graph_create_table_access_tool`로 엣지를 생성한다.

### 체인 패턴 (범용)

코드에서 시작점이 사용되는 경로는 여러 형태가 있다. 아래 패턴을 참고해 추적한다:

1. **직접** — 함수 본문에 시작점(테이블명/엔티티명/SQL id)이 나타남 → 그 함수에 엣지
2. **변수 경유** — 시작점이 변수/상수에 담김 → `bundle_search`로 변수 참조자 추적 → 참조하는 함수에 엣지
3. **Helper 경유** — 시작점을 인자로 받는 helper 함수 존재 → helper의 호출자를 찾아 **호출자**에 엣지 (helper 자체 아님)
4. **매퍼/레포 경유** — 시작점이 매퍼 정의/Repository 메서드를 통함 → 매퍼 호출자에 엣지

### STOP 조건

한 테이블에는 여러 사용 갈래가 있다. 각 갈래를 추적하다가:
- (a) CRUD가 실제 실행되는 함수 본문을 만나거나
- (b) 그 함수를 호출하는 지점을 만나면

→ "이 함수가 이 테이블을 사용한다"로 확정.
→ 그 함수에 엣지 등록 후 해당 갈래 추적 종료.
→ 더 상위 호출자까지 무한 역추적 금지.
→ 다른 갈래가 남아있으면 새로 시작.

---

## 등록 규칙

### READS/WRITES

- `table_name`: user message의 명명 규칙이 있으면 적용 후 실제 테이블명
- `source_function_id`: 반드시 `bundle_search`에서 발견한 실제 함수의 chunk_id
- `evidence`: 구체적 호출문 필수 (실제 코드 라인 인용)
- `access`: READS 또는 WRITES — 코드에서 관찰된 것만

### 자체 검증

`graph_create_table_access_tool` 호출 직전:
> "이 함수의 코드에서 이 테이블의 접근 근거를 확인했는가? 구체적 코드가 무엇인가?"

→ 즉시 대답 불가하면 등록 금지.

### 컬럼

user message의 [컬럼 지침] 섹션을 반드시 읽고 따른다.
- "금지"라고 적혀있으면 `graph_create_column_tool` 호출 금지.
- 허용된 경우: READS/WRITES가 확정된 함수에서 사용하는 구조체/DTO 필드만 등록.
- 테이블 연결 증거 없는 필드 등록 금지.

### FK

- `from_table` = FK 컬럼 쪽 (자식), `to_table` = PK 쪽 (부모)
- 실제 참조 방향 준수. 확신 없으면 생략.
- 자기 참조 금지, 중복 등록 금지.

---

## 엔티티 판단 기준 (후보 시나리오용)

user message에 [시작점: 후보 클래스/구조체]가 있을 때만 적용.

✅ **엔티티 시그널** (하나 이상 해당하면 엔티티):
- SQL문에서 대응 테이블이 사용됨
- ORM/DB 함수의 인자로 사용됨
- 필드가 DB 컬럼과 1:1 대응 (id, created_at, status 등)
- 데이터 클래스 어노테이션 보유
- DB 매핑 메서드 보유

❌ **엔티티 아님** (스킵):
- 열거형(Enum)
- 예외 클래스
- 설정 클래스
- 서비스/로직 클래스 (Service, Manager, Controller, Repository, Handler 등 접미사)
- 추상 클래스 / 인터페이스

---

## 공통 규칙

- 하나의 함수를 찾았다고 멈추지 말고, 해당 테이블을 사용하는 **모든 함수**를 빠짐없이 찾을 것
- 검색 결과가 0건이면 검색어를 바꿔서 최소 2~3회 재시도
- 근거가 부족한 관계는 등록하지 말 것
- 추측 금지 — 코드에서 관찰된 것만 등록
