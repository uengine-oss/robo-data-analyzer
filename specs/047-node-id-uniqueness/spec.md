# Feature Specification: 노드 id 전역 유일성 복원 + id 불투명화

**Feature Branch**: `047-node-id-uniqueness`
**Created**: 2026-07-13
**Status**: Draft

**Input**: UI E2E 검증 중 발견 — 구조체가 DB 컬럼을 멤버로 갖고, 파일과 테이블이 한 노드로 합쳐지는 현상. 추적 결과 **서로 다른 노드가 같은 `id` 값을 갖고 있었다.**

---

## 배경 — 실측된 사실

### 무엇이 깨졌나

`schema_constraints.py:14-15` 가 명시한 불변식:

> **"모든 노드 식별 키 = `id` 단일 (종류 무관). 값은 노드별 고유 문자열"**

**이 불변식이 깨져 있다.** Neo4j 실측:

```
id='cart'              → 노드 2개: [TABLE] 과 [FILE]
id='member'            → 노드 2개: [TABLE] 과 [FILE]
id='member.member_id'  → 노드 2개: [COLUMN] 과 [MEMBER(구조체 필드)]
```

### 왜 겹치나 — 두 세계가 같은 모양의 id를 만든다

| 노드 | id 규칙 | 예 |
|---|---|---|
| 테이블 | `db.schema.name` (빈값 생략) | `cart` |
| 컬럼 | `테이블.컬럼` | `member.member_id` |
| 파일 | 경로에서 **확장자 제거**, `/`→`.` | `cart` |
| 구조체 필드 | `모듈.이름` — **구조체 이름이 빠짐** | `member.member_id` |

**DB 세계와 코드 세계가 구분 표식 없이 같은 점 표기를 쓴다.** 겹치는 건 시간문제였다.

### 왜 Neo4j 가 안 막았나

UNIQUE 제약이 **라벨별**이다 (`FOR (t:TABLE) REQUIRE t.id IS UNIQUE`). 다른 라벨끼리 같은 id는 위반이 아니다. 게다가 코드 노드는 라벨이 **열린 집합**(antlr type)이라 제약 자체가 없다.

### 실제 피해 (실측)

1. **그래프 오염**: 무라벨 `MATCH (n {id: …})` 가 두 노드를 **모두** 잡아 잘못된 엣지·라벨을 붙였다.
   - `TABLE` 에 `HAS_MEMBER` → 코드노드 **144개**
   - `TABLE` 에 코드 역할 라벨 `:SERVICE` **5개**
   - 구조체 `member_row_t` 가 **DB 컬럼을 멤버로** 가짐
2. **화면 오작동**: 프론트가 `properties.id` 로 노드를 키잉하므로 파일과 테이블이 **한 노드로 합쳐진다** → 트리에서 파일 밑에 테이블 컬럼이 붙고, 클릭 시 엉뚱한 카드가 열린다.

### 더 깊은 병 — **id 를 뜯어 쓴다**

id는 "이 노드가 누구인가"를 가리키는 **불투명한 열쇠**여야 한다. 그런데 3곳이 **문자열을 잘라 부모를 알아낸다**:

| 위치 | 하는 짓 |
|---|---|
| analyzer `analyze_code_step.py:127` | `c.id.rsplit(".",1)[0]` — 컬럼 id 를 잘라 테이블 id |
| architect `rule_extractor.py:63` | `fid.rsplit(".",1)[0]` — 함수 id 를 잘라 모듈 id |
| architect `agentic_retriever.py:155` | `fqn.split(".")[-1]` — 모듈 id 를 잘라 이름 |

**이게 id 규칙을 못 바꾸게 만든 진짜 족쇄다.** id 를 뜯는 한, id 는 영원히 "주소"여야 하고, 주소는 언젠가 겹친다.

---

## User Scenarios & Testing

### User Story 1 - 서로 다른 노드는 서로 다른 id를 갖는다 (Priority: P1)

분석이 만든 그래프에서 **id가 같은 노드가 두 개 이상 존재하지 않는다.**

**Why**: 이 불변식이 깨지면 그래프 전체가 거짓말을 한다 — 엣지가 엉뚱한 노드에 붙고, 화면이 다른 것을 보여준다.

**Independent Test**: 분석 후 `MATCH (n) WITH n.id AS id, count(*) AS c WHERE c > 1 RETURN count(*)` → **0** 이어야 한다.

**Acceptance Scenarios**:
1. **Given** 테이블 `cart` 와 소스 파일 `cart.c` 가 있는 프로젝트, **When** 분석, **Then** 두 노드의 id가 **다르다**.
2. **Given** `member` 테이블의 컬럼 `member_id` 와 `member.c` 의 구조체 필드 `member_id`, **When** 분석, **Then** id가 **다르다**.
3. **Given** 같은 이름의 `foo.c` 와 `foo.h`, **When** 분석, **Then** 두 파일 노드의 id가 **다르다**.

### User Story 2 - 관계가 올바른 노드에만 붙는다 (Priority: P1)

