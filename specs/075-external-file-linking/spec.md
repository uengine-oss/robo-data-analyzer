# Feature Specification: External File Linking

**Feature Branch**: `main`

**Created**: 2026-07-21

**Status**: Approved

**Input**: `D:/work/robo/_playground/xlink/HANDOFF.md`의 검증된 외부파일→코드 지식그래프 기능을 현재 Analyzer 최종 구조에 완전히 병합한다.

## Authoritative Decisions

- 현재 소스 코드와 현행 constitution이 런타임 사실의 단일 진실이다.
- 병합 목표 구조와 기능 계약은 `HANDOFF.md` §12가 단일 진실이다. 구형 `MERGE-PLAN.md`의
  `step2b_load_external`, 72~118h 계획은 사용하지 않는다.
- 최종 단계 트리는 아래 순서를 지킨다. 별칭, 호환 shim, 중복 경로를 만들지 않는다.

```text
step1_parse_ddl
step2_load_ast
step3_load_external
step4_scan_project
step5_linking
step6_build_context
step7_analyze_code
step8_post_processing
```

- 에이전트와 스킬의 이름·격리는 `framework/`와 `external/`이다. `format`, `filetype`,
  `reader`, `chunk-extract` 이름은 병합 산출물에 남기지 않는다.
- playground의 `java_functions.py`, `graph_io.py`, `llm_frentis.py`, `env.py`는 이식하지 않는다.
  Analyzer의 AST, graph writer, LLM client, settings를 재사용한다.
- 메모리 `todo.md`는 GOAL-D 전용이므로 이 기능의 요구나 완료 판정에 사용하지 않는다.

## User Scenarios & Testing

### User Story 1 - 외부파일의 직접 사용 근거를 코드 그래프에서 본다 (Priority: P1)

분석 사용자는 XML mapper, 설정, descriptor, 문서, 비표준 텍스트 파일의 항목을 코드가 직접
사용하는 경우 그 항목과 코드 노드가 근거와 함께 연결된 것을 확인한다. 추측 연결은 사실 그래프에
들어가지 않는다.

**Why this priority**: 외부파일에 숨은 호출·설정·업무 문맥이 빠지면 AI 코드분석 컨텍스트가
불완전하고, 반대로 오연결이 들어가면 AI가 거짓을 사실로 인용한다.

**Independent Test**: 실제 ANTLR `code_nodes`와 독립 정답지 위에서 직접 문자열 사용,
불변 상수 1단계 사용, 주석 전용 언급, 동적 키 조립, 미사용 키를 건별 대조한다.

**Acceptance Scenarios**:

1. **Given** 코드 문자열 리터럴이 외부 청크 key를 직접 포함할 때, **When** 외부 링커가 실행되면,
   **Then** 코드→청크 관계와 파일·라인 근거가 저장되고 같은 NDJSON 관계 이벤트가 발신된다.
2. **Given** 같은 클래스의 불변 문자열 상수가 청크 key이고 코드가 그 상수를 사용할 때,
   **When** 링커가 실행되면, **Then** `via_const` 근거로 연결된다.
3. **Given** key가 주석에만 있거나 런타임 문자열 조립으로만 만들어질 때, **When** 링커가 실행되면,
   **Then** 사실 관계는 생성되지 않고 미해결 사유가 기록된다.
4. **Given** SQL 청크가 DDL의 TABLE을 읽거나 쓸 때, **When** 링커가 실행되면,
   **Then** SQL→TABLE 및 코드→TABLE의 기존 READS/WRITES 계약이 보존된다.

---

### User Story 2 - 모든 외부파일을 안전하게 인벤토리하고 작은 청크로 읽는다 (Priority: P1)

운영자는 프로젝트의 소스·DDL 외 모든 파일이 누락 없이 등록되고, 지원 포맷은 결정론 파서로,
미지원·바이너리·실패 포맷은 명시적인 상태로 처리되며, 어떤 파일도 분석 전체를 멈추거나 토큰 폭탄을
만들지 않는다고 신뢰한다.

**Independent Test**: 실프로젝트 15개 센서스 포맷과 병적 입력을 격리 입력 루트에서 전수 처리하고,
파일 inventory 수·상태·청크 크기·parse failure를 원문과 대조한다.

**Acceptance Scenarios**:

