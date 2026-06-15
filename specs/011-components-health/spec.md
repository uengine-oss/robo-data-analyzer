# Feature Specification: Component Clustering (Leiden) & Health Metrics

**Feature Branch**: `011-components-health`

**Created**: 2026-06-15

**Status**: Backfilled (reverse-engineered)

**Input**: 기존 구현(pipeline step7 post-processing)에서 역설계. "함수 호출 그래프를 Leiden 으로 부품(COMPONENT)으로 군집화해 이름을 붙이고, 함수별 결정론 건강지표를 산출한다."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 호출 군집을 함수 속성(component_id)으로 묶어 보기 (Priority: P1)

레거시 분석가는 수천 개 함수를 개별로 보는 대신, 함께 자주 호출하는 함수들이 어떤 "기능 부품(바운디드 컨텍스트)"으로 묶이는지 보고 싶다. 시스템은 내부 CALLS 엣지만으로 함수를 군집화하고, 각 군집에 사람이 읽을 이름을 붙여, **그 멤버십을 각 FUNCTION 노드의 `component_id`·`component_name` 속성으로 SET** 한다.

> **★ 합성 노드 없음(2026-06-15 개정)**: 군집을 `:COMPONENT` 노드로 materialize 하지 않는다. 멤버십은 **`FUNCTION.component_id` 속성이 단일 진실**이고, 이름·그룹간 관계는 전부 거기서 파생한다 — 시각화(영역 hull)는 뷰에서 `component_id` 기준 오버레이, 그룹↔그룹 호출은 저장하지 않고 `CALLS` + `component_id` 로 온디맨드 GROUP BY. 합성 노드/엣지를 만들면 도달성·중심성·임팩트 분석이 왜곡되므로 금지.

**Why this priority**: 군집 멤버십은 이 기능의 핵심 산출물이다. 군집화만 단독으로 동작해도 "거대 코드베이스를 몇십 개 부품으로 압축해 본다"는 가치를 즉시 제공한다.

**Independent Test**: 함수/호출 그래프가 담긴 state 로 step 실행 → 멤버 함수마다 `FUNCTION.component_id`(+`component_name`)가 SET 되는지 확인(`:COMPONENT` 노드·`HAS_COMPONENT_MEMBER`·`COMPONENT_CALLS` 는 생기지 **않아야** 함). 같은 입력 두 번 → 동일 군집(seed 고정).

**Acceptance Scenarios**:

1. **Given** 내부 CALLS 엣지가 있는 함수 집합, **When** build_components_step 실행, **Then** 멤버 2개 이상인 군집의 모든 멤버 FUNCTION 에 동일 `component_id` 가 SET 되고, **`:COMPONENT` 노드/엣지는 생성되지 않는다**.
2. **Given** 그룹 A 함수가 그룹 B 함수를 내부 호출, **When** 그룹간 호출이 필요, **Then** 저장된 엣지가 아니라 `CALLS` + 양끝 `component_id` 를 GROUP BY 해 온디맨드로 집계한다.
3. **Given** 동일 그래프 재분석, **When** 두 번 실행(seed=42), **Then** 동일한 `component_id`·멤버 구성이 나온다(결정론).

---

### User Story 2 - 함수별 위험 건강지표 산출 (Priority: P1)

분석가는 어떤 함수가 위험한지(거대 함수, 순환 의존, 허브, 데이터 과결합) 한눈에 보고 싶다. 시스템은 LLM 없이 결정론적으로 함수별 지표를 계산해 Neo4j FUNCTION 노드 속성으로 SET 하고, 요약 리포트(health_report.json)를 남긴다.

**Why this priority**: 위험 지표는 군집과 독립적으로 가치를 준다(군집 없이도 함수별 risk 만으로 리팩토링 우선순위 판단 가능). LLM 0 이라 빠르고 비용이 들지 않는다.

**Independent Test**: state 그래프만으로 step 실행 → 각 함수에 fan_in/fan_out/loc/in_cycle/table_coupling/god_flag/risk 7개 속성이 SET 되는지, health_report.json 이 생성되는지 확인.

**Acceptance Scenarios**:

1. **Given** LOC 300 이상이며 fan_out 15 이상인 함수, **When** 지표 계산, **Then** god_flag=true 이고 risk=high 가 된다.
2. **Given** 순환 호출(SCC)에 포함된 함수, **When** 계산, **Then** in_cycle=true 이고 risk=high 가 된다.
3. **Given** neo_client 미주입, **When** step 실행, **Then** Neo4j SET 은 skip 하되 계산·health_report.json 은 정상 산출된다.

