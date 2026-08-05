# Feature Specification: 데이터소스 테이블 발견 — 단어장 이원화 + 샘플 수집 통합(DRY)

**Feature Branch**: `107-datasource-table-discovery`

**Created**: 2026-07-30

**Status**: Draft

**Input**: User description: "DDL 없이 데이터소스가 있으면 실제 테이블·컬럼·샘플을 가져오고, DDL이 있어도 항상 가져와 합친다. 처음부터 들고올 거면 그때 체킹하고 나중에 또 들고오는 중복 없이 DRY하게 — 연결 체크·수집을 한 곳으로."

## 근거 (전수조사 사실 — 2026-07-30)

- 단어장(테이블 목록)의 유일한 출처는 step1 parse_ddl. **DDL 파일 0건이면 빈 단어장으로 조용히
  진행**되어 R/W 링킹이 전부 불발한다 (parse_ddl_step.py — raise 없음 확인).
- 데이터소스 접근은 두 번으로 갈라져 있다: step6 `fetch_table_samples_step`(catalog
  sample-context, 연결 가드·청크 로직 한 벌)이 **분석 후반에 따로** 샘플만 가져온다.
  사용자 지적 그대로 "처음 들고오기/나중에 또 들고오기"의 이중 접점.
- catalog의 테이블 지식은 `graph_owner='analyzer'` 그래프 되읽기(catalog `samples/context.py`
  `_list_tables`) — **analyzer가 안 쓴 테이블은 catalog도 모른다** (순환). 실DB 테이블
  열거·컬럼·샘플의 원천은 fabric REST 3종이며 **이미 존재**한다:
  `GET /datasources/{name}/tables` · `…/{table}/schema` · `…/{table}/sample`.
- analyzer의 서비스 경계는 헌법상 Catalog HTTP뿐 — fabric 직결은 경계 신설이므로,
  발견(discovery)은 **catalog가 fabric을 프록시**하는 엔드포인트로 제공한다 (Cross-Service 변경).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - DDL 없이도 단어장이 생긴다 (Priority: P1)

DDL 파일을 주지 않은 고객 프로젝트라도, 분석 요청에 datasource가 지정되고 catalog가 활성이면
실DB의 테이블·컬럼이 단어장에 들어가 R/W 링킹이 동작한다.

**Why this priority**: 지금은 이 시나리오에서 링킹 결과가 조용히 0건 — silent fail의 실구멍.

**Independent Test**: DDL 디렉토리 빈 상태 + discovery 응답 모킹으로 링킹까지 단독 검증.

**Acceptance Scenarios**:

1. **Given** DDL 0건 + datasource 지정 + discovery가 테이블 N개 반환, **When** 수집 step 실행,
   **Then** `tables`에 N개(컬럼 포함)가 생기고 Neo4j에 Table/Column/HAS_COLUMN이 업서트된다.
2. **Given** 위 상태, **When** step5 링킹 실행, **Then** 소스의 해당 테이블 접근이 엣지로 생성된다.
3. **Given** datasource 미지정 또는 catalog 비활성, **When** 수집 step 실행, **Then** 종전과
   동일하게 skip(log_info)하고 DDL 단어장만으로 진행한다.

---

### User Story 2 - DDL이 있어도 항상 합친다 (Priority: P2)

DDL과 데이터소스가 둘 다 있으면 합집합: DDL에 빠진 실DB 테이블이 단어장에 추가되고,
겹치는 테이블은 **DDL 정의가 우선**(DDL = 진실 기준, 주석·FK 보존)이며 샘플만 보강된다.

**Acceptance Scenarios**:

1. **Given** DDL 테이블 A·B + discovery가 B·C 반환, **When** 수집, **Then** 단어장 = A·B·C,
   B는 DDL 정의 유지(중복 생성 0), C만 신규 생성.
2. **Given** 대소문자만 다른 동일명(b vs B), **When** 병합, **Then** 같은 테이블로 취급(중복 0).

---

### User Story 3 - 접점 하나(DRY): 샘플 수집 흡수 (Priority: P2)

데이터소스 접근은 수집 step **한 곳**뿐이다: 연결 가드 1벌, 수집 1회(테이블+컬럼+샘플).
기존 step6 `fetch_table_samples_step`은 제거(흡수)되고, step7이 읽는 `table_sample_data`
상태 키·형태는 그대로다.

**Acceptance Scenarios**:

1. **Given** discovery 응답에 sample_rows 포함, **When** 수집, **Then** `table_sample_data`가
   `{Table.id: {columns, sample_rows}}` 형태로 채워지고 step7은 무변경으로 소비한다.