1. **Given** UTF-8, BOM, EUC-KR 또는 CP949 텍스트 파일, **When** 읽으면, **Then** 한글이 손실되지 않는다.
2. **Given** 1,200자를 넘는 원문 청크, **When** 청킹하면, **Then** 작은 anchor와 순서가 있는 parts로
   나뉘고 모든 조각이 상한 이하다.
3. **Given** 깨진 XML, 거짓 인코딩 선언, 바이너리 위장, 빈 파일 또는 미지원 포맷, **When** 처리하면,
   **Then** 조용한 빈 성공 대신 파일 상태와 이유가 보존되고 다른 파일 처리는 계속된다.
4. **Given** XML entity 폭탄·외부 DTD 또는 위험한 정규식, **When** 처리하면, **Then** 외부 네트워크,
   무한 확장, ReDoS가 차단되고 전체 run이 hang하지 않는다.

---

### User Story 3 - 처음 보는 외부 포맷을 격리된 Agent가 학습한다 (Priority: P2)

운영자는 라이브러리로 읽을 수 없는 텍스트 포맷에 대해서만 External Agent가 제한된 규칙을 제안하고,
결정론 검증과 합의에 통과한 스킬만 `agents/skills/external/`에 영속되어 재실행은 LLM 0회가 되는 것을
확인한다.

**Independent Test**: agent battery, 합의, 복구, 조기 종료, 오버핏, ReDoS 공격을 실행하고 framework
스킬 카탈로그와 external 스킬 카탈로그가 서로를 볼 수 없음을 확인한다.

**Acceptance Scenarios**:

1. **Given** framework 스킬 6개와 external 스킬 N개, **When** 각 agent가 카탈로그를 읽으면,
   **Then** 자기 하위 폴더만 보며 상대 스킬은 0개다.
2. **Given** 처음 보는 포맷, **When** 합의 샘플을 생성하면, **Then** 공유 LLM semaphore와
   `gather_with_warmup`을 사용하고 run 비용 한도를 우회하지 않는다.
3. **Given** 규칙이 기대 key를 나열하거나 위험·과매칭·빈추출인 경우, **When** 검증하면,
   **Then** 채택되지 않고 정직한 미분석 상태로 끝난다.
4. **Given** 이미 채택된 external 스킬, **When** 같은 포맷을 재실행하면, **Then** LLM 호출 없이 재사용된다.

---

### User Story 4 - 외부 근거가 분석 컨텍스트와 UI까지 이어진다 (Priority: P2)

사용자는 코드 분석 설명이 직접 연결된 외부 청크를 근거로 사용할 수 있고, Analyzer UI에서는 분석 중
신규 단계와 그래프 델타가 실시간으로 보이되 외부 보조 노드·관계는 기본 화면을 어지럽히지 않는다.

**Independent Test**: 한 run에서 입력→청크→링킹→컨텍스트→LLM prompt record→Neo4j→NDJSON→UI를
같은 key와 ID로 대조하고 Playwright로 기본 숨김·전체 보기·콘솔·네트워크를 확인한다.

**Acceptance Scenarios**:

1. **Given** 코드 노드에 직접 연결된 외부 anchor와 parts, **When** context를 만들면, **Then** 토큰 예산
   안에서 원문 순서가 복원되고 출처·key가 포함된다.
2. **Given** `step3.load_external` 또는 `step5.link_external` 진행 이벤트, **When** UI가 소비하면,
   **Then** 한국어 단계 문구와 안정된 아이콘·이름이 표시된다.
3. **Given** 기본 그래프 보기, **When** 외부 노드와 관계가 존재하면, **Then** 기본 숨김이고 기존
   READS/WRITES 업무 뼈대는 보인다.
4. **Given** “숨긴 요소까지 모두 보기”, **When** 활성화하면, **Then** 외부 노드·관계가 정확한 끝점과
   함께 나타나며 무관 관계는 섞이지 않는다.

---

### User Story 5 - 기존 Framework/DBMS 분석이 회귀하지 않는다 (Priority: P1)

운영자는 단계 번호가 이동하고 신규 분석이 추가돼도 기존 Framework·DBMS의 AST, TABLE, CALLS,
READS/WRITES, 분석 결과, 비용·취소·종료 계약이 그대로 유지된다고 신뢰한다.

**Independent Test**: 이동 전 기준선과 이동 후 전체 unittest, prompt hash, 실제 analyzer 기동,
대표 Framework/DBMS 분석, REST/MCP/UI 결과를 대조한다.

