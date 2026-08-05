# Feature Specification: DML 문법 의미표 — R/W 판정의 단일 진실

**Feature Branch**: `106-dml-semantics-table`

**Created**: 2026-07-30

**Status**: Draft

**Input**: User description: "DML 키워드 하드코딩 나열(SELECT|WITH|INSERT|UPDATE|DELETE|MERGE)을 문법 구문 → READS/WRITES 의미표(공통 SQL 코어 + 방언별 추가분, 데이터 표)로 교체. TRUNCATE 부재로 AMS 실코드 40여 건 누락/오분류 실증. FROM 없는 오라클 DELETE도 READS 오분류. C 리터럴 SQL 판정도 같은 의미표를 소비."

## 근거 (실측)

벤치마킹 딥리서치 2회 + `분석대상모음` 전수 실측으로 확정된 채택안 1/3. (2/3 데이터소스 통합 수집, 3/3 wrapper DSL은 별도 spec. 값 전파 엔진은 실측 0건으로 기각.)

- **TRUNCATE 누락**: `EXECUTE IMMEDIATE 'TRUNCATE TABLE AMS_MIG.TD_AB00008';` — AMS_procedures.sql에 46회 등장(주석 처리 포함, 활성 라인 다수). 현재 `_DML_KW_RE`(link_dbms_tables_step.py)에 TRUNCATE가 없어 해당 노드 자기 줄에 다른 DML이 없으면 **엣지 통째 누락**, SELECT가 섞여 있으면 **READS 오분류**. 테이블 전체 삭제라는 가장 파괴적인 쓰기가 그래프에서 안 보이거나 읽기로 기록된다.
- **FROM 없는 DELETE 오분류**: `EXECUTE IMMEDIATE 'DELETE SGIOS.ARREO_SMS_KAKAO'|| …` (GIOS 실코드) — 오라클은 `DELETE <table>`(FROM 생략)이 합법인데 `_WRITE_TARGET_RE`가 `DELETE\s+FROM`만 매칭 → 테이블은 발견되지만 write_targets에 안 들어가 **READS 오분류**.
- **이중 누수**: 감지를 고쳐도 `derive_dml_type`(graph_edges.py)의 화이트리스트 `("SELECT","INSERT","UPDATE","DELETE","MERGE")`에 TRUNCATE가 없어 dml_type이 조용히 빈 문자열로 강등된다. 같은 종류의 지식(DML 구문 → 의미)이 **최소 3곳에 중복 하드코딩**되어 있다:
  1. `link_dbms_tables_step.py` — `_DML_KW_RE`, `_WRITE_TARGET_RE`
  2. `regex_table_linker.py` — `_C_SQL_START_RE`, `_C_SQL_WRITE_TARGET_RE`, `_C_SQL_READ_TARGET_RE`, `_SQL_KEYWORD_RE`
  3. `graph_edges.py` — `derive_dml_type` 화이트리스트

한 곳을 고치면 나머지가 어긋나는 구조 자체가 결함이다 (헌법 III 단일 진실 위반이 이 사고의 근본 원인).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - TRUNCATE가 쓰기로 보인다 (Priority: P1)

Oracle 프로시저를 분석한 아키텍트 사용자가 "이 테이블을 누가 비우는가"를 조회하면, `EXECUTE IMMEDIATE 'TRUNCATE TABLE …'`를 실행하는 프로시저가 해당 테이블의 **WRITES** 관계로 나타난다.

**Why this priority**: 테이블 전체 삭제는 영향도 분석에서 가장 위험한 쓰기다. 지금은 누락되거나 READS로 뒤집혀 있어, 이 그래프를 소비하는 아키텍트의 공유 테이블 검사·업무 흐름 추적이 실물과 다른 결론을 낸다. 실측으로 확인된 유일한 대량(40여 건) 오분류이므로 최우선.

**Independent Test**: AMS_procedures.sql 하나만 분석 실행 → Neo4j에서 `TD_AB00008` 등 TRUNCATE 대상 테이블의 인입 엣지 조회로 단독 검증 가능.

**Acceptance Scenarios**:

1. **Given** `EXECUTE IMMEDIATE 'TRUNCATE TABLE AMS_MIG.TD_AB00008';`가 자기 줄에 있는 노드, **When** DBMS 링킹 실행, **Then** 그 노드 → TD_AB00008 엣지가 `relation=WRITES`, `dml_type=TRUNCATE`로 생성된다.
2. **Given** 같은 자기 줄에 SELECT와 TRUNCATE가 함께 있는 노드, **When** 링킹 실행, **Then** TRUNCATE 대상 테이블은 WRITES, SELECT 소스 테이블은 READS로 각각 분리 판정된다.
3. **Given** 주석 처리된 `/*EXECUTE IMMEDIATE 'TRUNCATE TABLE …';*/`, **When** 링킹 실행, **Then** 엣지가 생성되지 않는다 (주석 제거 규칙 유지).

