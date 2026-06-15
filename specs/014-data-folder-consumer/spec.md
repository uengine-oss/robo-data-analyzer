# Feature Specification: Data-Folder-Only Consumer (data/ 단일 소비 · 경로 분기 제거)

**Feature Branch**: `014-data-folder-consumer`

**Created**: 2026-06-15

**Status**: Draft

**Input**: User description: "analyzer를 레거시처럼 data/ 폴더만 바라보는 순수 소비자로 되돌린다. 업로드/경로 분기(project_root)를 전부 제거하고, 입구 구성은 antlr가 단독 책임진다(antlr spec 006). DDL은 data/ddl만 읽어 함수/프로시저가 DDL 파서에 닿지 않게 한다."

## Overview (배경)

analyzer는 원래 표준 `data/` 폴더 하나만 소비했다. 이후 추가된 경로 모드가 `project_root`를 도입했고, 그 값이 있으면 analyzer가 `data/` 대신 로컬 폴더를 **제자리에서** 읽었다. 이 `project_root` 분기는 파이프라인 곳곳(약 8곳: run 진입, DDL 단계의 소스 폴더, 코드 스캔, 결정론적 링킹의 파일 탐색)으로 퍼졌고, 경로 모드에서 DDL 단계가 root 아래 **모든** `.sql`을 긁어 — 저장프로시저 파일까지 — 표 정의 파서에 넣는 바람에, 그 파서가 프로시저 코드를 못 읽어 **run 전체가 중단**됐다.

이 기능은 analyzer를 경로 모드 이전 형태, 즉 **표준 `data/` 폴더의 순수 소비자**로 되돌린다. 경로 모드 기능은 사라지지 않는다 — 그 처리는 전부 파서(antlr 스펙 006 "입구 통일")로 옮겨가, 파서가 항상 `data/{source, ddl, analysis}`를 만든다. 따라서 analyzer는 `project_root`/모드 분기를 전부 버리고 `data/`만 읽으며, 표 정의 단계는 `data/ddl`만(파서가 진짜 표 정의만 담았음을 보장) 읽어 프로시저 코드가 닿을 수 없게 한다.

이는 analyzer 헌법 원칙 II(**Zero-Branch 분기 0**)에 곧바로 부합한다 — 모드 분기 제거는 새 변형이 아니라 단순화다.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - analyzer는 표준 data/ 폴더만 소비한다 (Priority: P1)

분석 run은 소스가 업로드로 왔든 로컬 경로로 왔든, 필요한 모든 것을 표준 `data/` 폴더에서 읽으며 입력 모드를 모른다.

**Why this priority**: 구조적 단순화. 모드별 버그의 원인인 이중 경로를 없애고, 단일·재현 가능한 입력 형태를 복원한다.

**Independent Test**: `data/{source, ddl, analysis}`가 (파서에 의해) 채워진 상태에서, 로컬 경로를 주지 않고 분석을 실행 — 소스는 `data/source`, 표 정의는 `data/ddl`, AST는 `data/analysis`에서 읽고 그래프를 만드는지 확인.

**Acceptance Scenarios**:

1. **Given** 채워진 `data/` 폴더, **When** 경로/모드 인자 없이 run 시작, **Then** 소스·DDL·AST를 오직 `data/`에서 읽고 완료한다.
2. **Given** run 진행, **When** 코드 스캔·링킹 수행, **Then** 모든 파일 탐색이 `data/` 아래에서 해결된다(로컬경로 vs data 분기 없음).
3. **Given** 같은 `data/` 내용으로 두 번 실행, **When** 비교, **Then** 재현 가능하다(외부 경로 의존 없음).

---

### User Story 2 - 프로시저 코드는 표 정의 파서에 닿지 않는다 (Priority: P1)

표 정의(DDL) 단계는 표 정의 세트만 읽으므로, 저장프로시저/함수가 닿지 않고 프로시저가 든 프로젝트도 중단 없이 완료된다.

**Why this priority**: 지금 그래프를 막는 바로 그 실패. `data/ddl`(파서가 표 정의만 담음 보장)만 읽으면 크래시가 근원에서 사라진다.

**Independent Test**: 프로시저/함수를 포함한 프로젝트(파서가 `data/source`로 올바르게 분류해 `data/ddl`엔 표 정의만 남은 상태)로 분석 실행 — DDL 단계가 `data/ddl`만 처리하고 run이 완료돼 그래프가 나오는지 확인.

**Acceptance Scenarios**:

1. **Given** `data/ddl`엔 표 정의만, `data/source`엔 프로시저가 있다, **When** DDL 단계 실행, **Then** `data/ddl`만 읽고 프로시저 코드를 만나지 않는다.
2. **Given** 과거 중단됐던 프로젝트(프로시저가 DDL 파서에 닿았던), **When** 이 기능으로 실행, **Then** 완료되고 그래프를 기록한다.

---

### User Story 3 - 파이프라인 전반의 경로 모드 분기 제거 (Priority: P2)

유지보수자 입장에서, run 진입·DDL 단계·코드 스캔·링킹에 퍼진 `project_root`/경로 모드 조건문을 제거해 입력원을 하나(`data/`)로 남긴다.

**Why this priority**: 유지보수성·버그 예방(Zero-Branch). US1/US2가 들어오면 그래프는 이미 동작하니, 이건 향후 분기 발생을 막는 정리다.

**Independent Test**: 파이프라인에 남은 입력 모드 조건문을 점검 — 동작을 가르는 것이 없고, 요청에 경로 인자가 더 이상 필요 없는지 확인.