**Acceptance Scenarios**:

1. **Given** 기존 Framework와 DBMS 입력, **When** 최종 파이프라인을 실행하면, **Then** 기존 필수
   노드·관계·분석 결과의 승인 기준선 밖 차이가 0이다.
2. **Given** pause, stop, 취소, 비용 초과 또는 파일 파싱 실패, **When** run이 종료되면, **Then** task와
   리소스가 수거되고 오류 단계가 드러나며 빈 성공으로 끝나지 않는다.
3. **Given** 모든 구조 이동이 끝났을 때, **When** 저장소와 외부 소비자를 검색하면, **Then** 옛 step 경로,
   `format`/`filetype`, 금지 이식 모듈, 죽은 `has_xml_mapper` 참조가 0이다.

### Edge Cases

- 서로 다른 파일에 같은 key가 있어도 파일 기반 ID가 달라야 한다.
- 같은 파일·key를 재실행해도 노드·관계 수가 증가하지 않아야 한다.
- 빈 key, 지나치게 짧은 key, 따옴표 부분문자열, 대소문자 차이를 오연결하지 않아야 한다.
- CTE alias, SQL 문자열 속 SQL, XML 주석, 주석 처리된 호출, SQL 예약어를 TABLE로 만들지 않아야 한다.
- 문서 exact mention과 임베딩 후보를 구분하고 candidate를 사실 관계로 승격하지 않아야 한다.
- source root 밖 경로, symlink/junction 이탈, 읽기 권한 실패를 명시적으로 거부해야 한다.
- 외부파일 수만 개·key 수만 개에서도 키→파일 역인덱스가 전수 매칭과 같은 결과를 내야 한다.
- Frontend·Architect·ANTLR의 기존 사용자 WIP와 겹치는 파일은 덮거나 되돌리지 않아야 한다.

## Requirements

### Functional Requirements

- **FR-001**: Analyzer는 승인된 최종 8-step 트리를 호환 shim 없이 구현해야 한다.
- **FR-002**: 프로젝트 파일 inventory는 포함/제외 정책에 따른 모든 외부파일과 처리 상태·이유를 기록해야 한다.
- **FR-003**: 외부 텍스트 읽기는 run-scoped `SourceFileCache`와 공용 경로 경계를 재사용해야 한다.
- **FR-004**: 외부 내용은 종류 무관 `ExternalChunk` anchor+parts 계약으로 정규화하고 각 text는
  `MAX_CHUNK_CHARS=1200` 이하여야 한다.
- **FR-005**: 직접 사용 판정은 따옴표 리터럴 exact match와 안전한 불변 상수 1단계만 허용해야 한다.
- **FR-006**: 미확정·동적·실패 참조는 관계를 만들지 않고 `UNRESOLVED_REF`로 보존해야 한다.
- **FR-007**: 문서 exact mention만 `MENTIONS` 사실 관계이며 임베딩 결과는 `CANDIDATE_LINK` 노드로 격리해야 한다.
- **FR-008**: 신규 노드·관계 이름은 graph schema 단일 진실에 등록되고 모든 writer는
  `graph_owner='analyzer'`를 사용해야 한다.
- **FR-009**: 외부파일 ID는 코드·DB·외부 호출 stub과 구조적으로 충돌하지 않는 `file:` 네임스페이스를 써야 한다.
- **FR-010**: Neo4j 저장 관계와 NDJSON 관계 이벤트는 타입·끝점·의미 속성이 동형이어야 한다.
- **FR-011**: Framework와 External Agent는 runner·LLM client·catalog helper만 공유하고 프롬프트,
  출력 스키마, 호출 단위, 검증, 스킬 폴더를 분리해야 한다.
- **FR-012**: External Agent는 R≤2 self-repair, n=7/agree=4 합의, 공유 semaphore, 비용 예산,
  KV-cache warmup 계약을 지켜야 한다.
- **FR-013**: 정규식 실행은 프로덕션에서 kill 가능한 timeout 또는 선형시간 엔진으로 격리해야 하며
  daemon thread 누수를 허용하지 않는다.
