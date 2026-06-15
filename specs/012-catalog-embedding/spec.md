# Feature Specification: Catalog Integration & Embedding

**Feature Branch**: `012-catalog-embedding`

**Created**: 2026-06-15

**Status**: Backfilled (reverse-engineered)

**Input**: 기존 구현 코드(shared/external/catalog_client.py · analysis_flow/step5_build_context/fetch_table_samples_step.py · analysis_flow/step7_post_processing/enrich_lineage_from_catalog_step.py · analysis_flow/step7_post_processing/embed_step.py · shared/llm/embedder.py · shared/config/env_catalog.py)에서 역공학으로 backfill 한 명세 — "이미 동작하는 것"을 문서화.

## User Scenarios & Testing *(mandatory)*

<!--
  IMPORTANT: User stories should be PRIORITIZED as user journeys ordered by importance.
  Each story is INDEPENDENTLY TESTABLE — 셋 다 ROBO_* env 토글로 단독 on/off 가능.
-->

### User Story 1 - 테이블 샘플 행으로 시나리오 품질 강화 (Priority: P1)

분석가가 DBMS 코드베이스를 분석할 때, 시나리오/문맥 생성 step 이 컬럼의 **실제 값**(예: 상태 코드, enum, 날짜 포맷)을 보고 더 정확한 GWT 시나리오를 만들도록, build_context 직후 외부 catalog 서비스에서 테이블별 샘플 행을 한 번에 받아 둔다.

**Why this priority**: 샘플 행은 LLM 분석의 입력 품질을 직접 끌어올리는 가장 영향 큰 보강 — 추측 대신 실제 데이터 기반.

**Independent Test**: `ROBO_CATALOG_ENABLED=true` + `datasource` 지정 상태로 분석을 돌려 `table_sample_data` 에 매칭된 테이블별 `{columns, sample_rows}` 가 채워지는지 확인.

**Acceptance Scenarios**:

1. **Given** catalog 활성·datasource·테이블 N개, **When** 분석이 build_context 를 통과, **Then** 청크(100개)별 병렬 `POST /tables/sample-context` 로 매칭된 테이블만 `table_sample_data` 에 적재.
2. **Given** 일부 청크가 HTTP 실패, **When** 샘플 수집, **Then** 실패한 청크의 테이블만 샘플 없이 진행(log_warning 표면화)하고 분석 전체는 불정지.
3. **Given** `ROBO_CATALOG_ENABLED=false` 또는 datasource 미지정 또는 테이블 0개, **When** step 진입, **Then** 즉시 graceful skip (`{}` 반환).

---

### User Story 2 - DBMS ETL 리니지를 그래프 엣지로 보강 (Priority: P2)

코드 정적분석만으로 잡히지 않는 DBMS ETL/리니지 흐름을, catalog 의 `/lineage` 응답으로 받아 코드의 실제 Function 과 매칭해 READS/WRITES 엣지로 그래프에 추가한다. 매칭 안 되는 source 는 가상 노드로라도 흔적을 남긴다.

**Why this priority**: 코드에 없는(저장 프로시저·외부 ETL) 데이터 흐름을 그래프에 채워 리니지 완전성을 높이지만, P1 샘플 보강 없이도 그래프는 성립.

**Independent Test**: catalog `/lineage` 가 flows 를 반환하는 상태에서 분석을 돌려 Neo4j 에 ReadsWrites 엣지(및 매칭 실패 시 `__catalog__:<source>` 가상 노드)가 생기는지 확인.

**Acceptance Scenarios**:

1. **Given** catalog 활성·`/lineage` 가 flows 반환, **When** post-processing, **Then** 각 flow 의 source 를 function_id/파일경로(절대·상대·suffix)로 매칭해 ReadsWrites 엣지 upsert.
2. **Given** flow.source 가 어떤 Function 과도 매칭 안 됨, **When** 엣지 생성, **Then** function_id 를 `__catalog__:<source>` 가상 노드로 두고 엣지 생성(흐름 유실 방지).
3. **Given** `/lineage` 가 404, **When** step 진입, **Then** 정상으로 간주하고 `{}` 반환(실패 아님).

---

