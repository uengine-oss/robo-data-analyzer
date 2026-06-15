# Feature Specification: Codebase Scan Agent

**Feature Branch**: `005-scan-agent`

**Created**: 2026-06-15

**Status**: Backfilled (reverse-engineered)

**Input**: Pipeline step3 (`step3_scan_project`), Framework-only. 단일 LLM 에이전트가 분석 대상 코드베이스를 도구로 직접 둘러보고(filesystem/grep/read), SKILL 을 읽어 매칭하고, table-access 정규식을 실제 소스에 검증한 뒤, 구조화된 `ScanResult`(table_access_patterns / framework_functions / call_wrappers / codebase_notes / ddl_transform)를 1회 invoke 로 산출한다.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 프로젝트 성격 식별 후 검증된 R/W 정규식 산출 (Priority: P1)

레거시 코드베이스 1개를 분석할 때, 후속 링킹(step4)이 "어떤 함수가 어떤 테이블을 읽고 쓰는지"를 결정론적으로 grep 하려면 그 프로젝트의 DB 접근 관용구를 잡는 정규식이 필요하다. 에이전트는 매니페스트·확장자·폴더 단서로 언어/프레임워크를 파악하고, 맞는 SKILL 이 있으면 그 정규식을 글자 그대로 옮기고(없으면 직접 작성), 출력 전 실제 소스에 돌려 검증한 정규식만 `ScanResult.table_access_patterns` 로 넘긴다.

**Why this priority**: 이 정규식이 없거나 틀리면 step4 의 결정론 R/W 링킹이 통째로 비어 파이프라인이 "빈 채로 성공"처럼 보인다. 분석 가치의 토대.

**Independent Test**: 알려진 프레임워크(JPA / MyBatis / OFBiz / ProFrame) 프로젝트 1개를 입력해, `table_access_patterns` 가 채워지고 `confidence ≥ 0.5` 이며 entity 가 DDL 테이블과 해소되는지 확인.

**Acceptance Scenarios**:

1. **Given** SKILL 이 매칭되는 프로젝트, **When** 에이전트가 1회 invoke, **Then** SKILL 정규식이 `verify_table_regex` 통과 후 `table_access_patterns` 에 담기고 `matched_skill` 이 채워진다.
2. **Given** 매칭 SKILL 이 없는 프로젝트, **When** 에이전트가 정규식을 직접 작성, **Then** 검증 통과분만 출력되고 끝내 못 맞추면 `table_access_patterns` 는 비고 `no_match_reason` 이 채워진다(조용한 실패 금지).

### User Story 2 - 프레임워크 노이즈 제거 + 호출 wrapper 보존 (Priority: P2)

호출 그래프에 프레임워크/런타임 함수(업무 로직 아님)가 섞이면 분석이 오염된다. 에이전트는 SKILL 문서의 프레임워크 함수 목록을 `framework_functions` 로 옮겨 드롭 대상으로 표시한다. 단, 인자(이름 string)가 실제 호출 대상인 동적 dispatch wrapper(예: ProFrame `mpfm_dlcall`)는 `call_wrappers` 로 별도 보존해, 드롭되더라도 그 인자가 `Calls` 엣지로 정합되게 한다.

**Why this priority**: P1 의 R/W 정합 위에 호출 그래프 정확도를 더한다. wrapper 누락은 모듈 간 의존 손실 사고로 이어진다(실측: `mpfm_dlcall` 11건 소실).

**Independent Test**: SKILL 에 `## 호출 wrapper` 절이 있는 프로젝트에서 `call_wrappers` 가 비면 경고가 표면화되는지 확인.

**Acceptance Scenarios**:

1. **Given** SKILL 에 wrapper 절 존재, **When** 에이전트가 `call_wrappers` 를 빈 배열로 출력, **Then** 코드가 `call_wrappers_missing_from_scan` 경고를 남긴다.

### User Story 3 - 함수 분석 LLM 에 주입할 codebase_notes 생성 (Priority: P3)

step6 의 함수 분석 LLM 호출마다 동일 prefix 로 주입할 짧은 1단락(프로젝트 성격 + 명명 컨벤션 + op 결정 신호)을 만든다. 이 prefix 가 고정이라 RadixAttention/KV 캐시에 친화적이고, op 결정 신호가 빠지면 함수 분석 LLM 이 코드 주석에 끌려 op 를 오판한다.