- **FR-014**: 직접 연결된 외부 청크는 출처와 순서를 보존한 채 build-context에 주입되어야 한다.
- **FR-015**: Frontend는 신규 단계·노드 이름을 렌더하고 외부 보조 노드·관계를 기본 숨김 단일 정책에 추가해야 한다.
- **FR-016**: 기존 `has_xml_mapper` 플래그는 생산자·소비자 0을 확인한 뒤 API·모델·문서에서 제거해야 한다.
- **FR-017**: `java_functions`, `graph_io`, `llm_frentis`, `env`의 Analyzer 복사본을 만들지 않아야 한다.
- **FR-018**: 모든 파일 이동은 `git mv`와 파일별 이동표·소비자표를 사용하고 각 슬라이스에서 옛 경로 0을 증명해야 한다.
- **FR-019**: P0~P8 각 Phase는 집중 테스트, 전체 계약 회귀, 실기동 또는 해당 Phase의 실제 경계 검증 증거를 남겨야 한다.
- **FR-020**: Analyzer·Frontend 외의 사용자 WIP와 관련 없는 변경은 수정·삭제·stage하지 않아야 한다.
- **FR-021**: PDF/XLSX/DOCX/HWP 경로는 라이선스와 로컬 실행 가능성을 검증한 adapter 계약으로 제공하고,
  미설치 선택 의존성은 전체 run을 깨뜨리지 않되 해당 파일의 미분석 이유를 명시해야 한다.

### Key Entities

- **ProjectFile**: 프로젝트 상대경로, 포맷 분류, 처리 상태와 실패 이유를 가진 inventory 항목.
- **ExternalChunk**: 파일 기반 ID, key, kind, text, 원문 위치, part 순서와 anchor ID를 가진 통합 청크.
- **DocumentNode**: DOCUMENT/DOC_SECTION/DOC_CHUNK 구조와 순서·원문 위치를 가진 문서 노드.
- **UnresolvedRef**: 연결 또는 파싱을 확정하지 못한 대상·key·reason·detail의 정직한 기록.
- **CandidateLink**: 사실 관계가 아닌 검색 후보와 점수·모델·상태를 가진 격리 노드.
- **ExternalSkill**: 외부파일 포맷별 결정론 추출 규칙과 검증·합의 provenance를 가진 영속 스킬.

## Success Criteria

### Measurable Outcomes

- **SC-001**: xlink 독립 정답지의 직접 링크는 누락·초과 0이며 주석·동적·부분문자열 오연결 0이다.
- **SC-002**: inventory 대상 파일 수와 `PROJECT_FILE` 수가 일치하고 미분류 파일은 상태·이유 없이 사라지지 않는다.
- **SC-003**: 모든 anchor/part의 text 길이가 1,200자 이하이고 재조립 hash가 원문 hash와 일치한다.
- **SC-004**: 신규 노드·관계의 Neo4j와 NDJSON 계약 테스트가 모든 라벨·관계·속성에 대해 통과한다.
- **SC-005**: 동일 입력 2회 실행의 Analyzer 소유 노드·관계 수와 ID 집합이 동일하다.
- **SC-006**: agent battery·consensus·loop·오버핏·ReDoS 검증이 통과하고 채택분 precision은 100%다.
- **SC-007**: Analyzer 전체 unittest, 구조 검사, prompt hash, Framework/DBMS 계약 테스트가 기준선 이상이다.
- **SC-008**: 실제 Analyzer run에서 외부파일 단계가 완료되고 원문→graph→context→REST/MCP→UI가 같은 ID로 대조된다.
- **SC-009**: Frontend 실제 화면에서 기본 숨김·전체 보기·진행 문구·콘솔 오류 0을 확인한다.
- **SC-010**: 최종 검색에서 옛 step3~7 경로, `format`/`filetype` 잔재, 금지 이식 모듈,
  `has_xml_mapper`, 임시 호환 경로가 0이다.
- **SC-011**: 관련 없는 사용자 WIP의 시작·종료 diff가 byte-for-byte 동일하다.

## Assumptions and Scope

- playground의 검증 자산과 실제 코퍼스는 읽기 전용 정답·회귀 자산으로 사용한다.
- 공유 `project/data`는 읽기 전용이며 파서 출력·테스트 초기화 대상으로 사용하지 않는다.
- 컬럼/FK 수준 외부 SQL 확장은 이번 핵심 완료 조건이 아니며 기존 TABLE 수준 계약을 유지한다.
- 문서 임베딩 후보는 사실 그래프가 아니며 품질이 부족해도 MENTIONS 정확성에 영향을 주지 않는다.
- 기능 완료는 P0~P8, Analyzer·Frontend, 실기동·UI 검증까지 모두 끝난 상태다.