### User Story 3 - 의미 임베딩으로 검색 기반 마련 (Priority: P3)

Table/Column 설명과 Function 요약을 다국어 임베딩 벡터로 변환해 Neo4j 노드의 `embedding` property 에 저장, 향후 의미 검색·유사도 질의의 기반을 만든다.

**Why this priority**: 다운스트림(검색)용 부가 기능 — 그래프 구축 자체와 독립이며 토글로 끌 수 있음.

**Independent Test**: `ROBO_PIPELINE_EMBED_ENABLED=true` 로 분석 후 Table/Column/Function 노드에 `embedding` property 가 SET 되는지, description/summary 빈 노드는 제외되는지 확인.

**Acceptance Scenarios**:

1. **Given** description/summary 가 있는 노드들, **When** embed step, **Then** 그룹별 배치 인코딩 후 UNWIND 로 `embedding` property SET + state 노드 객체에도 반영.
2. **Given** `ROBO_PIPELINE_EMBED_ENABLED=false`, **When** step 진입, **Then** 즉시 skip(느린 모델 로드 회피).

---

### Edge Cases

- catalog 활성인데 연결 자체 실패(타임아웃·DNS) → 심각으로 간주하고 raise (조용히 skip 금지). 단 개별 자원 404 는 정상(None).
- non-200(404 제외) 또는 JSON 파싱 실패 → RuntimeError/ValueError 전파.
- 빈 table_names → catalog 호출 없이 `{}`.
- `/lineage` flow 에 table_fqn 없음 또는 relation 정규화 실패 → 해당 flow 건너뜀.
- description/summary 가 빈 노드 → 임베딩 대상에서 제외, embedding 미부여 노드는 state 에 원본 그대로 유지.
- 제거된 엔드포인트: `enrich_metadata_from_catalog_step`(GET `/tables/<fqn>/metadata`)는 미존재 엔드포인트라 **삭제됨** — 테이블 메타는 sample-context 로 대체.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST catalog 연동을 `ROBO_CATALOG_ENABLED`(기본 true)로 토글하고, off 또는 `ROBO_CATALOG_BASE_URL` 미설정이면 catalog step 들을 graceful skip 해야 한다.
- **FR-002**: System MUST catalog 호출 시 `ROBO_CATALOG_BASE_URL`(rstrip)을 base 로, `ROBO_CATALOG_TIMEOUT_SECONDS`(기본 120)를 HTTP timeout 으로 사용해야 한다.
- **FR-003**: System MUST 개별 자원 404 는 정상(None/skip)으로, 그 외 non-200·파싱 실패·연결 실패는 catalog 활성 시 raise 로 처리해야 한다(조용히 묻지 않음).
- **FR-004**: System MUST build_context 직후 `POST /tables/sample-context` 로 테이블 샘플 행을 수집하고, 테이블명을 100개 청크로 병렬 호출하며 테이블당 최대 5행을 받아 `table_sample_data` 에 적재해야 한다.
- **FR-005**: System MUST 샘플 수집 시 datasource 미지정 또는 테이블 0개면 skip 하고, 청크 단위 실패는 해당 테이블만 제외(log_warning)하되 분석 전체는 중단하지 않아야 한다.
- **FR-006**: System MUST DBMS 리니지 보강에서 `GET /lineage` 응답의 각 flow source 를 function_id·절대경로·상대경로·suffix 로 매칭해 ReadsWrites(READS/WRITES) 엣지를 Neo4j 에 upsert 해야 한다.
- **FR-007**: System MUST flow source 가 코드 Function 과 매칭되지 않으면 function_id 를 `__catalog__:<source>` 가상 노드로 설정해 엣지를 생성해야 한다(흐름 유실 방지).
- **FR-008**: System MUST 임베딩 step 을 `ROBO_PIPELINE_EMBED_ENABLED`(기본 true)로 토글하고, off 시 모델 로드 없이 skip 해야 한다.
- **FR-009**: System MUST Table.description / Column.description / Function.summary 중 비어있지 않은 것만 텍스트로 수집해 그룹별 1회 배치 인코딩 후 노드의 `embedding` property 를 SET 하고 state 노드 객체에도 반영해야 한다.
- **FR-010**: System MUST 임베딩 backend 를 LLM 영역(`shared/llm/embedder`)에 격리하고, 현재 로컬 다국어 모델 `paraphrase-multilingual-MiniLM-L12-v2`(lazy 로드·1회 캐시)를 사용해야 한다.
- **FR-011**: System MUST 분석기 책임을 "URL 호출 + 응답 처리"로 한정하고, catalog 인증·dataset 식별·정책은 catalog 서비스 영역으로 둬 분석기 인자로 흐르지 않게 해야 한다.

