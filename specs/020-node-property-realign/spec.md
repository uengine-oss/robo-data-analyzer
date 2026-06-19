# Feature Specification: 노드 속성 정체화 — 종류에 맞는 속성만, 빈값·중복·잡속성 제거

**Feature Branch**: `020-node-property-realign`

**Created**: 2026-06-19

**Status**: Draft

**Input**: 통일 dataclass + 일괄 SET 탓에 모든 코드노드가 같은 속성 bag을 받아, 구문(SELECT/IF) 같은 노드에 `annotations`·`params`·`return_type`·`signature`·`loc`·`god_flag`·`fan_in` 등 빈값/무의미 속성이 잔뜩 박힌다. 라벨 정체화(019)에 이은 **속성 레벨 정체화**.

> **설계 원칙(확정)**: 그래프(노드·라벨·엣지)가 진실. 노드 속성 = ① 정체성/위치 ② 표시용 원문 ③ 그래프로 못 구하는 분석결과 — 이 셋만. 그 외(파생가능·중복·빈값)는 저장하지 않는다. 핵심 규칙 = **"빈값은 저장하지 않는다"**(`SET n += {비어있지 않은 키만}`) → 종류별 하드 분기 거의 없이 종류에 맞는 속성만 남는다.

## 개요 *(맥락)*

분석기는 코드를 그래프(노드+속성+관계)로 만들고 화면·카탈로그가 소비한다. 현재 모든 코드노드가 단일 `Function` dataclass + 일괄 SET로 **같은 속성 셋**을 받아, DBMS 구문처럼 그 속성이 의미 없는 노드에도 빈값/잡속성이 붙는다(사용자가 노드패널에서 직접 목격). 본 기능은 **노드가 자기 종류에 의미있는 속성만 갖도록** 정체화하고, 중복·파생가능·잡음 속성을 제거한다. 019(라벨)·018(관계)·017(식별자)와 같은 방향의 부채 정리.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 노드가 "자기 종류에 맞는 속성만" 갖는다 (Priority: P1)

사용자가 노드패널·그래프에서 노드를 볼 때, **그 종류에 의미있는 속성만** 보인다. 구문(SELECT/IF) 노드엔 `annotations`/`params`/`return_type`/`signature`/`loc`/`god_flag`/`fan_in` 같은 빈값·무의미 속성이 더 이상 붙지 않는다.

**Why this priority**: 비개발자가 노드를 이해하려면 잡음이 없어야 한다. 현재 구문 노드에 빈 framework/health 속성이 잔뜩 떠 "이게 뭐지?" 혼란을 준다(사용자 직접 지적).

**Independent Test**: DBMS 재분석 후 구문 노드의 속성을 덤프해, framework/health 잡속성(annotations·params·return_type·signature·loc·god_flag·fan_in·fan_out·table_coupling)이 **0건**인지 확인. 루틴 노드엔 params·return_type·risk_level이 정상 존재.

**Acceptance Scenarios**:

1. **Given** DBMS SELECT 구문 노드, **When** 속성을 보면, **Then** 정체성/위치·원문(code_text)·분석(summary·stereotype·logical_name·notes)만 있고 framework/health 속성은 없다.
2. **Given** 프로시저(루틴) 노드, **When** 속성을 보면, **Then** params·return_type·risk_level이 있다(빈값이면 그 키 자체가 없다).
3. **Given** 변경 후, **When** 노드패널을 보면, **Then** 종류별 의미있는 정보만 표시되고 동작 회귀가 없다.

---

### User Story 2 - 분석결과가 "그래프 진실 위치"에 산다 (Priority: P2)

테이블/컬럼 사용 인사이트가 **컬럼 노드와 R/W 관계** 라는 제자리에 저장되고, 코드노드에 박히던 중복 JSON 블롭(`table_insights_json`)은 사라진다.

**Why this priority**: 같은 정보를 노드 블롭과 그래프 양쪽에 두면 진실이 둘이 된다(드리프트). 컬럼별 인사이트는 컬럼 노드에 있어야 소비처가 깔끔히 읽는다.

**Independent Test**: 재분석 후 ① `table_insights_json` 블롭이 노드에서 0건 ② 컬럼 노드의 `analyzed_description`이 채워져 인사이트 유실 0 ③ R/W 관계로 테이블 접근이 그대로 표현됨.

**Acceptance Scenarios**:

1. **Given** 테이블을 읽는 구문, **When** 분석 후, **Then** 컬럼 노드에 분석 설명이 있고 코드노드엔 JSON 블롭이 없다.
2. **Given** 분배 중 매칭 실패(컬럼 못 찾음), **When** 발생하면, **Then** silent 무시가 아니라 명시 로깅(silent fail 금지).

---

### User Story 3 - 속성 용어가 명확하다 (Priority: P2)

부가 텍스트는 `notes` 하나로 통합(주석+어노테이션), 호출 지표 이름은 의미가 드러나게, 불필요 지표(loc·god_flag·table_coupling)는 제거된다.

**Why this priority**: framework 전용 `annotations`가 DBMS에 빈값으로 남고, `fan_in`/`god_flag` 등 이름이 비직관적이라 가독성 부채.

**Independent Test**: 속성 키에서 `annotations`/`signature`/`loc`/`god_flag`/`table_coupling`/`fan_in`/`fan_out` 잔재 0, `notes` 통합 존재, 호출 지표는 명확한 이름.

