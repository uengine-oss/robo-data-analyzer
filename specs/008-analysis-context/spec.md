# Feature Specification: Analysis Context Building

**Feature Branch**: `008-analysis-context`

**Created**: 2026-06-15

**Status**: Backfilled (reverse-engineered)

**Input**: User description: "pipeline step5 — for every Function, build an AnalysisContext (identity/location/reads·writes·callers·callees/referenced_symbols); compress large function bodies into a one-liner via a small-model LLM only when body tokens exceed a threshold (avoid leaking full callee bodies downstream); Framework augments identity, DBMS adds parent/sibling tree context; plus an optional, strategy-independent table-sample fetch from the catalog."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 모든 함수에 결정적 분석 컨텍스트 부착 (Priority: P1)

레거시 코드가 Function 노드와 Linking 결과(reads/writes/callers/callees, 참조 변수·상수)로
분해된 뒤, 다음 의미 분석 단계(step6)가 함수 한 개를 분석할 때 필요한 모든 주변 사실을
함수마다 한 묶음(AnalysisContext)으로 미리 모아둔다. 식별(누구), 위치(어디), 읽고 쓰는 테이블,
호출 관계, 참조 심볼이 LLM 호출 0회로 결정적으로 채워진다.

**Why this priority**: 이 컨텍스트가 없으면 step6 의미 분석이 입력 자체를 갖지 못한다. step5의
존재 이유이자 MVP. 나머지 모든 시나리오는 이 결정적 골격 위에 얹힌다.

**Independent Test**: Function 목록 + reads/writes/calls/references 엣지를 입력으로 step을
실행하면, function_id 마다 reads/writes/callers/callees/referenced_symbols 가 정렬된 형태로
채워진 AnalysisContext 가 생성됨을 LLM·외부서비스 없이 단독 검증 가능.

**Acceptance Scenarios**:

1. **Given** functions 와 reads_writes/calls/references 엣지가 state 에 있을 때, **When** step5를
   실행하면, **Then** 모든 함수에 대해 reads/writes/callers/callees 가 정렬·중복제거된 tuple 로,
   referenced_symbols 가 함수당 1회씩만 채워진 AnalysisContext 가 생성된다.
2. **Given** strategy 가 config 에 누락된 경우, **When** step5를 실행하면, **Then** 경고만 남기고
   빈 결과로 안전하게 건너뛴다(파이프라인 중단 없음).
3. **Given** strategy 가 Framework 면 **When** augment 가 호출되면 **Then** ctx 는 무변경(항등),
   strategy 가 DBMS 면 **Then** ctx.extra 에 parent_id 와 child_ids(트리 메타)가 채워진다.

---

### User Story 2 - 큰 함수 본문 사전 압축으로 다운스트림 토큰·비용 차단 (Priority: P2)

본문이 큰 함수는 callee 로서 다른 함수 분석에 통째로 노출되면 토큰이 폭증한다(과거 $400 사고
원인). 본문 토큰 추정치가 임계를 넘는 함수만 골라, 작은 모델 LLM 한 번으로 한 줄 요약을 만들어
one_liner 에 박아 둔다. 임계 미만 함수는 LLM 호출이 발생하지 않는다.

**Why this priority**: 정확성보다 비용·안정성 방어책. 골격(P1)이 먼저 있어야 의미 있지만,
다운스트림 토큰 폭증을 막는 핵심 안전장치다.

**Independent Test**: 임계 초과 본문 1개 + 임계 미만 본문 1개를 입력하면, 전자에만 one_liner 가
채워지고 후자에는 LLM 호출이 발생하지 않음을 검증 가능.

**Acceptance Scenarios**:

1. **Given** 본문 토큰이 임계 이상인 함수, **When** step5를 실행하면, **Then** 작은 모델로 한 줄
   요약을 1회 계산해 one_liner 에 채운다.
2. **Given** 본문 토큰이 임계 미만인 함수, **When** step5를 실행하면, **Then** one_liner 는 None
   으로 남고 LLM 호출이 일어나지 않는다.
3. **Given** 한 함수의 압축이 비용 예산 초과(회로 차단기)로 실패하면, **When** 처리 중이면,
   **Then** 즉시 상위로 전파되어 step 전체가 중단된다. 그 외 일반 예외는 해당 함수만 one_liner
   없이 진행(로그 표면화).

---

### User Story 3 - 선택적 테이블 샘플행 사전 수집 (Priority: P3)

step6가 실제 값 기반 시나리오를 만들도록, 분석 전에 catalog 에서 테이블별 실제 샘플행을 한 번에
받아 table_sample_data 에 둔다. catalog 비활성, datasource 미지정, 테이블 0개 중 하나면 조용히
건너뛴다. strategy 와 무관하다.

