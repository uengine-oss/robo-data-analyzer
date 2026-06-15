# Feature Specification: AST Loading → Code Structure Graph

**Feature Branch**: `004-ast-code-structure-graph`

**Created**: 2026-06-15

**Status**: Backfilled (reverse-engineered)

**Input**: User description: "파이프라인 step2 (LLM 0회). antlr-code-parser 가 미리 만들어 `data/analysis/<project>/**/*.json` 에 떨궈둔 AST JSON 만 읽어(소스 재파싱 X) antlr 타입을 온톨로지 라벨/stereotype 으로 매핑하고, 결정론적으로 식별자 REFERENCES/INIT_BY 를 잇고, FUNCTION/MODULE/PACKAGE/VARIABLE/CONSTANT/DATA_TYPE 노드 + 구조 엣지를 Neo4j 에 UPSERT 한다. 더불어 step4 용 raw call_sites 를 수집한다."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 코드 구조 그래프 구축 (Priority: P1)

분석가가 레거시 프로젝트를 분석할 때, 외부 antlr-code-parser 가 미리 만들어 둔 AST JSON 트리(`data/analysis/<project>/`)를 읽어 함수·모듈·패키지·변수·상수·자료형 노드와 이들을 잇는 구조 엣지를 Neo4j 지식그래프에 만든다. 이 단계는 LLM 을 한 번도 호출하지 않으며, 같은 입력이면 항상 같은 그래프를 만든다(결정론).

**Why this priority**: 이후 모든 분석 단계(호출그래프·요약·시각화)가 이 구조 노드 위에서 동작한다. 이 단계가 없으면 그래프 자체가 비어 파이프라인 전체가 무의미하다.

**Independent Test**: 샘플 프로젝트의 antlr JSON 디렉토리만 주고 step2 를 단독 실행한 뒤 Neo4j 에 FUNCTION/MODULE/… 노드와 HAS_FUNCTION/PARENT_OF 엣지가 antlr 트리 구조 그대로 들어갔는지 확인한다. LLM·후속 step 없이 검증 가능.

**Acceptance Scenarios**:

1. **Given** `data/analysis/<project>/` 아래 java/c/python/PL\*SQL AST JSON 들이 존재할 때, **When** step2 를 실행하면, **Then** 각 antlr 타입이 온톨로지가 정한 primary 라벨(MODULE/FUNCTION/VARIABLE/CONSTANT/DATA_TYPE)로 매핑되어 노드가 UPSERT 되고, 트리 중첩이 PARENT_OF·HAS_FUNCTION·HAS_VARIABLE·HAS_MEMBER·BELONGS_TO_PACKAGE 엣지로 도출된다.
2. **Given** Java `METHOD` 같이 primary 라벨과 다른 타입의 노드일 때, **When** 노드를 만들면, **Then** 온톨로지가 도출한 동적 sub-label(`FUNCTION:METHOD`)이 함께 부여된다. C `FUNCTION` 처럼 타입과 라벨이 같으면 sub-label 없이 단일 라벨이 된다.

### User Story 2 - 결정론적 식별자 참조 링킹 (Priority: P2)

antlr JSON 에는 "변수 참조" 노드가 없으므로, 함수 본문 텍스트와 변수/상수 초기화식의 식별자 토큰을 실제 변수/상수 이름과 정확히 일치시켜 REFERENCES·INIT_BY 엣지를 결정론으로(LLM 0회) 만든다. import 신호로 범위를 좁혀 동명이인 오연결을 막는다.

**Why this priority**: 데이터 흐름·의존성 분석의 토대지만, 구조 노드(P1)가 먼저 있어야 매칭 대상이 존재한다.

**Independent Test**: 한 모듈 안에서 함수가 쓰는 변수, 다른 import 한 모듈의 상수를 담은 픽스처를 주고 REFERENCES/INIT_BY 엣지가 정확히 그 노드를 가리키며 동명의 무관한 노드는 가리키지 않는지 확인.

**Acceptance Scenarios**:

1. **Given** 같은 모듈에 정의된 변수를 함수 본문이 사용할 때, **When** 링킹하면, **Then** Function→Variable REFERENCES 엣지가 첫 등장 라인을 evidence 로 생성된다.
2. **Given** Java `import pkg.Cls` / Python `from mod import X` / C `#include "h"` 로 명시 import 한 외부 모듈의 상수를 참조할 때, **When** Pass 2 링킹하면, **Then** 그 import 가 못박은 모듈의 노드만 매칭된다(미import 동명 노드는 제외).

### Edge Cases

