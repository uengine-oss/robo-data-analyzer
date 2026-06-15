# Feature Specification: Post-Processing — Module & Table Summaries

**Feature Branch**: `010-postprocessing-summaries`

**Created**: 2026-06-15

**Status**: Backfilled (reverse-engineered)

**Input**: 기존 구현(pipeline step7)에서 역설계. 멤버 함수 분석 결과를 모듈/테이블 단위로 종합해 지식그래프를 보강하는 후처리 단계.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 모듈 차원 요약 종합 (Priority: P1)

함수 단위 summary 만으로는 "이 클래스/패키지/소스파일이 전체적으로 무슨 일을 하는가"를 한눈에 알 수 없다. 이 단계는 각 모듈에 속한 멤버 함수들의 summary 를 종합해 모듈 차원의 요약(summary), 논리명(logical_name), 역할 분류(stereotype)를 도출하고 지식그래프의 MODULE 노드에 채운다.

**Why this priority**: 모듈 요약은 그래프 탐색/카탈로그 화면의 1차 진입점이며, 함수 단위 정보만으로는 비개발자가 코드 구조를 이해할 수 없다. step7 의 가장 핵심 산출물.

**Independent Test**: 멤버 함수 summary 가 채워진 모듈 집합을 입력하면, 각 MODULE 노드에 summary/stereotype/logical_name 이 채워지는지 검증. 테이블 보강 없이도 단독으로 가치 제공.

**Acceptance Scenarios**:

1. **Given** 한 파일 안에 라인 범위가 명확한 모듈과 그 범위 안에 속한 함수들, **When** step7 모듈 요약 실행, **Then** 함수들이 file_path + 라인 범위로 해당 모듈에 매칭되고 모듈당 1회 module_summary LLM 호출로 summary/stereotype/logical_name 이 채워진다.
2. **Given** 어느 모듈 라인 범위에도 속하지 않는 함수, **When** 매칭 수행, **Then** 그 파일의 file-level 모듈(start_line=0, end_line=0)로 fallback 매칭된다.
3. **Given** 일부 모듈의 LLM 호출 실패, **When** fan-out 처리, **Then** 해당 모듈은 원본 그대로 유지(로그 남김)되고 나머지 모듈 요약은 계속 진행된다.

---

### User Story 2 - 테이블/컬럼 분석 설명 보강 (Priority: P2)

step6 함수 분석에서 함수마다 누적된 table_insights(테이블 접근 role + 컬럼 의미)를 테이블 단위로 모아, 여러 함수 관점을 종합한 비즈니스 description 을 생성해 TABLE/COLUMN 의 analyzed_description 에 채운다. 이 값은 DDL/카탈로그에서 온 1차 description 과 별도 필드로 격리한다.

**Why this priority**: 테이블의 "실사용 용도"는 DDL 만으로는 알 수 없고 코드가 그 테이블을 어떻게 쓰는지에서 나온다. 1차 description 을 덮어쓰지 않으므로 안전하게 추가 가치를 제공.

**Independent Test**: table_insights 가 채워진 함수 집합을 입력하면, 테이블별 analyzed_description 과 컬럼별 analyzed_description 이 채워지고 기존 description 은 변경되지 않는지 검증.

**Acceptance Scenarios**:

1. **Given** 여러 함수가 같은 table_fqn 을 접근(서로 다른 role/columns), **When** step7 테이블 보강 실행, **Then** 테이블당 후보들이 누적되어 테이블당 1회 table_description LLM 호출로 통합 description 이 생성된다.
2. **Given** 같은 컬럼이 여러 함수 insight 에서 등장, **When** 컬럼 설명 수집, **Then** 컬럼당 가장 먼저 등장한(명확한) 설명 1개만 채택된다.
3. **Given** 생성된 analyzed_description, **When** Neo4j 업데이트, **Then** TABLE/COLUMN 의 analyzed_description 만 SET 되고 기존 description(DDL/카탈로그 1차)은 건드리지 않는다.

---

### User Story 3 - 프레임워크 / DBMS 전략 분기 (Priority: P3)

대상 코드가 OOP 프레임워크(Java/C 등)인지 PL/SQL DBMS 패키지인지에 따라 전략별 프롬프트 디렉터리(prompts/framework vs prompts/dbms)를 사용해 같은 종합 로직을 다른 도메인 어휘/stereotype 풀로 수행한다.

**Why this priority**: 핵심 종합 흐름은 동일하며, 전략별 프롬프트 선택은 품질 보정. 단독으로는 사용자 가치가 작지만 멀티 도메인 지원에 필요.

**Independent Test**: strategy 의 prompts_dir 를 framework/dbms 로 바꿔 실행하면 각 디렉터리의 module_summary/table_description 규칙이 적용되는지 검증.

**Acceptance Scenarios**:

1. **Given** dbms 전략, **When** 모듈 요약 실행, **Then** 패키지/프로시저 어휘와 DBMS stereotype 풀(BatchProcessing/MetadataService 등)이 적용된다.

---

### Edge Cases

