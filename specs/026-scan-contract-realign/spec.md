# Feature Specification: Scan-Agent 계약 명칭 정합 (framework_functions drift 수정)

**Feature Branch**: `refactor` (analyzer 고정 — 별도 브랜치 없음, specs/ 누적)

**Created**: 2026-06-26

**Status**: Draft

**Input**: User description: "스캔 에이전트 계약 명칭 정합 — refactor 17922c3 가 ScanResult 필드를 framework_functions → framework_code_nodes 로 리네임했으나 에이전트 지시 문서(시스템 프롬프트·스킬·템플릿)를 안 고쳐, 스캔 에이전트가 드롭 대상(프레임워크/런타임 함수)이 아닌 프로젝트 자기 함수를 그 필드에 채워 호출 필터링이 깨짐."

## Clarifications

### Session 2026-06-26

- Q: 드롭 목록 필드명 모호성(`framework_code_nodes` ↔ 프로젝트 `code_nodes`) 해소 방식 → **A: (b) 필드 리네임(근본).** 필드를 **`framework_runtime_functions`** 로 리네임하고 코드+문서 일괄 정합. "code_nodes" 모호성 자체를 제거(헌법 III 단일진실 + LLM 오인 재발 근절). 변경면: 코드 5(agent_output.py·scan_agent.py·call_classifier.py·link_calls_by_name_step.py·잔여패턴 경로) + 문서 4.
- Q: FR-003 가드(드롭목록 ∩ 프로젝트 code_nodes 표면화) 이번 범위 포함 여부 → **A: 포함.** 헌법 IV 안전망 — 명칭을 고쳐도 LLM 재오작동 시 조용한 업무함수 드롭을 경고/에러로 표면화.

## 배경 (사실관계 — 라이브/git 실측)

- 커밋 `17922c3` (specs 018~022, 2026-06-19) 가 에이전트 출력 모델 `ScanResult` 의 필드를 **`framework_functions` → `framework_code_nodes`** 로 리네임. 의미(="호출 분류 시 드롭할 프레임워크/런타임 함수 목록")는 동일.
- 그러나 그 필드를 채우도록 에이전트에게 지시하는 문서 4파일 8곳이 **옛 이름 `framework_functions` 그대로** 남음 (2026-06-26 전수조사 확정):
  - `prompts/agents/scan_agent_system.md` (57·60·69·121 — **스캔 에이전트 시스템 프롬프트**, 필드표 포함)
  - `prompts/agents/residual_pattern_system.md` (28)
  - `skills/proframe-c-pfm-dbio/SKILL.md` (70·88)
  - `skills/_templates/SKILL.md` (44 — 향후 스킬 템플릿)
- 스킬 자체는 2026-05-29(`4e54f4f`) 이후 안 바뀜 → 리네임 이전(6/19 전)엔 이름이 일치해 **정상 동작**했음.
- 결과(2026-06-26 framework 분석 라이브 run): 스캔 에이전트가 `framework_code_nodes` 에 **프로젝트 업무 함수 37개**(a000_*, b000_*…) 를 채움 → 호출 분류기가 그 함수들을 framework 로 드롭 → **internal 호출 54건뿐**, 진짜 런타임 함수(PFM_*, mpfm_*)는 안 걸러져 **external 489 노이즈**.
- 분류 알고리즘 코드(`shared/lang/call_classifier.py`)는 정상 — **입력(scan 결과)이 오염**된 것.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 스캔 에이전트가 드롭 목록을 올바르게 채운다 (Priority: P1)

운영자가 프레임워크 코드(예: ProFrame C)를 분석하면, 스캔 단계가 **프레임워크/런타임 함수(PFM_*, mpfm_*, PRINT_* 등)**를 드롭 목록에 담고, 프로젝트 자기 업무 함수(a000_*, b000_* 등)는 담지 않는다. 그 결과 자기 함수끼리의 호출은 internal 로 연결되고, 런타임 함수 호출은 그래프에서 드롭된다.

**Why this priority**: 이게 안 되면 호출 그래프 전체가 오염(internal 누락 + external 노이즈)되어 분석 산출물이 신뢰 불가. 단일 최우선.