- antlr JSON 디렉토리(`data/analysis`)가 아예 없으면 → 명시적 `FileNotFoundError` 를 던지고 멈춘다(silent skip 금지, "antlr-parser 를 먼저 실행하세요" 안내).
- 개별 JSON 파일이 깨졌거나(JSON 파싱 실패) 루트가 FILE 객체가 아니면 → 그 파일만 오류/경고 로깅 후 건너뛰고 나머지 파일은 계속 처리.
- 온톨로지 미등록 확장자/언어면 → 경고 1회 로깅 후 그 파일 skip(antlr-parser 미지원 언어).
- Pass 2(모듈 너머 링킹) 미지원 언어(java/python/c 외)면 → 언어당 1회만 안내 로깅, Pass 1(같은 모듈) 은 정상 작동(안전 degrade).
- 같은 이름 노드 id 충돌(Java 오버로드 등) → `:startLine` 접미로 고유화.
- 주석·문자열 리터럴은 식별자 매칭 전 제거 → 오탐 차단. 자기참조는 제외.
- `project`(codebase) 설정 누락 시 → 경고 후 step skip(빈 결과).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: 시스템은 소스를 재파싱하지 않고, 외부 antlr-code-parser 가 `data/analysis/` (원본 소스 구조 그대로 미러)에 미리 만들어 둔 `*.json` AST 만 입력으로 읽어야 한다. 이 JSON 이 두 서비스 간 계약(cross-service contract)이다.
- **FR-002**: 시스템은 antlr JSON 디렉토리가 없으면 명시적 `FileNotFoundError` 를 던져야 하며, 조용히 건너뛰어서는 안 된다.
- **FR-003**: 시스템은 antlr node type 을 온톨로지(`ontology_roles.yaml` 단일 진실)가 정한 역할(module/data_type/function/variable/constant/internal)로 분류하고, 역할에 대응하는 Neo4j primary 라벨로만 노드를 만들어야 한다. internal(FUNCTION_CALL/IMPORT/FILE 컨테이너 등)은 노드로 박지 않는다.
- **FR-004**: 시스템은 타입이 primary 라벨과 다를 때 온톨로지가 도출한 동적 sub-label 을 노드에 부여해야 한다(예: `FUNCTION:METHOD`). DBMS(PL\*SQL) 노드는 sub-label 없는 단일 라벨이어야 한다.
- **FR-005**: 시스템은 확장자로 Framework(java/c/python) 경로와 DBMS(sql/pks/pkb/pls/ddl) 경로를 분기해야 한다. DBMS 는 PROCEDURE/FUNCTION/TRIGGER 를 Function 루트로, 그 안의 IF/LOOP/CASE/DML 제어흐름 중첩도 모두 Function 트리로 수집한다.
- **FR-006**: 시스템은 antlr 트리 중첩에서 구조 엣지를 결정론으로 도출해야 한다: PARENT_OF(중첩 함수), HAS_FUNCTION(모듈→함수), HAS_VARIABLE(모듈→변수), HAS_MEMBER(자료형→멤버변수), BELONGS_TO_PACKAGE(모듈→폴더 패키지). 자료형 멤버 변수는 HAS_MEMBER 에만 속하고 HAS_VARIABLE 에서 제외된다.
- **FR-007**: 시스템은 파일이 든 폴더를 곧 Package 노드로 만들고(최상위 파일은 패키지 없음), 모듈을 그 패키지에 BELONGS_TO_PACKAGE 로 연결해야 한다.
- **FR-008**: 시스템은 LLM 없이 결정론으로 REFERENCES 엣지를 만들어야 한다 — 함수 본문 라인의 식별자 토큰을 변수/상수 이름과 정확 일치(주석·문자열 제거 후)시켜 Function→(Variable|Constant)로 잇고, 함수당 타겟별 1개를 첫 등장 라인 evidence 로 기록한다.
- **FR-009**: 시스템은 변수/상수 정의식(antlr 가 `initValue`/`value` 로 통합 출력 — `=` 우변·size 표현식·상수 식 포함)의 식별자를 다른 변수/상수 이름과 일치시켜 INIT_BY 엣지((Variable|Constant)→출처)를 만들어야 한다.
- **FR-010**: 시스템은 참조 링킹을 2단 패스로 수행해야 한다. Pass 1 은 같은 모듈(전 언어 공통), Pass 2 는 코드에 명시된 import 신호만 따라 모듈 너머로 확장한다 — Java `import`/`import static`/`.*`/같은 패키지, Python `import`/`from import`(상대 import 포함), C `#include "헤더"`. 추측하지 않으며 동명이인 오연결을 차단한다.
- **FR-011**: 시스템은 Neo4j 에 UPSERT(멱등) 해야 한다 — 노드는 라벨이 달라 병렬, 엣지는 노드 선행·락 경합 회피를 위해 순차로 쓴다. neo client 가 없으면(드라이런) 쓰기를 건너뛰되 노드/엣지는 계산해 반환한다.
- **FR-012**: 시스템은 후속 step4(호출그래프)용 raw `call_sites`(FUNCTION_CALL: call_id·caller_function_id·call_text·file·line)와 `import_refs`(INCLUDE/IMPORT: name·file·line)를 노드로 만들지 않고 단서로만 수집해 state 로 넘겨야 한다.
- **FR-013**: 시스템은 같은 antlr 입력에 대해 항상 같은 노드/엣지를 산출해야 한다(LLM 0회·정렬된 파일 순회·결정론 id).

