# Feature Specification: Strategy Detection & Pipeline Assembly

**Feature Branch**: `002-strategy-pipeline-assembly`

**Created**: 2026-06-15

**Status**: Backfilled (reverse-engineered)

**Input**: 기존 코드에서 역설계해 backfill — `analysis_pipelines.py` / `strategy_detector.py` / `graph_builder.py` / `state.py` / `step_utils.py` 의 실제 동작을 명세로 옮김.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 소스 종류 자동 판별 후 올바른 파이프라인 실행 (Priority: P1)

분석 대상 폴더를 던지면, 시스템이 사용자 지시 없이 그 안의 파일 확장자를 보고
"애플리케이션 코드(framework)"인지 "Oracle PL/SQL(dbms)"인지 스스로 정하고
거기에 맞는 분석 단계 순서를 그대로 실행한다.

**Why this priority**: 이게 없으면 사용자가 매번 종류를 직접 지정해야 하고, 잘못 지정하면
엉뚱한 단계가 돌아 그래프가 깨진다. 전체 파이프라인의 진입점이자 가장 흔한 사용 경로.

**Independent Test**: framework 확장자 파일만 든 폴더 / dbms 확장자만 든 폴더 / 둘 다 없는 빈
폴더를 각각 넣고, 선택된 strategy 이름과 실행 step 순서를 확인하면 단독 검증된다.

**Acceptance Scenarios**:

1. **Given** `.java`/`.py` 같은 코드 파일이 든 폴더, **When** 분석을 시작하면, **Then**
   strategy 가 `framework` 로 잡혀 11개 framework step 순서가 실행된다.
2. **Given** `.sql`/`.pkb`/`.pks` 만 든 폴더(코드 파일 0), **When** 분석을 시작하면, **Then**
   strategy 가 `dbms` 로 잡혀 13개 dbms step 순서가 실행된다.
3. **Given** 인식 확장자가 하나도 없는 폴더, **When** 분석을 시작하면, **Then**
   안전 기본값 `framework` 로 잡힌다 (분기 없는 단일 경로).

---

### User Story 2 - 자동 판별 결과를 사용자가 덮어쓰기 (Priority: P2)

자동 감지가 의도와 다를 때(예: 코드와 SQL이 섞인 폴더를 DBMS 로만 보고 싶을 때),
사용자가 strategy 를 명시적으로 강제해 자동 감지를 무시할 수 있다.

**Why this priority**: 자동 감지는 휴리스틱이라 100% 맞지 않는다. override 가 안전판 역할을
하지만, 자동 감지(P1)가 동작해야 의미가 있으므로 그 다음 우선순위.

**Independent Test**: 같은 폴더에 CLI `--strategy dbms` 를 주거나 API 요청 본문에
`strategy: "dbms"` 를 줘서, 자동 감지값과 무관하게 지정한 파이프라인이 도는지 확인.

**Acceptance Scenarios**:

1. **Given** 자동 감지로는 `framework` 가 나올 폴더, **When** CLI 에 `--strategy dbms` 를 주면,
   **Then** `dbms` 파이프라인이 실행된다 (감지값 무시).
2. **Given** API `/analyze` 요청, **When** 본문에 `strategy: "framework"` 를 주면, **Then**
   해당 파이프라인이 실행되고, 알 수 없는 이름이면 `ValueError` 로 거부된다.

---

### User Story 3 - 코드 분기 없이 새 strategy / 단계 순서 추가 (Priority: P3)

개발자가 새 분석 종류(예: 새 언어 계열)를 추가할 때, 선언 파일 한 곳에 `Strategy(...)`
한 줄을 추가하기만 하면 되고, 그래프 빌더나 상태 코드에는 if 분기를 일절 넣지 않는다.

**Why this priority**: 유지보수성/확장성 가치. 당장의 사용자 기능은 아니지만 이 아키텍처가
P1·P2 를 "분기 0개"로 떠받치는 근본 설계라 명세에 못박아 둔다.

