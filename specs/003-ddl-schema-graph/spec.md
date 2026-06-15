# Feature Specification: DDL Parsing → Schema Graph

**Feature Branch**: `003-ddl-schema-graph`

**Created**: 2026-06-15

**Status**: Backfilled (reverse-engineered)

**Input**: Backfill spec — 기존 구현 코드(`analysis_flow/step1_parse_ddl`, `shared/ddl`, `shared/neo4j`)에서 역설계. WHAT/WHY 기준이며, 실제 노드/엣지/동작과 일치하도록 작성됨.

## User Scenarios & Testing *(mandatory)*

<!--
  파이프라인 step1 (LLM 0회, 결정론 파싱). DDL = 스키마 구조의 진실 기준.
  소스코드 분석(step2+) 이전에 그래프의 골격(테이블·컬럼·관계)을 먼저 세운다.
-->

### User Story 1 - DDL에서 스키마 골격 그래프 구축 (Priority: P1)

분석가는 레거시 시스템의 `*.sql` DDL 파일들을 입력으로 주면, 시스템이 이를 파싱해 SCHEMA / TABLE / COLUMN 노드와 그 사이의 HAS_COLUMN / BELONGS_TO 관계를 Neo4j 지식 그래프로 만들어 주기를 원한다. 이 골격이 이후 소스코드 분석 단계가 참조하는 "구조의 진실 기준"이 된다.

**Why this priority**: 테이블·컬럼 노드 없이는 후속 단계(함수↔테이블 R/W 링킹, 설명 보강)가 붙을 대상이 없다. 파이프라인의 가장 기초 토대.

**Independent Test**: DDL 파일 1개(CREATE TABLE + 컬럼)만 주고 실행 → Neo4j에 TABLE 1개와 그에 매달린 COLUMN N개, HAS_COLUMN N개가 생기는지 확인. LLM 호출 0회로 결정론적 동일 결과여야 한다.

**Acceptance Scenarios**:

1. **Given** `CREATE TABLE`과 컬럼 정의가 든 DDL 파일, **When** step1 실행, **Then** TABLE 노드 1개 + 각 컬럼당 COLUMN 노드 + TABLE→COLUMN HAS_COLUMN 엣지가 UPSERT된다.
2. **Given** `CREATE SCHEMA` 또는 `SET search_path`로 스키마가 명시된 DDL, **When** 비수식 테이블이 정의되면, **Then** 그 테이블에 스키마가 부여되고 TABLE→SCHEMA BELONGS_TO 엣지가 생긴다.
3. **Given** PK / NOT NULL / UNIQUE / DEFAULT 제약이 든 컬럼, **When** 파싱, **Then** COLUMN 노드의 `is_primary_key` / `nullable` / `is_unique` / `default_value` 속성이 올바르게 채워진다(PK는 항상 NOT NULL).

---

### User Story 2 - 다중 방언 DDL 무손실 파싱 (Priority: P1)

분석 대상 레거시 DB는 Oracle / PostgreSQL / MySQL / MSSQL 등 제각각이다. 분석가는 방언을 일일이 지정하지 않아도 시스템이 DDL 본문에서 방언을 자동 감지하고, Oracle 물리옵션(TABLESPACE/STORAGE 등)·`VARCHAR2(n BYTE)`·`/` 종결자 같은 분석 무가치 구문을 정규화해 끝까지 파싱해 주기를 원한다.

**Why this priority**: 방언 1개라도 파싱 실패하면 해당 시스템 전체 골격이 비어 후속 분석이 불가능. 다중 방언 견고함이 곧 적용 가능 범위.

**Independent Test**: Oracle DDL(`VARCHAR2`, `NUMBER`, `TABLESPACE`, `COMMENT ON`)을 dialect 미지정으로 실행 → ParseError 없이 테이블/컬럼/설명이 추출되는지 확인.

**Acceptance Scenarios**:

1. **Given** `VARCHAR2`/`NUMBER`/`TABLESPACE` 마커가 든 Oracle DDL, **When** dialect 미지정 실행, **Then** Oracle 방언으로 자동 감지되어 파싱 성공.
2. **Given** `COMMENT ON TABLE/COLUMN ... IS '...'` 구문, **When** 파싱, **Then** 해당 설명이 TABLE/COLUMN의 `description`(출처 `description_source="ddl"`)으로 들어가고, 인라인 `--`/`/* */` 주석은 `comment`에 보존된다.
3. **Given** 컬럼목록 없는 `CREATE VIEW v AS SELECT`, **When** 파싱, **Then** TABLE 노드(`table_type="VIEW"`)만 생기고 컬럼은 생성하지 않는다.

---

### User Story 3 - FK 관계 추출 (인라인·ALTER·복합) (Priority: P2)