### Key Entities *(include if feature involves data)*

- **antlr AST JSON (입력 계약)**: 외부 antlr-code-parser 산출물. 파일당 루트 노드 = `FILE` 객체. 키: `fileName`, `filePath`(소스 루트 기준 상대경로), `packageName`(Java), `children`(중첩 노드 트리), 노드별 `type`·`name`·`startLine`·`endLine`·`signature`·`returnType`·`parameters`·`variableType`·`initValue`·`annotations`·`comment`. analyzer 는 이 JSON 만 신뢰하고 소스를 재파싱하지 않는다.
- **MODULE**: 함수를 품는 컨테이너. 언어별로 file-level(c/python FILE) 또는 클래스(java/python CLASS·INTERFACE). 동적 sub-label `MODULE:CLASS` 등.
- **FUNCTION**: 함수/메서드. sub-label `FUNCTION:METHOD`(java/python) 또는 단일 `FUNCTION`(c). DBMS 는 PROCEDURE/FUNCTION/TRIGGER + 제어흐름 노드 모두 Function(단일 라벨, parent_id 트리).
- **PACKAGE**: 파일이 든 폴더. 모듈이 BELONGS_TO_PACKAGE 로 소속.
- **VARIABLE**: 필드·전역변수·멤버·DBMS 선언변수/파라미터/커서. sub-label `VARIABLE:FIELD` 등.
- **CONSTANT**: 상수 필드·C `#define`·DBMS CONSTANT_FIELD.
- **DATA_TYPE**: 함수를 품지 못하는 자료형(C STRUCT/UNION/ENUM). 멤버는 Variable + HAS_MEMBER.
- **구조 엣지**: PARENT_OF·HAS_FUNCTION·HAS_VARIABLE·HAS_MEMBER·BELONGS_TO_PACKAGE — antlr 트리에서 결정론 도출.
- **참조 엣지**: REFERENCES(Function→Variable|Constant, evidence 라인 포함)·INIT_BY((Variable|Constant)→출처) — reference_linker 결정론 식별자 매칭.
- **call_sites / import_refs (state)**: Neo4j 노드 아님. call_sites 는 step4 호출그래프 단서, import_refs 는 Pass 2 모듈 너머 링킹 범위 결정에 쓰인 뒤 state 로도 전달.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: antlr JSON 디렉토리만 주어지면 LLM 호출 0회로 코드 구조 그래프가 완성된다(외부 모델 의존 없음).
- **SC-002**: 같은 antlr 입력을 두 번 돌리면 동일한 노드·엣지 집합이 나온다(결정론, UPSERT 멱등).
- **SC-003**: antlr JSON 디렉토리가 없으면 100% 명시적 오류로 멈춘다(빈 그래프를 조용히 만들지 않음).
- **SC-004**: import 으로 못박힌 모듈 외의 동명 노드를 가리키는 REFERENCES/INIT_BY 오연결이 0건이다.
- **SC-005**: 개별 JSON 파일 하나가 깨져도 나머지 파일 처리율 100% 유지(파일 격리).

## Assumptions

- antlr-code-parser 가 step2 이전에 실행되어 `data/analysis/` 에 유효한 AST JSON 을 미러 구조로 떨궈 둔다(이 단계는 소스를 파싱하지 않는다).
- AST 루트는 `ENV.data.dir / "analysis"`, 소스 루트는 분석 대상 경로 그 자체(업로드 모드면 `data/source`, 경로/Electron 모드면 사용자가 고른 로컬 폴더). 둘 다 `project.root` 로 들어와 모드 무관 동일 처리.
- 온톨로지(`ontology_roles.yaml`)가 antlr node type ↔ 역할/라벨의 단일 진실이며, 노드 분류 코드는 yaml 을 직접 읽지 않고 `ontology.py` 만 거친다.
- Pass 2 모듈 너머 링킹은 java/python/c 만 지원하며, 그 외 언어는 같은 모듈(Pass 1)만 적용한다.
- 설계상 범위 밖(못 잡아도 silent fail 아님): Python 인스턴스 속성 접근(`self._cfg.x` — 타입 추론 필요), Java 상속 상수(부모 클래스 상수를 맨이름으로).
- Neo4j 클라이언트가 주입되면 UPSERT 하고, 없으면 계산 결과만 state 로 반환한다.