**Independent Test**: 새 `Strategy` 인스턴스를 선언하고 빌더에 넘겨, 빌더/상태/유틸 코드를
한 줄도 안 고치고 그래프가 컴파일·실행되는지 확인.

**Acceptance Scenarios**:

1. **Given** `analysis_pipelines.py` 에 step tuple 만 다른 새 `Strategy` 선언, **When** 빌더에
   그 strategy 를 넘기면, **Then** 빌더 코드 수정 0으로 StateGraph 가 빌드된다.
2. **Given** step 순서 중 한 자리에 함수 대신 함수들의 tuple 을 두면, **When** 빌드하면,
   **Then** 그 tuple 멤버들이 병렬 그룹(동시 실행 후 합류)으로 묶인다.

---

### Edge Cases

- framework 확장자와 dbms 확장자가 한 폴더에 섞여 있으면? → framework 우선
  (코드가 하나라도 있으면 framework). dbms 로 보려면 override 필요.
- 인식 못 하는 확장자만 있거나 폴더가 비면? → 기본값 `framework` (에러 없이 단일 경로).
- override 로 알 수 없는 strategy 이름을 주면? → `get_pipeline` 이 `ValueError` 로 거부.
- 병렬 그룹 멤버끼리 같은 state 키를 동시에 쓰면? → 키 충돌을 LangGraph 가 에러로 표면화
  (멤버는 입력 read-only·return 키 비충돌이 전제).
- step 함수의 `config` 어노테이션이 `RunnableConfig`/`Optional[RunnableConfig]` 가 아니면?
  → LangGraph 가 config 주입을 못 해 step 이 빈 결과만 반환 (silent 빈 결과).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: 시스템은 대상 폴더의 파일 확장자를 재귀적으로 훑어 strategy 를
  `framework` 또는 `dbms` 로 자동 판별 MUST.
- **FR-002**: dbms 확장자 집합(`.sql` `.pkb` `.pks` `.pls` `.ddl`) 과 framework 확장자
  집합(`.java` `.kt` `.py` `.go` `.c` `.cpp` `.h` `.hpp` `.ts` `.js` `.rb` `.rs` `.php` `.scala`)
  을 단일 진실로 두고 양쪽 진입점(CLI·baseline)이 같이 import MUST.
- **FR-003**: framework 확장자가 하나라도 있으면 `framework`, 없고 dbms 확장자만 있으면
  `dbms`, 둘 다 없으면 `framework`(안전 기본값)로 결정 MUST.
- **FR-004**: 사용자가 CLI `--strategy {framework|dbms}` 또는 API 요청의 `strategy` 필드로
  자동 판별을 덮어쓸 수 있어야 MUST. override 가 주어지면 자동 감지를 건너뛴다.
- **FR-005**: 알 수 없는 strategy 이름은 명확한 오류(`ValueError`, 기대 목록 포함)로 거부 MUST.
- **FR-006**: 각 strategy 는 step 함수 순서 tuple + context_builder + prompts_dir 만 담는
  순수 데이터 컨테이너(로직 없음)로 선언 MUST.
- **FR-007**: 그래프 빌더는 strategy 가 무엇인지 모른 채 `strategy.steps` 만 순회해
  StateGraph 노드/엣지를 구성 MUST — strategy 별 if 분기 0개.
- **FR-008**: steps 항목이 함수면 단일(선형) 노드, tuple 이면 병렬 superstep 그룹
  (직전 단계에서 fan-out → 동시 실행 → 다음 단계로 fan-in)으로 빌드 MUST.
- **FR-009**: 새 strategy 추가는 선언 파일(`analysis_pipelines.py`)의 `Strategy(...)` 추가만으로
  가능 MUST — 빌더/상태/유틸 코드 수정 없이.
- **FR-010**: WorkflowState 는 strategy 무관(framework·dbms 동일 state)이며, 각 state 키는
  한 step 만 책임지고 default replace 로 갱신 MUST. 단, 여러 step 이 나눠 누적하는 키
  (`call_sites`, `import_refs`, `reads_writes_edges`)는 add reducer 로 누적 MUST.