---

### User Story 2 - FROM 없는 DELETE가 쓰기로 보인다 (Priority: P2)

오라클 관용 구문 `DELETE <스키마>.<테이블>`(FROM 생략)을 쓰는 프로시저가 해당 테이블의 **WRITES**로 나타난다.

**Why this priority**: GIOS 실코드에서 확인된 오분류. 발견은 되고 방향만 틀리므로 P1보다는 피해가 작지만 같은 판정층 결함.

**Independent Test**: `DELETE SGIOS.ARREO_SMS_KAKAO` 포함 소스 하나로 링킹 실행 → 엣지 relation 확인.

**Acceptance Scenarios**:

1. **Given** `DELETE SGIOS.ARREO_SMS_KAKAO` (FROM 없음), **When** 링킹 실행, **Then** ARREO_SMS_KAKAO 엣지가 `relation=WRITES`, `dml_type=DELETE`.
2. **Given** 기존 `DELETE FROM t` 형태, **When** 링킹 실행, **Then** 종전과 동일하게 WRITES (회귀 없음).

---

### User Story 3 - DML 지식이 한 곳에만 산다 (Priority: P3)

새 DML 구문(예: 방언 추가분)을 지원하려는 유지보수 개발자가 **데이터 표 한 곳**만 수정하면, DBMS 링커·C 리터럴 SQL 판정·dml_type 표준화가 전부 같은 판단을 내린다.

**Why this priority**: 이번 사고의 근본 원인 제거. P1/P2는 증상 수리, P3는 재발 방지 구조.

**Independent Test**: 의미표에 테스트용 구문 1개를 추가 → 세 소비처(링커 2곳 + derive_dml_type)가 모두 반영됨을 단위 테스트로 확인. 소스 전체 grep으로 의미표 밖 DML 키워드 나열이 0건임을 확인.

**Acceptance Scenarios**:

1. **Given** 의미표에 구문 하나 추가, **When** 세 소비처 각각 해당 구문 입력 처리, **Then** 세 곳 모두 표의 relation/dml_type대로 판정한다.
2. **Given** 의미표에 없는 임의 토큰, **When** `derive_dml_type` 류 표준화 호출, **Then** 종전과 동일하게 빈 문자열(비표준 op 차단 계약 유지).
3. **Given** 저장소 전체, **When** DML 키워드 나열 패턴 검색, **Then** 의미표(및 그 테스트) 외 위치에서 0건.

---

### Edge Cases

- **주석 속 DML**: `-- TRUNCATE …`, `/* … */` — 주석 제거 후 스캔 유지. 주석 처리된 TRUNCATE 3건(AMS 실측)이 엣지를 만들면 안 된다.
- **문자열 리터럴 속 DML**: `EXECUTE IMMEDIATE '…'` 안의 리터럴 SQL은 **의도적으로 잡아야 하는 대상**이다 (현행 계약: 주석만 제거, 문자열 보존 — 유지).
- **MERGE의 양면성**: `MERGE INTO t USING s` — t는 WRITES, s(USING 쪽)는 READS. C 경로는 이미 이 구분이 있고 DBMS 경로에도 동일 계약 적용.
- **CTAS**: `CREATE TABLE t AS SELECT … FROM s` — t는 쓰기 생성, s는 READS. 현행은 CREATE 자체가 키워드에 없어 t·s 모두 판정 불가. 의미표 커버리지에 포함.
- **스키마 수식**: `AMS_MIG.TD_AB00008` — 기존 `(?:[\w$]+\.)?` 수식 허용 계약 유지.
- **동일 테이블 READS+WRITES 공존**: 같은 노드가 같은 테이블을 읽고 쓰면 두 엣지 모두 생성 (기존 정체성 `(source, target, relation)` 계약 유지).
- **대소문자**: DML 키워드·테이블명 모두 대소문자 무시 매칭 유지.
- **비 DML 본문**: ASSIGN/RAISE/CALL만 있는 노드는 종전대로 엣지 0 (이건 정당한 "DML 아님"이지 silent fail이 아니다).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: DML 구문 → (relation READS|WRITES, dml_type, 대상 추출 방식)의 대응은 **단일 데이터 표**(이하 의미표)에만 존재해야 한다. 구조는 공통 SQL 코어 + 방언별 추가분(예: oracle)으로 나뉘며, 언어·방언 차이는 표의 데이터로만 표현한다 (로직 분기 금지 — 헌법 II).
- **FR-002**: 의미표 공통 코어는 최소 다음을 포함해야 한다: SELECT/WITH(READS), INSERT(WRITES), UPDATE(WRITES), DELETE(WRITES, **FROM 생략형 포함**), MERGE(INTO 대상 WRITES + USING 소스 READS), TRUNCATE TABLE(WRITES), CREATE TABLE … AS SELECT(생성 대상 쓰기 + 소스 READS).
- **FR-003**: `link_dbms_tables_step`의 DML 감지·write 대상 추출은 의미표에서 파생되어야 하며, 하드코딩 키워드 나열(`_DML_KW_RE`, `_WRITE_TARGET_RE` 상수 직접 정의)은 제거한다.
- **FR-004**: `regex_table_linker`의 C 리터럴 SQL 판정(`_C_SQL_START_RE`, `_C_SQL_WRITE_TARGET_RE`, `_C_SQL_READ_TARGET_RE`, `_SQL_KEYWORD_RE`)도 동일 의미표에서 파생한다. 두 소비처의 판정 결과가 같은 입력에 대해 어긋나면 결함이다.
- **FR-005**: `derive_dml_type`의 허용 op 집합은 의미표에서 파생한다. TRUNCATE는 유효 dml_type이 된다. 의미표에 없는 토큰은 종전대로 빈 문자열(비표준 차단 계약 유지).
- **FR-006**: TRUNCATE 대상 테이블은 WRITES + `dml_type=TRUNCATE` 엣지를 생성해야 한다 (US1).
- **FR-007**: FROM 생략 DELETE 대상 테이블은 WRITES + `dml_type=DELETE` 엣지를 생성해야 한다 (US2).
- **FR-008**: 기존 정상 판정(리터럴 SELECT/INSERT/UPDATE/DELETE FROM/MERGE INTO, C 경로 포함)은 관계·dml_type이 변경 전과 동일해야 한다 (회귀 0 — 기존 테스트 전량 green + 대표 코퍼스 전후 엣지 diff로 증명).
- **FR-009**: 의미표 로딩 실패·형식 오류는 조용한 기본값 폴백 없이 명시적 오류로 표면화한다 (헌법 IV).
- **FR-010**: 판정 로직에 새 LLM 호출을 추가하지 않는다. 전 과정 결정론 유지 (헌법 I).