---

### User Story 3 - 부품 이름을 LLM 으로 자동 명명 (Priority: P2)

군집된 부품은 식별자가 아닌 업무 도메인 이름이 필요하다. 시스템은 부품마다 멤버 함수의 핵심 한 줄(headline)만 모아 component_naming LLM 을 1회 호출해 이름/설명/stereotype 을 도출한다.

**Why this priority**: 이름 없이도 군집 구조는 보이지만(P1), 도메인 이름이 붙어야 비개발자도 부품을 이해할 수 있다. LLM 의존이라 P1 보다 후순위.

**Independent Test**: 군집 1개에 대해 component_naming 룰 실행 → name/description/stereotype 3필드가 채워진 Component 가 나오는지 확인. LLM 실패 시 대표 함수 headline 으로 fallback 되는지 확인.

**Acceptance Scenarios**:

1. **Given** 멤버 함수 headline 들, **When** component_naming LLM 1회 호출, **Then** name/description/stereotype 이 채워진다.
2. **Given** 한 부품 명명 중 LLM 예외, **When** fail 정책 적용, **Then** 해당 부품만 대표 함수 headline 을 name 으로 쓰는 fallback Component 가 되고 나머지는 정상 진행된다.

### Edge Cases

- 내부 호출 엣지가 0개이면? → 군집 없음으로 step skip(부품 미생성).
- 군집 멤버가 1개(고립 함수)이면? → MIN_COMPONENT_SIZE=2 미만이라 노이즈로 폐기, 부품 X.
- leidenalg/igraph 미설치이면? → 군집화 시점 lazy import 실패 → 설치 안내 메시지와 함께 명시 raise(흐름 중단).
- LLM 비용 예산 초과(BudgetExceededError)이면? → fallback 하지 않고 그대로 전파(전체 중단).
- 이름 입력 멤버가 NAME_MAX_MEMBERS(40)를 넘으면? → 40줄까지만 넣고 "(외 N개 함수)" 로 축약(토큰 안전).
- start/end_line 누락·역전이면? → loc=0 으로 안전 처리.
- external(분석 범위 밖) 호출이면? → 차수·순환·군집 계산에서 모두 제외(internal 만 사용).
- health_report.json 파일 쓰기 실패이면? → 사유 로그 후 skip, 계산·Neo4j SET 은 막지 않음(graceful).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: 시스템은 state 의 `functions` 와 `calls_edges` 중 scope=internal 이며 양끝이 분석 대상이고 self-loop 가 아닌 호출만으로 무방향 그래프를 구성해야 한다.
- **FR-002**: 시스템은 leidenalg/igraph 의 Leiden(ModularityVertexPartition)으로 함수를 군집화하며, seed=42 고정으로 동일 그래프 → 동일 군집(결정론)을 보장해야 한다.
- **FR-003**: 시스템은 leidenalg/igraph 를 군집화 시점에 lazy import 하고, 미설치 시 설치 안내 메시지와 함께 명시적으로 예외를 raise 해야 한다.
- **FR-004**: 시스템은 멤버 수가 MIN_COMPONENT_SIZE(2) 미만인 군집을 폐기해야 한다.
- **FR-005**: component_id 는 멤버를 정렬한 첫 function_id 기반(`COMPONENT:{first}`)으로, 재분석 시 안정적인 키여야 한다.
- **FR-006**: 시스템은 부품마다 멤버 함수의 headline(logical_name 우선, 없으면 summary 의 [핵심] 첫 줄)을 최대 40개 모아 component_naming LLM 을 1회 호출해 name/description/stereotype 을 도출해야 한다.
- **FR-007**: 부품 명명은 병렬(공유 Semaphore + gather_with_warmup, return_exceptions)로 수행하며, 개별 명명 실패 시 대표 함수 headline 을 name 으로 쓰는 fallback Component 를 만들어야 한다. 단, BudgetExceededError 는 fallback 하지 않고 전파해야 한다.
- **FR-008**: 시스템은 멤버 함수의 internal 호출을 부품 단위로 롤업해, 서로 다른 부품 간 호출 쌍 개수(count)를 가진 `COMPONENT_CALLS` 엣지를 산출해야 한다.
- **FR-009**: 시스템은 `COMPONENT` 노드 → `HAS_COMPONENT_MEMBER` → `COMPONENT_CALLS` 순서로 UPSERT(MERGE) 하여 엣지 양끝 노드가 먼저 적재됨을 보장해야 한다.
- **FR-010**: 시스템은 함수마다 fan_in/fan_out(internal in/out 차수, distinct), loc(end-start+1), in_cycle(SCC 재사용 find_call_cycles), table_coupling(distinct 테이블 수), god_flag, risk 를 결정론적으로(LLM 0) 계산해야 한다.
- **FR-011**: god_flag 는 loc≥300(GOD_LOC_THRESHOLD) 그리고 fan_out≥15(GOD_FANOUT_THRESHOLD)일 때만 true 여야 한다.
- **FR-012**: risk 는 in_cycle 또는 god_flag 이면 high; 그 외 loc≥300 또는 fan_out≥15 또는 fan_in≥20 또는 table_coupling≥5 중 하나라도 충족하면 medium; 아니면 low 여야 한다.
- **FR-013**: 시스템은 계산한 건강지표를 Neo4j FUNCTION 노드 속성(fan_in/fan_out/loc/in_cycle/table_coupling/god_flag/risk)으로 배치 SET 해야 하며, neo_client 미주입 시 SET 만 skip 하고 계산·리포트는 수행해야 한다.
- **FR-014**: 시스템은 risk 분포·in_cycle 수·god 함수 수·임계값과, high-risk 함수 중 (fan_in, loc) 내림차순 상위 20개를 담은 health_report.json 을 산출해야 하며, 파일 쓰기 실패는 흐름을 막지 않아야 한다.
- **FR-015**: 두 step 은 본 분석 로직(step1~6·기존 step7)을 수정하지 않고 state 만 읽어 계산하며, health_metrics_step 은 state 도 변경하지 않아야 한다(Neo4j 가 단일 진실).