- **FR-011**: 후처리 병렬 그룹은 공유 동시성 한도(`llm_semaphore`)를 통해 서버 총 동시
  LLM 호출 수를 제어 MUST (과부하 방지).
- **FR-012**: 매 분석 실행은 독립 run 이며 run 1회 안의 superstep state 전파는 Checkpointer
  (MemorySaver)가 보존 MUST.

### Key Entities *(include if feature involves data)*

- **Strategy**: 파이프라인 전략 선언. 속성 = `name`(`framework`/`dbms`),
  `context_builder`(`build_context_step` 에 주입), `prompts_dir`(yaml 프롬프트 위치),
  `steps`(실행 순서 함수 tuple). frozen 데이터컨테이너 — 로직 없음.
- **Framework step list (11개, 순서)**: parse_ddl → load_ast → scan_codebase →
  link_deterministic → link_calls_by_name → link_residual_patterns → build_context →
  fetch_table_samples → analyze_function → **(병렬 그룹: summarize_module,
  enrich_table_description, build_components, health_metrics)** → embed.
- **DBMS step list (13개, 순서)**: parse_ddl → load_ast → link_dbms_tables →
  link_calls_by_ast → build_context → fetch_table_samples → analyze_function →
  summarize_module → enrich_table_description → enrich_lineage_from_catalog →
  build_components → health_metrics → embed. (scan_codebase·link_deterministic·
  link_residual_patterns 없음 — DBMS 는 Scan Agent 미사용.)
- **Parallel superstep group**: steps tuple 안의 한 자리에 든 함수들의 tuple. 서로 독립
  (입력 read-only·return state 키 비충돌)이라 동시 실행 후 합류. 공유 `llm_semaphore` 로
  총 동시 LLM 수 제어. framework 후처리 4-step 묶음이 유일한 현재 사례.
- **StateGraph (LangGraph)**: `WorkflowState`(TypedDict)를 state 로 갖는 컴파일된 워크플로.
  빌더가 strategy.steps 로부터 노드·엣지를 생성하고 `astream(stream_mode="updates")` 단일
  채널로 실행. 분기 없는 결정론 fan-out/fan-in 그래프.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 폴더만 주어졌을 때 사용자 입력 없이 100% 자동으로 strategy 가 결정된다
  (입력 미지정 케이스 0건).
- **SC-002**: 동일 strategy_detector 가 모든 진입점(CLI·baseline)에서 import 되어,
  같은 폴더는 진입점과 무관하게 항상 같은 strategy 로 판별된다 (drift 0).
- **SC-003**: 새 strategy 또는 step 순서 변경 시 빌더/상태/유틸 소스 파일 수정 라인 = 0
  (선언 파일만 변경).
- **SC-004**: strategy 분기를 위한 if 문 개수 = 0 (빌더는 strategy 정체를 모른 채 동작).
- **SC-005**: 알 수 없는 strategy 이름 요청은 100% 명확한 오류로 거부되며 그래프 실행에
  도달하지 않는다.

## Assumptions

- 한 분석 대상 폴더는 하나의 strategy 로 처리한다(폴더당 단일 strategy). 혼합 폴더는
  자동 감지 우선순위(framework 우선) 또는 override 로 결정.
- 확장자 집합은 현재 지원 언어/DDL 기준이며, 미지원 확장자만 있는 폴더는 framework 로
  처리하는 것이 안전 기본값으로 합의됨.
- AST 파싱은 strategy 무관 공통 경로(antlr JSON reader)이며 strategy 별 parser 주입은 없다.
- LangGraph 의 state 전파·체크포인트·스트림 책임에 의존하며, 각 실행은 디스크 재개가 아닌
  같은 세션 안의 독립 run 이다.
- step 함수는 모두 `async def step(state, config: Optional[RunnableConfig]=None)` 시그니처를
  지키며 `config` 어노테이션 화이트리스트를 준수한다(준수 실패 = silent 빈 결과).