> 참고: 테이블 샘플 수집의 catalog 계약(엔드포인트·청크·행수 한도)은 [012](../012-catalog-embedding/spec.md)가 소유한다. 본 스펙(FR-013/014/015)은 step5 통합 관점만 기술하며, 계약 변경은 012 에서 한다.

**Why this priority**: 분석 품질 향상용 부가 기능. 없어도 P1·P2 파이프라인은 정상 동작한다.

**Independent Test**: catalog 토글 on + datasource + 테이블이 있을 때 table_sample_data 가
채워지고, 셋 중 하나라도 빠지면 빈 결과로 건너뜀을 검증 가능.

**Acceptance Scenarios**:

1. **Given** catalog 활성 + datasource + 테이블 N개, **When** step을 실행하면, **Then** 테이블을
   청크(100개)로 나눠 병렬로 샘플행(테이블당 최대 5행)을 받아 매칭된 것만 table_sample_data 에 둔다.
2. **Given** ROBO_CATALOG_ENABLED=false 또는 datasource 미지정 또는 테이블 0개, **When** 실행하면,
   **Then** 빈 결과로 graceful 하게 건너뛴다.
3. **Given** 일부 청크 호출이 실패하면, **When** 실행하면, **Then** 해당 청크의 테이블만 샘플
   없이 제외하고(경고 표면화) 나머지는 정상 수집한다.

---

### Edge Cases

- referenced_symbols 의 대상 변수·상수 노드를 state 에서 찾지 못하면 그 줄은 생략된다.
- 한 노드가 여러 초기화 출처를 참조하면 마지막 출처만 표시된다(보조 정보용 1:1 한계 허용).
- 같은 파일을 여러 함수가 공유해도 파일 본문은 1회만 읽어 캐시(토큰 추정·one_liner 공용).
- 토큰 추정은 휴리스틱(영문 4자/토큰, 비-ASCII 약 1.5자/토큰)이며 정확한 토크나이저가 아니다.
- DBMS 트리 메타는 parent_of 엣지로 children 맵을 1회 빌드 후 재사용(함수마다 전체 스캔 회피).
- 테이블 샘플 청크 응답에서 비어 있는 컨텍스트는 table_sample_data 에 보관하지 않는다.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST 모든 Function 에 대해 AnalysisContext 를 생성하고 결과를
  `analysis_contexts`(function_id → AnalysisContext) 로 반환해야 한다.
- **FR-002**: System MUST 식별(function_id, signature)과 위치(file_path, start_line, end_line)를
  함수 메타에서 그대로 채워야 한다.
- **FR-003**: System MUST reads/writes(테이블 fqn), callers/callees(function_id) 를 Linking 엣지
  에서 집계하되 정렬·중복제거된 tuple 로 저장해야 한다(반복 비결정성 차단, 캐시 친화).
- **FR-004**: System MUST referenced_symbols 를 REFERENCES(함수→변수/상수) + INIT_BY(초기화 출처)
  엣지에서 결정론적으로(LLM·Neo4j 조회 0회) 도출하고, 함수당 동일 심볼은 1회만 포함해야 한다.
- **FR-005**: System MUST strategy 의 context_builder 를 통해 strategy 별 컨텍스트를 augment 해야
  하며, Framework 는 항등(ctx 무변경), DBMS 는 ctx.extra 에 parent_id 와 정렬된 child_ids 를
  채워야 한다.
- **FR-006**: System MUST 각 함수 본문의 토큰 수를 추정해 token_count 에 저장해야 한다.
- **FR-007**: System MUST 본문 토큰이 임계(`ROBO_PIPELINE_BIG_CONTEXT_THRESHOLD_TOKENS`, 기본
  1000) 이상인 함수만 골라 작은 모델 LLM(`large_function_one_liner.yaml`, `ROBO_LLM_MODEL_SMALL`)
  으로 한 줄 요약을 계산하고 one_liner 에 저장해야 한다.
- **FR-008**: System MUST 임계 미만 함수에 대해서는 어떤 LLM 호출도 하지 않아야 하며 one_liner 는
  None 으로 남겨야 한다.
- **FR-009**: System MUST one_liner 의 LLM 호출을 동시성 상한(`max_concurrency`) 하에서 병렬로
  수행하되, 첫 1건으로 prefix 캐시를 워밍한 뒤 나머지를 병렬화해야 한다(cold 동시 캐시 미스 방지).