**Independent Test**: `MATCH (t:TABLE)-[r]->() WHERE NOT type(r) IN ['HAS_COLUMN','FK'] RETURN count(r)` → **0**.

**Acceptance Scenarios**:
1. **Given** 분석 완료, **When** 테이블 노드의 관계를 조회, **Then** `HAS_COLUMN`/`FK` **외의 관계가 없다** (현재: `HAS_MEMBER` 144개).
2. **Given** 분석 완료, **When** 테이블 노드의 라벨을 조회, **Then** 코드 역할 라벨(`SERVICE` 등)이 **없다** (현재: 5개).
3. **Given** 구조체 노드, **When** 멤버를 조회, **Then** **자기 필드만** 나온다 (DB 컬럼 없음).

### User Story 3 - 아무도 id를 뜯지 않는다 (Priority: P1)

부모·소속을 알아낼 때 **id 문자열을 자르지 않고**, 명시된 속성이나 관계를 쓴다.

**Why**: id를 뜯는 한 id 규칙을 바꿀 수 없고, 겹침을 고칠 수 없다. **이게 병의 뿌리다.**

**Independent Test**: analyzer·architect 전수 grep → id 를 `split`/`rsplit` 하는 코드 **0건**.

**Acceptance Scenarios**:
1. **Given** 리팩터 후, **When** 컬럼의 테이블을 찾는 코드, **Then** id 파싱이 아니라 **관계(HAS_COLUMN) 또는 명시 속성**을 쓴다.
2. **Given** 리팩터 후, **When** 아키텍트가 함수의 모듈을 찾는 코드, **Then** **노드 속성(`owner_id`)** 을 읽는다.
3. **Given** 리팩터 후, **When** 아키텍트가 모듈 이름이 필요할 때, **Then** **`name` 속성**을 읽는다.

### User Story 4 - 같은 실체를 여러 번 발견해도 한 노드가 된다 (Priority: P1)

파일별 AST가 같은 폴더·패키지를 반복 보고해도 이는 충돌이 아니라 **동일 실체의 반복 관측**으로 정규화되어야 한다. 서로 다른 실체가 같은 id를 갖는 경우만 실패해야 한다.

**Independent Test**: 같은 `mall` 폴더 아래 AST 12개를 적재 → `PACKAGE(id="mall")` 관측 12건이 최종 노드 1개가 되고 분석이 계속되며, 같은 id의 다른 종류/내용 노드는 명시적 충돌로 실패한다.

**Acceptance Scenarios**:
1. **Given** 같은 폴더 아래 소스 12개, **When** AST를 합산, **Then** 공유 Package는 1개이고 Module은 12개다.
2. **Given** 같은 id·같은 타입·같은 내용의 노드가 반복 관측됨, **When** 등록, **Then** 첫 노드만 남고 반복 관측 수가 구조화 로그에 기록된다.
3. **Given** 같은 id인데 타입 또는 내용이 다른 노드, **When** 등록, **Then** 저장 전에 명시적 충돌 오류가 스트림과 로그에 드러난다.

### Edge Cases

- **Java 오버로드**: 같은 클래스에 이름·시그니처가 다른 동명 메서드 → id 뒤에 위치 접미가 필요하다. 접미 구분자는 **경로 구분자와 달라야** 한다.
- **중첩 구조체**: 구조체 안 구조체의 필드 → 컨테이너 경로가 끝까지 반영되어야 한다.
- **외부 stub**: 파일·라인이 없는 노드는 위치가 없다 → 이름 기반 id를 쓰되 **`code:` 네임스페이스 안**에 둔다.

---

## Requirements

### 유일성 (구조적 보장)

- **FR-001**: 노드 id는 **네임스페이스 접두사**를 가져야 한다 — 코드 세계와 DB 세계가 **섞일 수 없어야** 한다. 접두사 구분자는 **파일명·SQL 식별자에 들어갈 수 없는 문자**여야 한다 (우연이 아니라 **구조적 보장**).
- **FR-002**: 코드 노드의 id는 **그 노드가 실제로 사는 위치**를 반영해야 한다 — 파일 경로(**확장자 포함**) + 담고 있는 컨테이너(구조체/클래스) 경로 + 이름. 파일시스템과 언어가 이미 보장하는 유일성을 **버리지 않고 가져온다**.
- **FR-003**: DB 노드의 id는 DB가 보장하는 유일성(db·스키마·테이블·컬럼)을 그대로 반영해야 한다.
- **FR-004**: 같은 컨테이너에 같은 이름이 둘일 수 있는 경우(Java 오버로드)만 **위치 접미**를 붙인다. 그 접미 구분자는 **경로 구분자와 달라야** 한다.
- **FR-005**: 분석이 끝난 그래프에 **id가 중복인 노드가 0개**여야 한다.

### id 불투명화 (병의 뿌리 제거)

- **FR-006**: 어떤 소비자도 **id 문자열을 잘라 부모·이름을 알아내서는 안 된다.**
- **FR-007**: 코드 노드는 자기 **소속 모듈 id를 노드 속성으로 갖는다** — 소비자가 id를 뜯지 않고 읽을 수 있어야 한다. (현재 `owner_id` 는 dataclass 에만 있고 **그래프에 저장되지 않는다** → 아키텍트가 id를 뜯을 수밖에 없었다.)
- **FR-008**: 컬럼의 소속 테이블은 **관계(`HAS_COLUMN`) 또는 명시 속성**으로 찾는다.