**Acceptance Scenarios**:

1. **Given** 분석 요청, **When** 로컬 경로 인자를 빼고 호출, **Then** 동작이 동일하다(경로 인자는 더 이상 소스를 바꾸지 못함).
2. **Given** 파이프라인 코드, **When** 검토, **Then** "data 폴더 vs 로컬 경로"로 분기하는 단계가 없다.

---

### Edge Cases

- **`data/`가 비었거나 기대 하위폴더 없음**: 기존 no-silent-failure 정책대로 명시적 오류(필수 입력 부재가 단계를 무효화).
- **`data/ddl`이 비었음(표 정의 없음)**: run 진행 — 스키마 부분만 비고, 나머지 분석(`data/analysis` AST 기반)은 정상 동작.
- **옛 클라이언트가 경로 인자를 여전히 보냄**: 무시(소스를 바꾸지 않음). run은 무조건 `data/` 사용.
- **재실행**: 여전히 파괴적 전체 재빌드(전량 wipe 후 rebuild), 불변.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: analyzer는 소스 파일을 `data/source`, 표 정의를 `data/ddl`, AST를 `data/analysis`에서 읽어야 한다 — 표준 `data/` 폴더만.
- **FR-002**: analyzer는 외부/로컬 폴더를 제자리에서 읽어서는 안 된다; 전달된 경로 인자가 analyzer가 읽는 소스를 바꿔서는 안 된다.
- **FR-003**: 표 정의(DDL) 단계는 `data/ddl`만(전체 root 아님) 읽어야 하며, 파서가 진짜 표 정의만 거기 두었음에 의존한다.
- **FR-004**: 파이프라인 전반의 입력 모드/경로 조건문(run 진입·DDL 단계·코드 스캔·결정론적 링킹)을 제거해 입력원을 하나로 만들어야 한다.
- **FR-005**: 입구 이후의 기존 분석 동작(결정론적 링킹, 분석 DAG, LLM 단계, 컴포넌트 군집, 그래프 upsert)은 불변이어야 한다.
- **FR-006**: run은 여전히 파괴적 전체 재빌드여야 한다(증분 업데이트 없음, 현행 정책 유지).
- **FR-007**: 단계를 무효화하는 `data/` 필수 입력 부재는 명시적 오류를 발생시켜야 한다(기존 no-silent-failure 정책 유지).

### Key Entities *(include if feature involves data)*

- **Standard Data Set(표준 데이터 세트)**: analyzer가 소비하는 유일한 입력 `data/{source, ddl, analysis}`. 파서(antlr 스펙 006)가 생성.
- **DDL Input**: `data/ddl` — 표 정의만. 표 정의 단계의 유일한 입력.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: analyzer가 로컬 경로 인자 없이 `data/`만으로 run을 완료한다.
- **SC-002**: 저장프로시저/함수를 포함한 프로젝트가 전체 run을 완료하고 그래프를 산출한다(기존 중단 사라짐).
- **SC-003**: 어떤 단계도 입력 모드로 분기하지 않는다 — 경로 인자를 주든 안 주든 동작이 동일하다.
- **SC-004**: 같은 `data/` 내용에 대한 분석 산출물이 기존 data 폴더(업로드) 경로와 동일하다 — 즉 그 경우엔 동작 보존 단순화다.

## Assumptions

- **파서가 입구를 책임짐**: `data/{source, ddl, analysis}`는 상류 파서(antlr 스펙 006)가 내용 기반 분류 포함하여 완전·정확하게 만든다. analyzer는 `data/ddl`에 표 정의만 있음을 신뢰한다.
- **레거시 형태 복원**: 경로 모드 이전의 "data/만 보기" 동작으로 되돌린다(현재 진입의 `or data/source` 기본값이 이미 그 형태); 변경은 경로 override와 그 전파의 제거다.
- **문법/로직 불변**: 입력 배선만 바뀐다. 분석 알고리즘과 그래프 스키마는 그대로.

## Dependencies & Cross-Service Impact

- **↑ antlr-code-parser (스펙 006)**: 깨끗한 `data/`(특히 `data/ddl`에 진짜 표 정의만)를 만들어야 한다. 이 기능은 그 보장에 의존한다. **순서: antlr 006이 이 기능보다 먼저/함께 반영.**
- **↑ robo-data-frontend (스펙 014)**: analyzer의 `project_root` 경로 모드에 의존하지 않아야 한다; 범위/입구는 파서로 표현된다. 분석 요청의 경로 인자는 무력화된다.
- **AST 입력 계약 불변**: 여전히 기존 노드 스키마의 `data/analysis`를 소비한다(파서 헌법 VI / analyzer 입력 계약 유지).

### Constitution Impact

- **원칙 II (Zero-Branch)**: 강화 — 모드 분기 제거(새 변형 아닌 단순화). 개정 없음.
- **원칙 IV (No Silent Failure)**: 유지 — analyzer는 진짜 무효/부재 입력에 여전히 raise. 깨진 파일 *격리*(건너뛰고 보고)는 상류 파서(antlr 스펙 006 US4)가 담당하므로, analyzer엔 skip-and-continue가 필요 없고 IV가 그대로 유지된다.
- 원칙 I, III, V–VIII: 영향 없음.

**이 analyzer 기능에는 헌법 개정이 필요 없다.** (내용 분류 개정은 antlr 헌법, 스펙 006에 있음.)