분석가는 테이블 간 외래키 관계가 그래프에 FK 엣지로 드러나, 데이터 의존 흐름을 따라갈 수 있기를 원한다. 컬럼 인라인 FK, 테이블 레벨 `FOREIGN KEY` 절, `ALTER TABLE ADD CONSTRAINT` FK, 그리고 복합(composite) FK까지 다뤄야 한다.

**Why this priority**: 노드 골격(P1)이 먼저고, 관계는 그 위에 얹히는 가치. FK 없이도 노드 그래프는 유효한 MVP.

**Independent Test**: 두 테이블 + `ALTER TABLE child ADD CONSTRAINT ... FOREIGN KEY (pid) REFERENCES parent(id)` DDL → child→parent FK 엣지가 from_column/to_column과 함께 생성되는지 확인.

**Acceptance Scenarios**:

1. **Given** 컬럼/테이블 레벨 또는 ALTER 기반 FK 정의, **When** 파싱, **Then** from-TABLE→to-TABLE FK 엣지가 `from_column`/`to_column`과 함께 생성된다.
2. **Given** 복합 FK `FOREIGN KEY (a,b) REFERENCES t(x,y)`, **When** 파싱, **Then** from/to 컬럼이 1:1 zip되어 FK 엣지 2개가 생성된다(개수 불일치 시 생성하지 않음).

---

### Edge Cases

- **DDL 디렉토리 없음 / `*.sql` 0개**: 파싱 결과 0건 → 노드·엣지 미생성, 읽기/쓰기 엣지도 없음. 오류가 아니라 정상이며 파이프라인은 계속 진행한다(DDL은 선택 입력).
- **config 누락(project/neo_client 없음)**: step을 건너뛰고 state를 바꾸지 않는다(단위 테스트 등 정상 시나리오).
- **db 식별자 미명시**: 가공 식별자(디렉토리 이름 등)를 박지 않고 빈 문자열 유지 → fqn이 `.schema.name` 형태로 db 부재를 명시.
- **스키마 미명시 테이블**: 강제 'public'을 넣지 않고 빈 스키마 유지 → BELONGS_TO 엣지를 만들지 않는다.
- **알 수 없는 컬럼 타입**: `dtype`을 `"UNKNOWN"`으로 표기(파싱 중단 X).
- **이스케이프된 작은따옴표(`''`) 설명**: `'`로 복원.
- **방언 자동감지 실패**: dialect=None으로 sqlglot 기본 파서 사용. `"ansi"`는 화이트리스트에 없어 사용 금지.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: 시스템은 DDL 디렉토리(미지정 시 프로젝트 루트) 아래 모든 `*.sql` 파일을 재귀 수집해 파싱 대상으로 삼아야 한다.
- **FR-002**: 시스템은 `CREATE TABLE`, 컬럼목록 있는/없는 `CREATE VIEW`, `CREATE SCHEMA`, `SET search_path`, `ALTER TABLE ... FK`를 결정론적으로(LLM 0회) 파싱해야 한다.
- **FR-003**: 시스템은 DDL 본문 마커로 방언(Oracle/PostgreSQL/MySQL 등)을 자동 감지하고, 감지 실패 시 sqlglot 기본 파서로 폴백해야 한다.
- **FR-004**: 시스템은 파싱 전 Oracle 물리옵션(TABLESPACE/STORAGE/PCTFREE/LOGGING 등)·`VARCHAR2(n BYTE)` 단위·`/` 종결자를 정규화 제거하되 의미(타입·제약·설명)는 보존해야 한다.
- **FR-005**: 시스템은 SCHEMA(db,name) / TABLE(fqn) / COLUMN(fqn) 노드를 생성하고, 각각 UNWIND 배치로 MERGE(UPSERT)해야 한다(재실행 시 멱등).
- **FR-006**: 시스템은 컬럼 제약을 추출해야 한다 — 컬럼 레벨 + 테이블 레벨 PK, NOT NULL, UNIQUE, DEFAULT. PK 컬럼은 항상 NOT NULL로 처리.
- **FR-007**: 시스템은 설명을 우선순위로 결정해야 한다 — `COMMENT ON` 우선, 없으면 `--`/`/* */` 주석, 둘 다 출처는 `description_source="ddl"`. 원본 인라인 주석은 `comment`에 항상 보존.
- **FR-008**: 시스템은 TABLE→COLUMN HAS_COLUMN, TABLE→SCHEMA BELONGS_TO, TABLE→TABLE FK 엣지를 생성하고 UNWIND 배치로 MERGE해야 한다.
- **FR-009**: 시스템은 인라인 FK·테이블레벨 `FOREIGN KEY`·`ALTER TABLE ADD CONSTRAINT` FK·복합 FK를 모두 처리하고, from/to 컬럼을 1:1 매핑해야 한다(개수 불일치 시 건너뜀).
- **FR-010**: 시스템은 노드를 라벨별 병렬 업서트하고(SCHEMA/TABLE/COLUMN 동시), 엣지는 노드 선행 의존 및 락 경합 회피를 위해 순차 업서트해야 한다.
- **FR-011**: DDL이 없거나 테이블이 0개여도 시스템은 오류 없이 빈 결과를 반환하고 파이프라인을 계속 진행해야 한다.
- **FR-012**: config(project/neo_client)가 누락되면 시스템은 step을 건너뛰고 state를 변경하지 않아야 한다.
- **FR-013**: 시스템은 db/스키마 식별자가 명시되지 않은 경우 가공 식별자나 강제 기본값('public')을 박지 않고 빈 문자열을 유지해야 한다.
- **FR-014**: 시스템은 단계 종료 시 schemas/tables/columns/has_column/belongs_to/fk 집계를 state에 누적해 반환해야 한다.