**Independent Test**: 알려진 ProFrame C 입력(zapamcom 류)을 분석해, 그래프의 internal CALLS 가 프로젝트 함수쌍을 잇고, PFM_*/mpfm_* 가 노드/엣지로 남지 않음을 확인. 드롭 목록에 프로젝트 함수가 0건임을 로그로 검증.

**Acceptance Scenarios**:

1. **Given** ProFrame C 프로젝트 + 매칭 스킬, **When** 스캔 단계 실행, **Then** 드롭 목록 필드에는 스킬이 명시한 런타임 함수(PFM_*/mpfm_*/PRINT_* 글로브)만 들어가고 프로젝트 함수는 들어가지 않는다.
2. **Given** 그 드롭 목록, **When** 호출 분류 실행, **Then** 프로젝트 함수 호출은 internal 로 분류되고(54 같은 비정상 저값 아님), 런타임 함수 호출은 드롭된다.

---

### User Story 2 - 에이전트 지시와 제출 필드명이 단일 진실로 일치 (Priority: P1)

에이전트에게 필드를 채우라고 지시하는 모든 곳(시스템 프롬프트·잔여패턴 프롬프트·스킬·스킬 템플릿)이 **제출 도구(submit_scan)가 실제로 노출하는 필드명과 정확히 일치**한다. 새 스킬을 템플릿으로 만들어도 옛 이름이 전파되지 않는다.

**Why this priority**: 명칭 불일치가 근본 원인. 이걸 정합하지 않으면 US1 이 재발. P1.

**Independent Test**: 코드의 계약 필드명 집합과 prompts/·skills/ 가 참조하는 필드명 집합을 교차 대조해 stale 참조 0건임을 확인(전수 grep).

**Acceptance Scenarios**:

1. **Given** 리포지토리 전체, **When** 계약 필드명 ↔ 문서 참조를 전수 대조, **Then** 코드에 없는 필드명을 지시하는 문서가 0건이다.
2. **Given** 스킬 템플릿으로 새 스킬 작성, **When** 그 스킬이 드롭 목록을 지시, **Then** 현행 코드 필드명을 쓴다.

---

### User Story 3 - 드롭 목록 오염을 조용히 넘기지 않고 표면화 (Priority: P2)

스캔 에이전트가 드롭 목록에 **프로젝트 자기 함수(code_nodes)를 다수 포함**하는 비정상 출력을 내면, 시스템이 그것을 경고/에러로 **표면화**한다(조용히 업무 함수를 드롭하지 않는다).

**Why this priority**: 헌법 IV(No Silent Failure). 명칭을 고쳐도 LLM 이 또 오작동할 수 있으므로 안전망. 차순위(P2).

**Independent Test**: 드롭 목록에 프로젝트 code_nodes 이름을 의도적으로 섞은 합성 scan 결과를 주입 → 시스템이 경고/에러를 내는지 확인.

**Acceptance Scenarios**:

1. **Given** 드롭 목록과 프로젝트 code_nodes 가 임계 이상 겹침, **When** 호출 분류 진입, **Then** 명시적 경고(또는 에러)가 로그·스트림에 남고 조용한 드롭이 일어나지 않는다.

---

### Edge Cases