- modules 또는 functions 가 비어 있으면 단계 전체를 skip 한다.
- strategy 가 config 에 없으면 경고 로그 후 단계 skip.
- table_insights 가 하나도 누적되지 않으면 테이블 보강 skip.
- 한 파일에 여러 모듈(inner class/중첩)이 있을 때 함수는 가장 좁은 라인 범위 모듈에 우선 매칭된다.
- 멤버 함수가 0개인 모듈은 LLM 호출 없이 원본 그대로 유지된다.
- 예산 초과(BudgetExceededError)는 fail 정책에서 즉시 상위로 전파(회로 차단), 그 외 LLM 예외는 흐름 유지.
- 매칭/업데이트되지 않은 노드도 전체 리스트로 그대로 반환해 다음 step·artifact 가 노드를 잃지 않게 한다.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST 각 Function 을 file_path 일치 + start_line 이 모듈 라인 범위에 포함되는지로 Module 에 매칭해야 한다.
- **FR-002**: System MUST 한 파일에 여러 모듈이 있을 때 가장 좁은 라인 범위의 모듈을 우선 매칭해야 한다(inner class/중첩 모듈 정확 처리).
- **FR-003**: System MUST 어느 라인 범위에도 매칭되지 않은 함수를 해당 파일의 file-level 모듈(start_line=0, end_line=0)로 fallback 매칭해야 한다.
- **FR-004**: System MUST 멤버 함수가 있는 각 모듈마다 멤버 summary 를 종합해 모듈당 1회 module_summary LLM 을 호출해야 한다.
- **FR-005**: System MUST 모듈 요약 결과로 MODULE 의 summary / stereotype / logical_name 만 갱신하고 sub_label/comment/annotations 등 원본 필드는 보존(replace)해야 한다.
- **FR-006**: System MUST 함수별 table_insights(table, role, columns)를 table_fqn 단위로 누적해야 한다.
- **FR-007**: System MUST 누적된 테이블 후보를 종합해 테이블당 1회 table_description LLM 을 호출해 통합 description 을 생성해야 한다.
- **FR-008**: System MUST 컬럼별 설명은 처음 등장한 값 1개만 채택(setdefault)해야 한다.
- **FR-009**: System MUST 생성된 설명을 TABLE.analyzed_description / COLUMN.analyzed_description 에만 기록하고 DDL/카탈로그 1차 description 과 격리해야 한다.
- **FR-010**: System MUST 개별 모듈/테이블 LLM 실패 시 흐름을 유지(모듈은 원본 반환, 테이블은 결과 drop)하고 로그를 남겨야 한다.
- **FR-011**: System MUST 예산 초과(BudgetExceededError)는 즉시 상위로 전파해야 한다.
- **FR-012**: System MUST 첫 1건으로 prefix 캐시를 워밍한 뒤 나머지를 max_concurrency 세마포어로 병렬 처리해야 한다.
- **FR-013**: System MUST 매칭/업데이트되지 않은 노드를 포함한 전체 modules/tables/columns 리스트를 state 로 반환해야 한다.
- **FR-014**: System MUST 대상 도메인에 따라 전략별 프롬프트 디렉터리(framework/dbms)를 사용해야 한다.

### Key Entities *(include if feature involves data)*

- **MODULE.summary**: 멤버 함수 summary 를 종합한 모듈 차원 요약. `[핵심]` 한 줄 + 비즈니스 흐름 내용(코드 토큰 노출 금지).
- **MODULE.stereotype**: 멤버 책임 종합 기준의 역할 분류. framework 풀(Controller/Service/Repository 등) 또는 dbms 풀(BatchProcessing/MetadataService 등).
- **MODULE.logical_name**: 모듈의 비즈니스 목적 한국어 한 줄(멤버 종합에서 도출).
- **TABLE.analyzed_description**: 여러 함수의 접근 role 을 종합한 테이블 실사용 용도 설명(≤100자). 기존 description 과 별도 필드.
- **COLUMN.analyzed_description**: 함수 insight 에서 도출된 컬럼 비즈니스 의미. 컬럼당 1개. 기존 description 과 별도 필드.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 멤버 함수를 가진 모든 모듈의 MODULE 노드에 summary/stereotype/logical_name 이 채워진다(LLM 실패분 제외).
- **SC-002**: file_path + 라인 범위 매칭으로 inner class/중첩 모듈의 함수가 올바른 모듈에 귀속되고, 미매칭 함수는 file-level 모듈로 100% 흡수된다.
- **SC-003**: table_insights 가 누적된 모든 테이블에 대해 테이블당 정확히 1회 table_description LLM 이 호출된다.
- **SC-004**: analyzed_description 기록 후 기존 description(DDL/카탈로그)은 0건 변경된다.
- **SC-005**: 개별 모듈/테이블 LLM 실패가 발생해도 단계 전체가 중단되지 않고 나머지 산출물이 정상 기록된다.

## Assumptions

- step6(함수 분석)이 선행되어 Function.summary 와 Function.extra["table_insights"] 가 채워져 있다.
- modules/functions 노드에 file_path, start_line, end_line 이 신뢰 가능하게 채워져 있다.
- Neo4j 클라이언트가 config 에 주입되어 있으면 업서트가 수행되고, 없으면 state 갱신만 한다(테스트/드라이런 허용).
- 전략(strategy)과 prompts_dir 는 상위 워크플로가 도메인에 맞게 주입한다.
- table_fqn 은 `db.schema.name`, 컬럼 fqn 은 `table_fqn.column_name` 규칙을 따른다.