**Why this priority**: 다운스트림 분석 품질을 높이지만 R/W 정합 자체에는 필수가 아니다.

**Independent Test**: `codebase_notes` 가 op_mappings(token→SQL op)를 1줄로 풀어 포함하고 HTML entity 가 unescape 된 상태인지 확인.

**Acceptance Scenarios**:

1. **Given** 에이전트가 `&lt;op&gt;` 가 박힌 notes 출력, **When** finalize, **Then** `html.unescape` 로 `<op>` 로 영구 정규화된다.

### Edge Cases

- LLM 호출 한도(`ROBO_AGENT_SCAN_MAX_CALLS`=30) 초과 시 — 빈 결과로 넘기지 않고 `ModelCallLimitExceeded` 를 raise 해 식별 실패를 명시 중단한다.
- 에이전트가 root(`project_root`) 밖 경로를 도구에 줄 때 — `OUTSIDE_ROOT` 로 거부(`..`/심볼릭 링크 풀어 판정).
- 정규식 문법 오류 / `(?P<entity>...)` 명명 그룹 누락 — `verify_table_regex` 가 `INVALID_REGEX` / `MISSING_GROUP` 로 응답해 에이전트가 고치게 한다.
- op_token 인식률이 임계(`ROBO_AGENT_SCAN_REGEX_MIN_OP_RATIO`=0.5) 미달 — finalize 검증이 `ok=False`, confidence 0.2 로 표면화.
- 에이전트가 `submit_scan` 도구를 끝내 호출하지 않음 — invoke 실패로 처리(누락 명시).
- 매니페스트 못 읽음 / `project`·`run`·`cost_budget` 누락 — 경고 후 빈 `scan_result` 로 skip.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST run the scan as a SINGLE LLM agent invocation per project (탐색/결정 미분리) — 검증된 정규식이 전달 스키마에서 증발하던 과거 버그 회피.
- **FR-002**: System MUST 사전 단서(`extensions_count` 상위 20, 우선 매니페스트 앞 60줄, 카테고리별 파일 ≤50, SKILL 후보 brief, DDL 테이블 목록)를 user 메시지 prefix 로 직렬화한다(고정 prefix = 캐시 친화).
- **FR-003**: System MUST 에이전트의 모든 파일 탐색 도구를 `project_root` 에 묶고 경계 밖 접근을 `OUTSIDE_ROOT` 로 거부한다(path_boundary). `read_skill` 은 분석기 자신의 `skills/` 에 묶인다.
- **FR-004**: System MUST LLM 호출 수를 `ROBO_AGENT_SCAN_MAX_CALLS`(기본 30)로 제한하고, 초과 시 raise 하여 빈 결과로 조용히 넘기지 않는다.
- **FR-005**: System MUST 출력을 `ScanResult` Pydantic 으로 강제하되, `response_format` 직접 강제 대신 `submit_scan` 도구로 wrap(submit-wrap)하여 qwen+SGLang grammar-mask hang 을 회피한다.
- **FR-006**: System MUST 에이전트가 출력한 정규식을 `verify_table_regex` 로 검증할 때 step4 stage1 의 동일 링킹 엔진(`RegexTableLinker`)을 재사용한다(검증 정규식 == 링킹이 쓰는 정규식).
- **FR-007**: System MUST `verify_table_regex` 에서 `(?P<entity>...)` 명명 그룹을 필수로 하고, 정규식 문법·명명그룹·매치0 케이스를 명시 응답으로 돌려준다.
- **FR-008**: System MUST finalize 단계에서 채택된 정규식을 결정론 검증(`verify_table_patterns`)해 confidence 를 측정값(테이블 해소율, 통과 시 최소 0.5)으로 산정하고, op_token 인식률이 `ROBO_AGENT_SCAN_REGEX_MIN_OP_RATIO`(기본 0.5) 미만이면 통과시키지 않는다.
- **FR-009**: System MUST `framework_functions` 를 호출 그래프 드롭 대상으로, `call_wrappers` 를 인자-추출 호출 대상으로 분리 표시하고, SKILL 에 `## 호출 wrapper` 절이 있으나 `call_wrappers` 가 비면 경고를 표면화한다.
- **FR-010**: System MUST `codebase_notes` 의 HTML entity 를 unescape 하고, 이를 step6 함수 분석 LLM 의 공통 prefix 로 주입한다(Framework 만; DBMS 는 빈 문자열).
- **FR-011**: System MUST 정규식·프레임워크 함수·wrapper 를 도구로 확인한 사실에서만 도출하고(추측 금지), `evidence_locations` ≥ 3(`path:line`)을 요구한다.
- **FR-012**: System MUST 시스템 프롬프트(`prompts/agents/scan_agent_system.md`)를 byte-identical 로 보존한다(CI 검증 대상).