- **FR-010**: System MUST 비용 예산 초과(BudgetExceededError, 회로 차단기)는 즉시 상위로 전파하여
  step 을 중단하고, 그 외 압축 실패는 해당 함수만 one_liner 없이 진행하되 사유를 로깅해야 한다
  (silent return 금지).
- **FR-011**: System MUST summary 슬롯을 None 으로 남겨 후속 단계(step6 Bottom-up DAG)가 채우도록
  해야 한다(step5 는 summary 를 생성하지 않음).
- **FR-012**: System MUST strategy 가 config 에 없으면 경고 후 빈 결과로 안전하게 건너뛰어야 한다.
- **FR-013**: System MUST 선택적 테이블 샘플 수집을 strategy 와 무관하게 제공하고, catalog 비활성
  · datasource 미지정 · 테이블 0개 중 하나면 graceful 하게 건너뛰어야 한다.
- **FR-014**: System MUST 테이블 fqn 을 청크(100개) 단위로 나눠 병렬 호출하고 테이블당 샘플 행 수를
  제한(5행)하며, 청크 실패는 해당 테이블만 제외(경고 표면화)하고 분석을 중단하지 않아야 한다.
- **FR-015**: System MUST 매칭되고 비어 있지 않은 샘플만 `table_sample_data`(fqn → {columns,
  sample_rows}) 로 보관해야 한다.

### Key Entities *(include if feature involves data)*

- **AnalysisContext**: 함수 1개의 누적 분석 컨텍스트(Framework/DBMS 공유 dataclass). 필드 —
  `function_id`, `signature`(식별); `file_path`, `start_line`, `end_line`(위치); `reads`,
  `writes`(테이블 fqn tuple), `callers`, `callees`(function_id tuple); `referenced_symbols`
  (참조 변수/상수 줄 tuple); `one_liner`(큰 함수 사전 압축, 기본 None), `token_count`(본문 토큰
  추정); `summary`(step6 이 채움, 기본 None); `is_in_cycle`(SCC 사이클 메타); `extra`(strategy
  별 자유 dict — DBMS 의 parent_id/child_ids 등). strategy 별 확장은 dataclass 계승이 아니라 extra
  로 흡수.
- **one_liner**: 본문 토큰이 임계 이상인 함수만 작은 모델 LLM 으로 만든 ≤120자(60자 권장) 한 줄
  요약. callee 본문 통째 노출을 차단해 다운스트림 토큰·비용 폭증을 막는다.
- **table_sample_data**: fqn → {columns, sample_rows} 맵. catalog 의 sample-context 응답을
  병합한 것으로, step6 가 ctx.reads/writes 로 골라 쓴다(여기선 전 테이블 수집만).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 입력 Function 100%에 대해 AnalysisContext 가 생성되어 `analysis_contexts` 에 들어간다.
- **SC-002**: 본문 토큰이 임계 미만인 함수에서는 one_liner LLM 호출이 0회 발생한다.
- **SC-003**: 골격(reads/writes/callers/callees/referenced_symbols) 생성은 LLM·외부서비스 호출
  0회로, 같은 입력에 대해 항상 동일한(정렬된) 결과를 낸다.
- **SC-004**: 큰 함수 압축 실패(비예산 사유)나 테이블 샘플 청크 실패가 있어도 step 전체는 중단되지
  않고 부분 결과를 반환한다.
- **SC-005**: catalog 가드(비활성/무 datasource/무 테이블) 충족 시 외부 호출 없이 즉시 건너뛴다.

## Assumptions

- 선행 단계(Function 분해, Linking: reads/writes/calls/references/init_by/parent_of 엣지)가 이미
  state 에 채워져 있다고 가정한다.
- strategy 와 context_builder 는 DI 로 주입되며 모든 strategy 가 context_builder 를 제공한다고
  가정한다(Framework 는 항등 구현이라도 계약 준수).
- summary 와 실제 컨텍스트 텍스트(compact 본문·조상 골격·callee summary)는 step5 가 아니라 step6
  (Bottom-up DAG, three_split_llm_fanout/range_body)이 contexts 라이브 조회로 구성한다.
- 토큰 추정은 정확한 토크나이저가 아닌 휴리스틱으로 충분하다고 본다(임계 판정 보조용).
- 테이블 샘플 수집은 catalog 서비스(`sample-context`)가 가용하고 datasource 가 운영=AnalyzeRequest
  / 테스트=ENV.catalog.test_datasource 로 결정된다고 가정한다.
- 동일 파일을 여러 함수가 공유할 때 본문 캐시(SourceFileCache)로 파일 읽기를 1회로 줄인다.