**Acceptance Scenarios**:

1. **Given** 노드, **When** 부가 텍스트를 보면, **Then** 주석·어노테이션이 `notes` 하나로 통합돼 있다.
2. **Given** 위험도, **When** 계산을 보면, **Then** 입력 지표(loc·fan 등)는 계산에만 쓰이고 저장은 `risk_level`(+`in_call_cycle`) 최종 판정만 — 위험도 품질은 변경 전과 동일.

### Edge Cases

- **루틴인데 파라미터 없음**: params 빈값 → 그 키 저장 안 함(노드에 없음).
- **table_insights 분배 시 컬럼 매칭 실패**: 명시 로깅(silent 금지), 인사이트 유실 표면화.
- **혼합/엣지**: 빈값 미저장 규칙이 종류 무관 일관 적용 — 종류별 하드 분기 최소.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: 노드는 **자기 종류에 의미있는 속성만** 가져야 한다. 저장 규칙 = **빈값 미저장**(`SET n += {비어있지 않은 키만}`). 종류별 하드 분기는 최소(예외: health는 루틴에만).
- **FR-002**: `signature`를 **Neo4j 노드 속성으로 저장하지 않는다**(루틴은 `params`·`return_type` granular 저장이 대체). ★단 `signature`/`comment`/`annotations` **dataclass 필드는 유지** — call_linker 호출매칭·LLM 컨텍스트·module_pre_analysis 등 파이프라인이 사용하므로 제거 금지(persistence-only). 빈값 미저장 규칙상 구문·그릇 노드는 params/return_type를 자연히 갖지 않는다.
- **FR-003**: framework 전용 `annotations`와 `comment`를 **범용 단일 필드 `notes`로 통합**한다(DBMS 빈 annotation 문제 해소).
- **FR-004**: `loc`·`god_flag`·`table_coupling` 저장을 폐기한다. `fan_in`·`fan_out`은 저장하지 않고 **CALLS 관계로 즉석 파생**한다. 위험 지표는 **전 신호로 계산하되 최종 `risk_level`(+`in_call_cycle`)만 루틴에 저장** — 위험도 품질은 변경 전과 동일(계산 입력 불변).
- **FR-005**: `table_insights_json` 블롭(코드노드)은 **폐기**한다 — 분석 인사이트는 이미 `enrich_table_description_step`이 **Table/Column `analyzed_description`** 으로 분배(제자리)하므로 블롭은 중복이다. (블롭이 보유하던 "함수별 컬럼 사용" 디테일이 향후 필요하면 R/W 관계 속성으로 — 현재 범위 밖.) 분배 커버리지(인사이트 있는 컬럼은 채워짐)를 재분석으로 확인한다.
- **FR-006**: 소비처(노드패널·그래프 색)는 변경(이름·삭제 필드·`notes`·파생 지표)에 맞춰 **전수 정합**한다(회귀 0). 빈/무의미 필드는 표시하지 않는다.
- **FR-007**: 017 식별자(`id`)·018 관계·019 라벨 정체화 결과는 **유지**한다(되돌리지 않음).
- **FR-008**: 매 분석은 기존 정책대로 **파괴적 전체 재빌드**로 동작하며 본 변경이 이를 바꾸지 않는다.

### Key Entities *(그래프 = 노드 + 속성 + 관계)*

- **코드 노드 속성(종류별)**: 공통(id·name·file_path·start/end_line·code_text·summary·stereotype·logical_name·notes) + 루틴 한정(params·return_type·risk_level·in_call_cycle).
- **컬럼 분석설명**: 컬럼 노드 `analyzed_description` — table_insights에서 분배된 사용 인사이트의 제자리.
- **위험 판정**: `risk_level` — 여러 지표를 계산해 도출한 최종 라벨(저장은 이것만).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 재분석 후 구문 노드에 framework/health 잡속성(annotations·params·return_type·signature·loc·god_flag·fan_in·fan_out·table_coupling)이 **0건**.
- **SC-002**: 컬럼 노드 `analyzed_description`이 채워져 `table_insights_json` 블롭 제거 후에도 컬럼 인사이트 유실 **0**.
- **SC-003**: `risk_level` 결과가 변경 전과 **동일**(위험도 품질 회귀 0).
- **SC-004**: 노드패널·그래프가 종류별 의미있는 정보만 표시하며 소비처 동작 회귀 **0건**.
- **SC-005**: 속성 키에서 `signature`·`annotations`·`loc`·`god_flag`·`table_coupling`·`fan_in`·`fan_out` 잔재 **0**, `notes` 통합 존재.

## Assumptions

- 대상 = `robo-data-analyzer`(생산자) + 소비처 `robo-data-frontend`(노드패널·그래프). catalog는 라벨 무관 통과(읽기 무수정) 가정, 변경 시 점검.
- 019 라벨 정체화 완료본 위에 올린다(017·018·019 결과 유지).
- 매 분석은 파괴적 전체 재빌드.
- 위험도 계산 입력(loc·fan·table_coupling·cycle 등)은 **메모리 계산용으로 유지**, 저장만 폐기 — 위험도 의미 불변.
- 검증은 단위테스트가 아니라 실제 파이프라인 재분석 후 노드 속성을 원문과 대조. 분석 실행·커밋은 사용자 OK 후.