### Key Entities *(include if feature involves data)*

- **ScanResult**: 에이전트 1회 invoke 의 최종 출력. 필드: `matched_skill`(Optional), `confidence`(0~1, finalize 가 측정값으로 재산정), `evidence`, `evidence_locations`(≥3), `table_access_patterns`(검증 통과분), `ddl_transform`, `call_wrappers`, `codebase_notes`(step6 prefix), `framework_functions`(드롭 대상), `no_match_reason`(Optional).
- **TableAccessPattern**: `id` / `call_regex`(명명 그룹 `<op_token>`+`<entity>`) / `op_mappings`(OpMapping list) / `entity_source`.
- **OpMapping**: `token`(framework raw 토큰) → `op`(SELECT/INSERT/UPDATE/DELETE 4종만; READ/WRITE 는 코드가 자동 결정). 참고: `MERGE` 는 LLM/에이전트가 고를 수 있는 op 가 아니며, DBMS 경로(spec 006)에서만 결정론적으로 발생해 AFFECTS_TABLE op enum(spec 009)에 나타난다 — 4-vs-5 모순 아님.
- **DdlTransform**: `ddl_naming_rule`(예 `as_is`/`camel_to_snake_upper`) + `fallback_prefixes`.
- **CallWrapper**: `wrapper`(함수명) + `callee_from`(`first_arg_string`/`first_arg_identifier`).
- **filesystem 도구 (project_root 경계)**: `ls`(≤500) / `glob_search`(≤200) / `grep`(≤80줄) / `read_file`(앞 ≤50줄) / `read_file_range`(anchor±5=11줄).
- **code inspection 도구**: `list_functions`(step2 파싱 결과, ≤50) / `list_skills`(SKILL 카탈로그) / `read_skill`(SKILL.md 본문, `skills/` 경계).
- **verify_table_regex**: 에이전트 정규식을 실제 소스에 적용해 매치·R/W엣지·해소 샘플을 리포트(채택 전 필수).
- **submit_scan**: `ScanResult` 를 args_schema 로 wrap 한 출력 도구 — 에이전트가 정확히 1회 호출.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 알려진 프레임워크 프로젝트에서 `table_access_patterns` 가 1개 이상 채워지고 finalize confidence ≥ 0.5 다.
- **SC-002**: 산출된 모든 정규식은 `verify_table_regex`/`verify_table_patterns` 검증을 통과한 것만이다(매치 ≥1, 엣지 ≥1, op_token 인식률 ≥ 0.5).
- **SC-003**: 한 프로젝트 식별은 정확히 1회 LLM invoke 이며 호출 수는 30 이하다(초과 시 raise).
- **SC-004**: 모든 파일 탐색 도구 호출은 `project_root` 경계 안에서만 성공한다(밖은 100% `OUTSIDE_ROOT`).
- **SC-005**: SKILL 에 wrapper 절이 있으나 `call_wrappers` 가 빈 경우 100% 경고가 남는다(silent skip 0건).
- **SC-006**: `codebase_notes` 에 HTML entity 가 남지 않고 op 결정 신호 1줄이 포함된다.

## Assumptions

- 이 기능은 Framework 분석 경로 전용이다 — DBMS 경로는 Scan Agent 를 쓰지 않고 `codebase_notes` 가 빈 문자열이다.
- step1(parse_ddl)의 DDL 테이블과 step2(load_ast)의 Function 목록이 선행 제공된다(정규식 검증·`list_functions` 기준).
- 분석 대상 코드는 `data/source/<project>` 아래에 있고 그 디렉토리가 도구 경계(root)다.
- SKILL 은 자유 형식 마크다운이며 코드는 파싱하지 않는다 — 에이전트가 `read_skill` 로 읽어 `ScanResult` 로 정규화한다.
- LLM 백엔드는 qwen+SGLang 가정(그래서 submit-wrap), `ROBO_LLM_REASONING_EFFORT=none` 운영 정책과 결합한다.
- `run_structured_agent` / `standard_stack` 미들웨어 / `CostBudget` / 스트림 emit 인프라는 기존 공용 컴포넌트를 재사용한다.