### Key Entities *(include if feature involves data)*

- **SCHEMA**: 데이터베이스 스키마(네임스페이스). 식별 키 = (`db`, `name`), 보조 속성 `fqn` = `db.name`.
- **TABLE**: 테이블 또는 뷰. 단일 식별 키 = `fqn`(`db.schema.name`). 보조 속성 `db`/`schema`/`name`, `description`(DDL 1차 설명) / `description_source` / `comment`(원본 주석) / `table_type`("TABLE"|"VIEW"). `analyzed_description`은 후속 보강 단계가 채움(이 기능 범위 밖).
- **COLUMN**: 컬럼. 단일 식별 키 = `fqn`(`db.schema.table.column`). 속성 `name`, `dtype`(미상 시 "UNKNOWN"), `nullable`, `is_primary_key`, `is_unique`, `default_value`, `description`/`description_source`/`comment`.
- **HAS_COLUMN 엣지**: TABLE → COLUMN (1:N 소속).
- **BELONGS_TO 엣지**: TABLE → SCHEMA (스키마 소속). 스키마 명시된 테이블에만 생성.
- **FK 엣지**: TABLE → TABLE (외래키). 속성 `from_column`/`to_column`, `fk_type`(기본 `many_to_one`), `is_candidate`(DDL 명시 FK는 `False`; 추론된 FK는 `True` = 사람 검토 큐).
- **방언(dialects)**: Oracle / PostgreSQL / MySQL / MSSQL 등. 본문 마커로 자동 감지, 없으면 sqlglot 기본.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Oracle / PostgreSQL / MySQL DDL을 dialect 미지정으로 입력해도 ParseError 없이 테이블·컬럼·설명이 추출된다(4개 주요 방언 모두 파싱 성공).
- **SC-002**: 동일 DDL을 반복 실행해도 그래프 노드/엣지 개수가 변하지 않는다(UPSERT 멱등성 100%).
- **SC-003**: DDL에 정의된 모든 테이블·컬럼·FK가 누락 없이 그래프에 반영된다(파싱된 테이블 수 = 입력 CREATE TABLE 수).
- **SC-004**: DDL 디렉토리가 비어 있거나 없을 때 파이프라인이 오류 없이 다음 단계로 진행한다(0 테이블 → 0 엣지 → 정상 종료).
- **SC-005**: 단계 실행은 LLM을 0회 호출한다(완전 결정론, 비용·지연 0).

## Assumptions

- DDL은 선택 입력이다 — DDL 디렉토리가 없거나 비어 있어도 이 단계와 후속 파이프라인은 유효하게 동작한다.
- 입력 DDL 파일은 UTF-8 인코딩이라고 가정한다.
- `db` 속성은 DDL에 명시된 값만 신뢰하며, 명시 없으면 빈 문자열(가공 식별자 미사용)이다.
- 뷰 컬럼은 SELECT 본문에서 유도되므로 이 단계의 분석 범위 밖이다(컬럼목록 없는 VIEW는 노드만 생성).
- 추론(candidate) FK는 별도 단계에서 `is_candidate=True`로 표시되며 사람 검토 큐로 흘러간다 — 이 단계는 DDL 명시 FK(`is_candidate=False`)를 다룬다. 단, 추론/candidate FK(`is_candidate=True`)는 **현재 어떤 스펙도 구현하거나 소유하지 않는다**(데이터 모델 상으로만 존재하는 aspirational 필드) — README 의 불일치 노트(003)와 일치.
- 기존 Neo4j 인스턴스와 `TABLE.fqn`/`SCHEMA` UNIQUE 제약(schema_constraints)이 사전 구성되어 있다고 가정한다.