2. **Given** 저장소 전체, **When** 데이터소스 HTTP 접점 검색, **Then** 수집 step 1곳뿐
   (`fetch_table_samples_step` 파일·배선·참조 0).

---

### Edge Cases

- **개별 테이블 실패**: 테이블 1개의 schema/sample 조회 실패 → 그 테이블만 컬럼/샘플 없이
  진행 + log_warning (전체 불정지 — 기존 청크 부분실패 계약 승계).
- **datasource가 catalog/fabric에 미등록(404)**: log_warning + 스트림 메시지 후 DDL 단어장으로
  계속 (고객 환경에서 정상일 수 있음 — 단, 조용히 넘기지 않고 표면화).
- **catalog 활성인데 non-404 오류**: raise (기존 catalog 계약 동일 — 활성 상태의 실패는 심각).
- **대형 datasource**: `max_tables` 상한(기본 1000) 초과분은 절단하되 **몇 건 잘렸는지 log_warning**
  (조용한 절단 금지).
- **이름 병합 한계**: 병합은 대소문자 무시 정확 일치만. 구 sample-context의 fuzzy(≥85) 매칭으로
  이름 표기 편차를 흡수하던 동작은 사라진다 — discovery가 실DB 진짜 이름을 직접 공급하므로
  단어장 자체가 정확해져 필요성이 소멸. 계약 문서에 한계로 명시.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: catalog는 `GET /tables/discovery?datasource=&sample_limit=&max_tables=`를 제공한다.
  응답 = `{tables: [{name, schema, columns: [{name, dtype, nullable}], sample_rows: [...]}], truncated: n}`.
  내부는 fabric REST 3종 프록시(동시성 상한, 테이블 단위 부분실패 허용).
- **FR-002**: analyzer 수집 step은 parse_ddl 이후·링킹 이전에 실행되어, 발견 테이블을 단어장에
  병합(합집합·DDL 우선·대소문자 무시)하고 신규 Table/Column/HAS_COLUMN을 Neo4j에 업서트한다.
- **FR-003**: 수집 step이 `table_sample_data`(`{Table.id: {columns, sample_rows}}`)를 채우고,
  `fetch_table_samples_step`은 파일·전략 배선·테스트까지 제거된다 (옛 참조 grep 0).
- **FR-004**: 가드는 수집 step 한 곳: catalog 비활성 / datasource 미지정 → log_info skip.
  404 → log_warning 후 계속. 그 외 오류 → raise (헌법 IV).
- **FR-005**: 발견-신규 테이블의 `description_source`는 `"catalog"`로, db는 datasource명으로
  기록한다 (출처 추적 — DDL 유래와 구분 가능).
- **FR-006**: 두 전략(framework/dbms) 모두 같은 step 선언으로 배선한다 (분기 0 — 헌법 II).
- **FR-007**: LLM 호출 0 (헌법 I).

### Key Entities

- **DiscoveryResponse** (Cross-Service 계약): contracts/discovery-contract.md 소유.
- **Table/Column/HasColumn**: 스키마 변경 없음. 값 출처만 이원화(ddl | catalog).
- **table_sample_data**: 형태 불변 — 생산자만 step6 → 수집 step으로 이동.

## Success Criteria *(mandatory)*

- **SC-001**: DDL 0건 + discovery 모킹 N테이블 → 단어장 N + 링킹 엣지 생성 (통합 테스트).
- **SC-002**: DDL∪discovery 병합에서 중복 생성 0, DDL 정의 우선 (단위 테스트).
- **SC-003**: `fetch_table_samples_step` 참조 grep 0 + step7 소비 계약 테스트 green (형태 불변).
- **SC-004**: 기존 pytest 전량 green (사전 존재 실패 1건 제외 기준선 유지).
- **SC-005**: catalog 신규 엔드포인트 계약 테스트 (fabric 게이트웨이 모킹) green — 부분실패·
  404·절단 로그 케이스 포함.

## Assumptions

- fabric REST 3종은 현행 그대로 사용 (fabric 저장소 무변경).
- 실 datasource·fabric·Neo4j 런타임 E2E는 이 세션 미가용 → 모킹 계약 테스트 + 실기동 시
  quickstart 절차로 검증 (미검증 항목 정직 기록).
- catalog의 기존 `POST /tables/sample-context`는 유지 (다른 소비자 존재 가능) — analyzer만
  discovery로 이동. 이후 소비자 0 확인 시 별건으로 정리.
- 발견 테이블의 Schema 노드·BELONGS_TO는 생성하지 않는다 (DDL 유래만) — 네임스페이스 뷰가
  필요해지면 별건. 계약 문서에 명시.