### 그래프 무결성 (방어)

- **FR-009**: 노드를 찾는 Cypher 는 **id 유일성에만 기대지 않는다** — 끝점 종류를 아는 곳은 **라벨을 함께 제약**한다. (`has_member.py` docstring 이 *"id 가 단일 유니크 키라 라벨 제약 없이 안전"* 이라 적어놓고 그 전제가 깨져 있었다.)
- **FR-010**: 파일별 파서 산출물을 합칠 때 모든 노드는 단일 등록 경로를 거쳐야 한다. 원시 리스트를 그대로 전역 유일성 검사에 넣지 않는다.
- **FR-011**: 같은 id·같은 구체 타입·같은 값의 반복 관측은 하나로 정규화한다. 노드 종류별 허용 목록은 두지 않는다.
- **FR-012**: 같은 id인데 구체 타입 또는 값이 다르면 실제 식별 충돌로 간주해 `log_error`·NDJSON `error`·예외로 실패시킨다.
- **FR-013**: 전역 유일성 가드는 정규화된 최종 노드 집합에 유지하며, 정규화된 반복 관측 수를 실행 로그에 기록한다.

---

## Success Criteria

- **SC-001**: 분석 후 **id 중복 노드 0개**. (현재: 9쌍 이상)
- **SC-002**: `TABLE` 노드에 `HAS_COLUMN`/`FK` 외의 관계 **0개**. (현재: `HAS_MEMBER` 144개)
- **SC-003**: `TABLE` 노드에 코드 역할 라벨 **0개**. (현재: `:SERVICE` 5개)
- **SC-004**: 구조체 노드의 멤버에 **DB 컬럼 0개**.
- **SC-005**: analyzer·architect 에서 **id 를 파싱하는 코드 0건**.
- **SC-006**: 코드 노드가 `owner_id`(소속 모듈) 속성을 **그래프에 갖는다**.
- **SC-007**: 화면 — 파일 노드 아래에 **DB 컬럼이 붙지 않는다**. 이름이 같은 노드를 클릭해도 **맞는 카드**가 열린다.
- **SC-008**: 그 외 분석 결과는 종전과 **동일**하다 (노드 수·라벨 분포).
- **SC-009**: `shopmall/mall` 소스 12개가 공유하는 Package는 1개로 저장되고 `load_ast_step`이 완료된다.
- **SC-010**: 타입/내용이 다른 동일 id 입력은 정규화되지 않고 테스트에서 반드시 실패한다.

---

## Addendum: opaque id consumer regression (2026-07-14)

### User Story 5 - opaque id 이후에도 내부 호출을 내부로 연결한다 (P1)

호출 대상 판별은 함수 `id`에서 이름을 잘라내지 않고 `CodeNode.name`과 `owner_id`를 사용한다.

- 정의가 있는 같은 이름의 호출은 기존 함수로 `CALLS(scope=internal)` 연결한다.
- 분석 대상에 정의가 없는 사용자 함수만 external stub으로 연결한다.
- stdlib/framework runtime 호출은 stub을 만들지 않는다.
- 동명 함수가 여러 개면 호출자와 같은 `owner_id`의 함수를 우선한다.

### User Story 6 - graph id 해석 의존성을 제거한다 (P1)

Analyzer와 직접 소비자는 graph node id를 `split`, `rsplit`, prefix/suffix 추출로 해석하지 않는다. 이름은 `name`, 부모는 `owner_id` 또는 관계, AST 종류는 `label`, DB 소속은 명시 필드/관계에서 읽는다.

- **FR-014**: 호출 이름 index는 `CodeNode.name`, 동일 파일 판별은 `CodeNode.owner_id`를 사용한다.
- **FR-015**: `CodeNode.own_ast_type()`은 id 문자열이 아니라 명시 AST `label`을 사용한다.
- **FR-016**: external stub id는 중앙 helper가 `code:external:` namespace에서 생성한다.
- **FR-017**: analyzer/architect/catalog/frontend 전수 검색 결과를 `research.md`에 남긴다.

**Success**: shopmall 내부 함수명으로 생성된 `MODULE:EXTERNAL`은 0건이며, 감사 대상 production code의 graph id 구조 해석도 0건이다.

## Assumptions

- **id 값이 바뀌는 것은 수용한다**: 매 분석마다 그래프를 통째로 새로 만들므로(헌법 full-rebuild), 옛 id에 묶인 영속 데이터가 없다.
- **아키텍트 동반 수정**: id 파싱 2곳을 속성 읽기로 바꾼다. 이는 **계약 개선**이지 회귀가 아니다 — 오히려 아키텍트가 id 모양에 묶여 있던 것을 풀어준다.
- **프론트·catalog·data-fabric 무영향**: id 를 불투명 키로만 쓰거나(프론트), 라벨·이름으로 접근한다(catalog).