### Key Entities *(include if feature involves data)*

- **DML 의미표**: 구문(키워드/패턴) → relation(READS|WRITES), dml_type, 대상 테이블 추출 위치(직후 식별자 / USING 절 / AS SELECT 소스 등)의 표. 공통 코어 + 방언 섹션. `ontology_roles.yaml`과 같은 "언어차는 데이터" 계약의 새 식구.
- **ReadsWrites 엣지**: 기존 스키마 유지 — `(source_id, target_id, relation)` 정체성, `dml_type`·`dml_types`·`locations` 부속 메타. **스키마 변경 없음**, 값 집합에 TRUNCATE 추가.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: AMS_procedures.sql 분석 시 활성(비주석) `TRUNCATE TABLE` 구문 전건이 WRITES+dml_type=TRUNCATE 엣지로 생성된다 (현행 0건 → 전건). 주석 처리분은 0건 유지.
- **SC-002**: FROM 생략 DELETE 실측 라인(GIOS)이 WRITES로 판정된다 (현행 READS → WRITES).
- **SC-003**: 저장소 전체에서 의미표 및 그 테스트 밖의 DML 키워드 나열 정의 grep 0건.
- **SC-004**: 기존 pytest 전량 통과 + 변경 전후 대표 코퍼스(AMS·RWIS·shopmall) 엣지 스냅샷 diff에서 "의도한 추가/전환(TRUNCATE·FROM 생략 DELETE·CTAS)" 외 차이 0건.
- **SC-005**: 같은 SQL 텍스트에 대한 DBMS 경로와 C 리터럴 경로의 relation 판정 불일치 0건 (공유 파생 테스트로 증명).

## Assumptions

- 대상은 노드 자기 줄 텍스트 기반 판정 계약(현행) 유지 — 이 spec은 판정 규칙의 출처를 표로 옮기는 것이지, 자기 줄 스캔 방식·주석 제거·문자열 보존 계약을 바꾸지 않는다.
- 동적 조립 SQL(`EXECUTE IMMEDIATE v_Query`)은 이 spec 범위 밖 — 실측(코퍼스 내 함수 1개)으로 기각된 값 전파 엔진의 영역이며, 종전과 동일하게 엣지 미생성.
- dml_type 신규 값(TRUNCATE)의 다운스트림 소비자(graph_delta_stream, enrich_table_description, search/graph_access, frontend 계약 spec 075)는 **문자열 값을 열거 제한 없이 다루는지** plan 단계에서 전수 확인한다. 제한이 발견되면 같은 작업에서 정합시킨다.
- PostgreSQL 등 타 방언의 추가 구문(COPY, ON CONFLICT 등)은 의미표 구조가 수용하되, 이번 구현의 검증 범위는 실측 코퍼스가 있는 Oracle + C 리터럴 SQL로 한정한다.
- 오분류율 정량 측정(gold set 채점)은 사용자 별도 관할 — 이 spec의 검증은 단위·계약 테스트와 실코퍼스 전후 대조로 한다.