### Key Entities *(include if feature involves data)*

- **COMPONENT (노드)**: 함수 호출 군집 = 기능 부품. 키 component_id, 속성 name/description/stereotype/size. Module/Package 와 다른 축(호출 밀도 기반).
- **HAS_COMPONENT_MEMBER (엣지)**: Component → Function. 부품이 멤버 함수를 품는 관계.
- **COMPONENT_CALLS (엣지)**: Component → Component. 멤버 간 internal 호출을 부품 단위로 롤업한 관계, count(호출 쌍 개수) 부속.
- **건강지표 (FUNCTION 속성)**: fan_in/fan_out(내부 호출 in/out 차수), loc(코드 규모), in_cycle(순환 포함 여부), table_coupling(R/W distinct 테이블 수), god_flag(거대 함수 후보), risk(low/medium/high 종합 라벨).
- **health_report.json**: 보조 산출물. total_functions, risk 분포(high/medium/low), in_cycle 수, god_functions 수, top_risky(상위 20), thresholds 를 담는다. 경로 `logs/analysis.run/latest/health_report.json`.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 동일 입력 그래프를 두 번 분석하면 component_id·멤버 구성·각 함수 지표가 100% 동일하다(결정론).
- **SC-002**: 멤버 1개짜리 고립 군집은 부품으로 생성되지 않는다(0건).
- **SC-003**: 부품당 LLM 호출은 정확히 1회이며(병렬), 건강지표 계산의 LLM 호출은 0회다.
- **SC-004**: LOC≥300 & fan_out≥15 인 함수는 모두 god_flag=true·risk=high 로 분류된다.
- **SC-005**: health_report.json 의 risk 분포 합(high+medium+low)이 total_functions 와 일치하고, top_risky 는 최대 20개·전부 high 다.
- **SC-006**: leidenalg/igraph 미설치 환경에서 군집화 시도 시 설치 안내가 담긴 예외로 즉시 중단된다.

## Assumptions

- 선행 step(robo 호출그래프 분석)이 state 에 `functions`·`calls_edges`·`reads_writes_edges` 를 채워 둔다(이 기능은 신규 추출 X, 읽기만).
- 호출 scope 는 internal/external 로 구분되며, 분석 범위 밖(external) 호출은 차수·순환·군집·롤업 전반에서 제외한다.
- 순환 판정은 step6 의 find_call_cycles(Tarjan SCC)를 재사용하며 internal 엣지만 입력한다.
- 두 step 은 step7 post-processing 에서 group 이후 병렬로 실행되며 서로 독립적이다.
- 임계값(LOC 300, fan_out 15, fan_in 20, table 5)·군집 파라미터(min size 2, name max 40, seed 42)는 레거시 규모에 맞춘 튜닝 가능한 상수다.
- Neo4j 가 단일 진실 저장소이고 health_report.json 은 사람·UI 용 보조 산출물이다.
- leidenalg>=0.10 / igraph>=0.11 이 requirements.txt 에 반영되어 있다.