### Key Entities *(include if feature involves data)*

- **catalog `POST /tables/sample-context`**: 외부 catalog HTTP 엔드포인트. 입력=datasource·table_names·sample_limit·similarity_threshold. 응답=`{요청 테이블명: {resolved, score, columns, sample_rows} | None}`(매칭 실패=None).
- **catalog `GET /lineage`**: 외부 catalog HTTP 엔드포인트. 응답=`{flows: [{source, table_fqn, relation, dml_type, ...}]}`. DBMS ETL 리니지 원천.
- **table_sample_data**: state 키. `{table_fqn: {columns, sample_rows}}` — 매칭된 테이블만 보관, step6 가 함수별로 골라 씀.
- **embedding property**: Neo4j Table/Column/Function 노드의 벡터 property. 다국어 의미 임베딩 결과, 향후 검색용.
- **`__catalog__:<source>` 가상 노드**: 리니지 flow 의 source 가 코드 Function 과 매칭 실패할 때 만드는 합성 function_id — 외부 흐름의 흔적을 그래프에 남김.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: catalog 활성·datasource 지정 분석에서 catalog 에 존재하는 테이블 100%가 매칭되면 `table_sample_data` 에 샘플 행이 담긴다.
- **SC-002**: 청크 일부가 실패해도 분석은 끝까지 완주하며, 실패 청크 수가 로그에 표면화된다(부분 실패가 전체 중단을 일으키지 않음).
- **SC-003**: catalog `/lineage` 가 반환한 모든 유효 flow(table_fqn·relation 있음)가 그래프 엣지로 반영되고, 매칭 실패 flow 도 `__catalog__:` 가상 노드로 1건도 유실되지 않는다.
- **SC-004**: 임베딩 활성 시 description/summary 가 있는 모든 Table/Column/Function 노드에 `embedding` property 가 부여되고, 빈 설명 노드는 0건 처리된다.
- **SC-005**: catalog 비활성(`ROBO_CATALOG_ENABLED=false`) 또는 임베딩 비활성(`ROBO_PIPELINE_EMBED_ENABLED=false`)일 때 해당 step 들이 외부 호출/모델 로드 없이 즉시 skip 된다.

## Assumptions

- catalog 는 분석기와 별개로 운영되는 **외부 HTTP 서비스**이며, 인증은 내부망/IP whitelist 등 catalog 측 책임(분석기는 인증 인자를 보내지 않음).
- 운영 경로(HTTP `/analyze`)는 datasource 를 AnalyzeRequest 입력으로 받고, `ROBO_CATALOG_TEST_DATASOURCE` 는 CLI/테스트 전용(운영 코드 경로 미사용).
- `base_url` 에는 catalog api_prefix(예: `/robo`)가 포함된 형태로 주어진다(client 는 그 뒤에 `/tables/sample-context`·`/lineage` 만 붙임).
- 리니지 보강은 DBMS 코드베이스에만 적용된다(Framework 흐름은 정적분석으로 처리).
- 현재 임베딩은 로컬 sentence-transformers 다국어 모델이며, 향후 GPU 임베딩 서버 도입 시 `shared/llm/embedder` 내부만 HTTP 클라이언트로 교체(호출자 embed_step 변경 0).
- Neo4j MERGE 가 동일 (function_id, table_fqn, relation) 키로 엣지를 자동 dedup 한다(catalog 발견 R/W 의 중복 안전).
- 임베딩 대상은 `Table.description` / `Column.description` / `Function.summary` 이며, spec 010 이 쓰는 `analyzed_description` 필드는 임베딩하지 **않는다**(analyzed_description 은 별도 보관 필드로 유지, embed 대상 아님).