- 드롭 목록이 정당하게 비어 있는 프로젝트(프레임워크 매크로 없음) → 경고 없이 정상.
- 스킬이 매칭되지 않은 입력(no_match) → 드롭 목록 빈 배열, 모든 미해결 호출은 external (현행 동작 유지).
- 글로브 패턴(PRINT_*)이 우연히 프로젝트 함수명과도 매칭되는 경우 → US3 가드의 오탐 가능성(임계/판정 기준 필요).
- 다른 언어 스킬(java/python) 도 동일 필드명을 써야 함(전파 방지 = 템플릿 정합 포함).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: 에이전트에게 드롭 목록 필드를 채우라고 지시하는 **모든 문서**(스캔 에이전트 시스템 프롬프트, 잔여패턴 프롬프트, 스킬 본문, 스킬 템플릿)는 **제출 도구가 노출하는 실제 필드명과 일치**해야 한다(코드에 존재하지 않는 필드명 지시 = 0건).
- **FR-002**: 드롭 목록 필드를 **`framework_runtime_functions` 로 리네임**한다(현 `framework_code_nodes` → 신). "프로젝트 분석 함수(code_nodes)" 개념과 어휘가 겹치지 않아야 하며, 코드(출력 모델·스캔 에이전트·분류기·잔여패턴 경로)와 문서(시스템 프롬프트·잔여패턴 프롬프트·스킬·템플릿)가 **동일 신규명**을 쓴다. (Clarify 2026-06-26: 방식 b 채택.)
- **FR-003**: 스캔 결과의 드롭 목록이 프로젝트 자기 code_nodes 와 임계 이상 겹치면 시스템은 이를 **명시적으로 표면화**(경고/에러)해야 한다 — 조용히 업무 함수를 드롭하지 않는다(헌법 IV).
- **FR-004**: 분류 알고리즘(`call_classifier`)의 **동작·출력은 변경하지 않는다** — 본 작업은 입력 계약(명칭·검증)만 다룬다.
- **FR-005**: 프롬프트 편집은 헌법 V(KV-Cache)를 위반하지 않아야 한다 — 함수 분석 LLM 호출이 공유하는 시스템 프롬프트 prefix 의 바이트 동일성을 깨뜨리지 않는 범위로 변경을 한정한다.
- **FR-006**: 변경 후 **전수 대조 검증**(코드 계약 필드 ↔ 문서 참조)이 통과해야 하며, 재발 방지를 위해 그 대조를 반복 가능한 절차/테스트로 남긴다.

### Key Entities *(include if feature involves data)*

- **드롭 목록 필드 (현 `framework_code_nodes`)**: 스캔 에이전트가 채우는, 호출 분류 시 드롭할 프레임워크/런타임 함수 이름(글로브) 목록. submit_scan 도구의 출력 스키마 필드.
- **에이전트 지시 문서**: 스캔 에이전트 시스템 프롬프트, 잔여패턴 프롬프트, 언어/프레임워크별 스킬, 스킬 템플릿. 에이전트가 어떤 필드에 무엇을 담을지 지시.
- **프로젝트 code_nodes**: 분석 대상 코드의 자기 함수(CodeNode). 드롭 목록과 **상호 배타**여야 함(겹치면 오염).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 동일 ProFrame C 입력 재분석 시, 스캔 드롭 목록에 포함된 **프로젝트 함수 = 0건**(이전 run 37건 → 0).
- **SC-002**: 동일 입력에서 **internal 호출 건수가 정상 회복**(이전 비정상 54 대비 유의미 증가; 프로젝트 자기 호출이 드롭되지 않음).
- **SC-003**: 리포지토리 전체에서 **코드에 없는 계약 필드명을 지시하는 문서 = 0건**(전수 grep).
- **SC-004**: 드롭 목록이 프로젝트 code_nodes 와 임계 이상 겹치는 비정상 입력에 대해 **표면화(경고/에러) 100% 발생**(합성 테스트).
- **SC-005**: 분류 알고리즘 회귀 0 — 기존 baseline/단위 테스트 무변경 통과.

## Assumptions

- 스캔 에이전트는 submit_scan 도구 스키마 + 시스템 프롬프트 + 매칭 스킬 본문을 입력으로 받아 출력을 구성한다(현행 구조 유지).
- "옛날엔 잘 됐다"는 운영자 증언은 6/19 리네임 이전 상태(framework_functions 일치)를 가리킨다 — 회귀 기준선으로 사용.
- 데이터 구조·DDL 누락(테이블/R-W 부재)은 **별건**이며 본 스펙 범위가 아니다.
- analyzer 레포는 `refactor` 브랜치, spec 025 관련 WIP 13개가 병존 — 본 작업은 그 파일들을 건드리지 않는다.
- 한 run = 전체 재빌드(헌법: logs/ 초기화) — 증분 아님.
