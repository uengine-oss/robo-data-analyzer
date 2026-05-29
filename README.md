# ROBO Analyzer (refact)

레거시 코드베이스 + DDL → **Neo4j 지식 그래프** 로 변환하는 정적 분석 파이프라인.
Framework(자바·C·Python 등 응용 코드) 또는 DBMS(PL/SQL 패키지) 둘 중 한
**Strategy** 로 동작하며, 한 번의 `/analyze` 호출이 7 단계 LangGraph workflow 1회
실행 = `Run` 1개를 만든다.

> 이 문서 하나로 환경 구성 → 실행 → 산출물 해석 → 코드 구조 → 단계별 LLM 동작
> 까지 모두 파악 가능하도록 작성. 더 깊은 영역 노트는 메모리(`[[robo-analyzer-refact-guide]]`)
> 및 `c:/tmp/robo-pipeline-study/` 학습노트 참조.

---

## 0. 한 줄 요약

- **입력**: `<project_root>/` (소스 코드 디렉토리) + `<ddl_dir>/*.sql` (DDL)
- **출력**: Neo4j 그래프 (UPSERT) + `logs/analysis.run/latest/artifacts/*.json` 4 종 + `usage_summary.json` + NDJSON 스트림
- **엔진**: FastAPI + LangGraph StateGraph (7 step) + LangChain Agent (Scan / 잔여패턴)
- **LLM**: `llm_configs/<name>.yaml` 1개 = "모델 + 배포 환경" 1셋. `.env` 의 `ROBO_LLM_CONFIG` 1줄로 전환
- **결정론 우선**: parse_ddl / load_ast / linking 결정론 grep / Bottom-up DAG 는 LLM 0 회.
  LLM 은 (1) Scan Agent 1회 (2) 잔여 패턴 루프 (3) 큰 함수 one-liner (4) module pre-analysis (5) 3분할 함수분석 (6) module_summary (7) table_description 만.

---

## 1. 외부 의존성 / 사전 준비

### 1.1 별도 git repo — antlr-parser

| repo | 위치 | branch | 역할 |
|------|------|--------|------|
| `robo-analyzer-refact` | `c:/uEngine/robo/robo-analyzer-refact` | `refactor` | 본 파이프라인 (Python) |
| `antlr-parser` | `c:/uEngine/robo/antlr-parser` | `main` | **Java 외부 파서** — 소스 → AST JSON |

ROBO Analyzer 는 소스를 **직접 파싱하지 않는다**. `antlr-parser` 가 미리 생성해 둔
JSON AST 를 `data/analysis/` 에서 읽기만 한다 (`antlr_json_reader`).

push 위치:
- robo-analyzer-refact → `origin: github.com/uengine-oss/robo-data-analyzer`, branch `refactor`
- antlr-parser → `origin: github.com/uengine-oss/antlr-code-parser`, branch `main`

### 1.2 Neo4j 서버

UPSERT 가 매 step 마다 일어나므로 **Neo4j 인스턴스 필수**. `.env` 의
`ROBO_NEO4J_URI / USER / PASSWORD / DATABASE` 설정. analyzer 는 매 run 시작에
`MATCH (n) DETACH DELETE n` 으로 그래프 wipe → `ensure_schema()` 로 제약/인덱스
재확인 (idempotent).

### 1.3 LLM 엔드포인트

`llm_configs/<name>.yaml` (4 개 동봉):

| yaml | provider | 비고 |
|------|----------|------|
| `qwen36_sglang_local` | sglang | 사내 SGLang 배포 (Qwen3.6) — 현재 `.env` 활성 (`frentis-ai-model`) |
| `qwen36_openrouter` | openrouter | 같은 모델을 OpenRouter 로 |
| `gpt54_mini_openai` | openai | OpenAI 본점 gpt-5.4-mini |
| `gpt54_nano_openrouter` | openrouter | gpt-5.4-nano (Scan Agent 환각 회피 대체용) |

> 새 config 추가 = yaml 1개 + `.env` 의 `ROBO_LLM_CONFIG=<name>` 1줄. 코드 변경 0.
> 자세한 스키마: [`llm_configs/README.md`](llm_configs/README.md).

### 1.4 Python 의존성

`requirements.txt` 핵심 (전체 정책: 패치버전 고정):
- `langchain 0.3.27` / `langgraph 0.6.10` / `langgraph-checkpoint 2.1.2`
- `deepagents 0.1.16` — Scan/잔여 패턴 에이전트 (TodoListMiddleware 등)
- `pydantic 2.10.6` / `pydantic-settings 2.7.1` — 출력 스키마 강제 (환각 차단)
- `sqlglot 27.13.0` — DDL 다중 방언 (Oracle/PG/MySQL/MSSQL)
- `antlr4-python3-runtime 4.13.2` — antlr JSON 호환만 (파싱 자체는 외부 java)
- `neo4j 5.27.0`
- `sentence-transformers 3.3.1` — 임베딩 (`paraphrase-multilingual-MiniLM-L12-v2`)
- `fastapi 0.115.6` / `uvicorn 0.34.0`
- `tenacity 9.0.0` — provider rate-limit / Neo4j TransientError 재시도
- `httpx 0.28.1` / `aiohttp 3.11.11`

설치:
```bash
cd c:/uEngine/robo/robo-analyzer-refact
pip install -r requirements.txt
```

---

## 2. 입력 데이터 규약

### 2.1 `ROBO_DATA_DIR` 구조 (CLI `analyze_data_dir.py` 기준)

```
ROBO_DATA_DIR/                  # 기본 ./data, .env 의 ROBO_DATA_DIR 로 override
├── ddl/
│   ├── schema.sql              # 공통 DDL — Oracle 방언 가능, sqlglot 다중 방언
│   └── *.sql
├── source/
│   ├── <project_a>/            # 분석 대상 1
│   │   └── *.c | *.java | *.py | ...
│   └── <project_b>/
└── analysis/                   # antlr-parser 가 미리 생성한 AST JSON (source/ 미러)
    └── <project_a>/
        └── *.json
```

- 하위 `<project_x>/` 없이 소스가 `source/` 직속이면 `source/` 자체가 1 프로젝트.
- DDL 디렉토리는 없어도 동작 (Table 노드 0개로 진행, R/W 엣지 안 생김).
- `analysis/` 가 없거나 비어 있으면 `step2_load_ast` 가 `FileNotFoundError` 명시 raise.

### 2.2 검증된 테스트 데이터 (`c:/uEngine/robo/data/`)

| 폴더 | 파일 | 용도 |
|------|------|------|
| `source/` | `zapamcom10060.c` (89.5K) · `zapamcom10140.c` (121.9K) | ProFrame C 레거시 (skill: proframe-c-pfm-dbio) |
| `ddl/` | `schema.sql` (10K) | Oracle DDL — Table 7 개 |
| `analysis/` | `zapamcom10060.json` (62K) · `zapamcom10140.json` (87K) | antlr-parser 가 미리 만든 AST |

### 2.3 Strategy 자동 감지 — `cli_scripts/strategy_detector.py`

확장자 기반:
- `.sql / .pkb / .pks / .pls / .ddl` 만 있으면 → `dbms`
- `.java / .kt / .py / .go / .c / .cpp / .h / .hpp / .ts / .js / .rb / .rs / .php / .scala` 하나라도 있으면 → `framework`
- 둘 다 없으면 `framework` (안전 기본값)

`--strategy` CLI 인자로 override 가능.

---

## 3. 실행 방법

### 3.1 CLI — 가장 자주 쓰는 진입점

```bash
cd c:/uEngine/robo/robo-analyzer-refact

# source/ 안 모든 프로젝트 일괄 분석
python cli_scripts/analyze_data_dir.py

# 1 프로젝트만 (하위 폴더 이름)
python cli_scripts/analyze_data_dir.py zapamcom10060

# strategy 강제 override
python cli_scripts/analyze_data_dir.py <target> --strategy dbms
```

NDJSON 스트림이 콘솔에 실시간 출력 (`[PHASE]` / `[TOOL ]` / `[TODO ]` / `[ERROR]` /
`[WARN ]` / `[END  ]`). Windows cp949 콘솔 대응 — `sys.stdout.reconfigure(utf-8)` 자동.

### 3.2 FastAPI 서버

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
# 또는
python -m main
```

엔드포인트 — **단 1개**:

```http
POST /robo/analyze
Content-Type: application/json

{
  "project_name": "zapamcom10060",
  "project_root": "C:/uEngine/robo/data/source",
  "strategy": "framework",            # "framework" | "dbms"
  "ddl_dir": "C:/uEngine/robo/data/ddl",
  "has_xml_mapper": false,
  "locale": "ko",
  "catalog_service_url": null,        # DBMS only
  "catalog_api_key": null             # DBMS only
}

→ 200 OK, Content-Type: application/x-ndjson, long-lived stream
```

NDJSON 이벤트 7종 — `phase_event` / `tool_event` / `todo_update` / `node_event` /
`relationship_event` / `error` / `warning` / `stream_end`.

`GET /` — health check (`{"status": "ok"}`).

> 폴링/컨트롤 엔드포인트는 **없다** (Plan §4 M1). 매 `/analyze` = 독립 run.
> resume(중단 재개) 미지원. 클라이언트가 stream 끊으면 그 run 종료.

### 3.3 보조 CLI

| 스크립트 | 용도 |
|---------|------|
| `cli_scripts/baseline_runner.py <projects_dir>` | 여러 프로젝트 일괄 + `baseline_summaries.json` 생성 |
| `cli_scripts/reset_neo4j_schema.py` | Neo4j 제약/인덱스 1회 리셋 (오염 정리 — 평상시 불필요) |
| `cli_scripts/check_byte_identical.py` | `prompts/**` SHA256 ↔ `cli_scripts/prompt_hashes.json` 검증 (CI 보호장치) |
| `cli_scripts/refresh_pricing.py` | `shared/llm/llm_pricing.yaml` 갱신 보조 |

---

## 4. 파이프라인 — 7 단계 흐름

`analysis_pipelines.py` 의 `Strategy` 데이터클래스가 step 함수 tuple 을 declare 만
하면, `shared/workflow_runner/langgraph_workflow.build_langgraph_workflow()` 가
**if 분기 0개** 로 LangGraph StateGraph 노드를 순서대로 추가한다.

### 4.1 Framework Pipeline (11 step)

```
parse_ddl_step
 → load_ast_step
 → scan_codebase_step              ★ Framework only — Scan Agent 1 회
 → link_deterministic_step          (stage1) — 정규식 grep, LLM 0
 → link_calls_by_name_step          ★ Framework only — 호출 결정론 분류, LLM 0
 → link_residual_patterns_step      ★ Framework only — 잔여 패턴 루프 (Agent 라운드)
 → build_context_step               (큰 함수 one_liner LLM, 그 외 결정론)
 → analyze_function_step            (module pre-analysis + Bottom-up DAG + 3분할 fan-out)
 → summarize_module_step
 → enrich_table_description_step
 → embed_step                       (ROBO_PIPELINE_EMBED_ENABLED=true 일 때만)
```

### 4.2 DBMS Pipeline (11 step)

```
parse_ddl_step
 → load_ast_step
 (scan_codebase_step 없음 — DBMS 는 Scan Agent 안 씀)
 → link_dbms_tables_step            ★ DBMS only — DML range → DDL 테이블 결정론 매칭
 → link_deterministic_step          (잔여 정규식 — 거의 동작 안 함)
 → link_calls_by_regex_step         ★ DBMS only — PL/SQL PKG.FUNC regex
 (잔여 패턴 루프 없음 — DBMS 는 에이전트 링킹 안 씀)
 → build_context_step
 → analyze_function_step
 → summarize_module_step
 → enrich_table_description_step
 → enrich_metadata_from_catalog_step  ★ DBMS only — 외부 catalog HTTP
 → enrich_lineage_from_catalog_step   ★ DBMS only — ETL 리니지
 → embed_step
```

> 새 strategy 추가 = `analysis_pipelines.py` 의 `Strategy(...)` declare 만. 코드 분기 0.

---

## 5. Step 별 상세 — 입력 / 동작 / 출력

각 step 함수 signature 는 표준 `async def step(state: dict, config: RunnableConfig) -> dict`.
`step_config(config)` 로 `project / run / cost_budget / neo_client / stream / strategy` 접근.

### Step 1 — `parse_ddl_step` (LLM 0)

**입력**: `project.ddl_dir` (또는 `project.root`) 안의 `*.sql` 파일들.
**파서**: `sqlglot` 다중 방언 → `shared/ddl_parser/ddl_parser.py`.
**출력**: `Schema` · `Table` · `Column` 노드, `HasColumn` · `BelongsTo` · `Fk` 엣지.
state 누적 + Neo4j UNWIND 배치 UPSERT.

### Step 2 — `load_ast_step` (LLM 0)

**입력**: `data/analysis/<project>/**/*.json` (antlr-parser 가 미리 생성).
**파서**: `analysis_flow/step2_load_ast/antlr_json_reader.py` — 언어별 ontology 매핑.
**REFERENCES / INIT_BY**: `reference_linker.py` 가 결정론 식별자 매칭.
**출력**: `Function` · `Module` · `Package` · `Variable` · `Constant` · `DataType` 노드,
`ParentOf` · `HasFunction` · `HasVariable` · `HasMember` · `BelongsToPackage` ·
`References` · `InitBy` 엣지 + `call_sites` (raw — step4 에서 분류) + `import_refs`.
**실패 정책**: AST 디렉토리 없으면 명시 `FileNotFoundError` (silent skip 금지).

### Step 3 — `scan_codebase_step` (Framework only, LLM Agent 1 회)

**입력**: `project_root` + state 의 `functions / tables` + `scanner_input_collector` 가
모은 단서 (`manifest_content` / `extensions_count` / `scanner_categories`).
**에이전트**: LangChain `create_agent` 단일 호출. 도구 9 종:
- `ls / glob_search / grep / read_file / read_file_range` (project_root 경계 강제)
- `list_functions` (step2 가 파싱한 함수 목록)
- `list_skills` / `read_skill` (`skills/<name>/SKILL.md` — 자유 문서)
- `verify_table_regex` (정규식 → 실제 소스 검증 — DDL 테이블 매치율 산출)
- (`write_todos` 는 deepagents TodoListMiddleware 자동 제공)

**시스템 프롬프트**: `prompts/agents/scan_agent_system.md` (byte-identical 보존 — CI 검증).
**출력 강제**: `submit_scan` 도구 1회 호출 → `ScanResult` Pydantic 인스턴스.
※ LangChain 의 자동 ToolStrategy(`response_format` wrap) 진입을 차단하기 위해
`structured_agent.py:_wrap_pydantic_as_submit_tool` 가 모델을 submit 도구로 wrap.
SGLang + qwen 의 [도구 N + Pydantic schema 동시 grammar mask] hang 회피 (2026-05-28).

**산출 `ScanResult`** (`agent_tools/agent_output_models.py`):
- `matched_skill` (없으면 null) / `confidence` / `evidence` / `evidence_locations`
- `table_access_patterns: list[TableAccessPattern]` — 검증 통과분만
  - `call_regex` named group `<op_token>` + `<entity>`
  - `op_mappings: list[OpMapping]` (token → SELECT/INSERT/UPDATE/DELETE, R/W 는 코드가 결정)
- `ddl_transform: DdlTransform` (entity → DDL 테이블명 변환 규칙)
- `codebase_notes` — 1단락 (step6 함수분석 LLM prefix 로 항상 주입 — KV 캐시 친화)
- `framework_functions` — 호출 그래프에서 드롭할 런타임 함수 이름

**한도**: `ROBO_AGENT_SCAN_MAX_CALLS` (기본 30). 초과 시 `ModelCallLimitExceeded` raise
(빈 결과 silent fail 금지).

### Step 4 — Linking (3 stage)

#### 4-A `link_deterministic_step` (stage1, LLM 0)

`ScanResult.table_access_patterns` 의 검증된 정규식으로 `RegexTableLinker` 가
project root 전체를 grep → `ReadsWrites` 엣지. 매칭 안 된 케이스는
`unresolved_cases` 큐로 (stage3 가 처리).

#### 4-B `link_calls_by_name_step` (Framework, LLM 0)

step2 의 `call_sites` 를 코드 목록 대조로 분류:
- 프로젝트 함수 → `internal` `Calls` 엣지
- `framework_functions` 의 런타임/매크로 → 드롭
- 그 외 → `external` `Calls` 엣지

#### 4-B' `link_calls_by_regex_step` (DBMS, LLM 0)

PL/SQL `PKG.FUNC(...)` 단순 정규식으로 호출 추출 → `Calls` 엣지.

#### 4-B'' `link_dbms_tables_step` (DBMS, LLM 0)

leaf range (자식 없는 단일 statement) 본문에서 **DDL 테이블명 단어경계 문자열검색**.
첫 DML 키워드(SELECT/WITH/INSERT/UPDATE/DELETE/MERGE) 가 relation/dml_type 결정.
주석은 `--` / `/* */` 제거 후 매칭.

#### 4-C `link_residual_patterns_step` (Framework only, LLM Agent 라운드 루프)

stage1 의 `unresolved_cases` 를 받아 **잔여 패턴 루프** — 케이스마다 추적이 아니라
샘플에서 공통 패턴을 LLM 에게 찾게 해 코드가 일괄 적용.

- 시스템 프롬프트: `prompts/agents/residual_pattern_system.md`
- 라운드당 샘플 수: `ROBO_PIPELINE_RESIDUAL_SAMPLE_SIZE` (기본 30)
- 최대 라운드: `ROBO_PIPELINE_RESIDUAL_MAX_ROUNDS` (기본 3)
- 호출 한도: `ROBO_AGENT_RESIDUAL_MAX_CALLS` (기본 15)
- 출력 도구: `submit_residual` → `ResidualPatternResult`
- 발견된 정규식은 `verify_table_regex` 통과분만 적용

**state 정책**: `unresolved_cases` 는 reducer 없는 평키 — stage1 의 raw 잔여를 stage3 가
최종 잔여로 덮어쓴다 (누적 reducer 면 raw·최종 섞임).

### Step 5 — `build_context_step` (큰 함수만 LLM 1 회)

모든 `Function` 에 `AnalysisContext` 생성:
- 식별 (function_id / signature) / 위치 / `reads / writes / callers / callees` (정렬 tuple)
- `referenced_symbols` — REFERENCES + INIT_BY 결정론 도출
- `one_liner` — 본문 토큰 > `ROBO_PIPELINE_BIG_CONTEXT_THRESHOLD_TOKENS` (기본 1000) 면
  `prompts/common/large_function_one_liner.yaml` 로 압축 LLM 1 회 (작은 모델).
  → callee 본문 통째 노출 차단 ($400 사고 원인 차단).
- `strategy.context_builder.augment(ctx, function, state)` 호출:
  - Framework: 항등 (`FrameworkContextBuilder`)
  - DBMS: `DbmsContextBuilder` 가 parent / sibling 트리 메타를 `extra` 에 채움

### Step 6 — `analyze_function_step` (모듈 사전분석 + Bottom-up DAG + 3분할 fan-out)

0. **모듈 사전분석** (Framework only — `prompts/framework/module_pre_analysis.yaml`):
   모듈마다 헤더 주석 → `Module.intent`. 3분할 공통 context 에 주입.
1. **호출 그래프 cycle 식별**: `CALLS` + `PARENT_OF` 둘 다 입력. Tarjan SCC →
   `is_in_cycle=True` 메타 (cycle 내부는 callee summary 없이 분석).
2. **Bottom-up rounds**: leaf → root 정렬. 같은 round 는 `asyncio.gather`+`Semaphore`
   (`ROBO_PIPELINE_MAX_CONCURRENCY`, **기본 30** — 2026-05-28 8 → 30 상향),
   round 간 순차.
3. **함수당 3분할 fan-out** (`three_split_llm_fanout.analyze_function_three_split`):
   - **#1 `function_summary`** (항상) → `SummaryOutput` (`[핵심]` 한 줄 + 내용 + stereotype + logical_name)
   - **#2 `function_rules_examples`** (항상) → `RulesExamplesOutput` (rules + examples + questions — 2026-05-30 단순화: rule 4 필드 / example given/when/then 자연어 단일 평면 / affected_tables 단일)
   - **#3 `function_table_insights`** (R/W 테이블 있을 때만) → `TableInsightsOutput` (table_insights + column insights)

   3분할 입력 = [공통 context 영역 byte-identical] + [#3 전용 자식 rules]. KV 캐시
   prefix 적중을 노린다 (`prompts/<strategy>/_common.yaml` ← `extends`).
   await `#1` → `gather(#2, #3)` 순서로 cache 워밍.

4. **즉시 적재**: 함수 1개 완료 즉시 Neo4j `update_summaries()` + `node_event` /
   `relationship_event` NDJSON emit. 배치 누적 없음 — 메모리 누적 0.

5. **Example Mapping 빌더** (`shared/example_mapping/builder.py`, 결정론):
   3분할 결과 → `Rule` (공유, `rule_hash`) / `Example` / `Question` 노드 +
   `HasRule` / `HasExample` / `HasQuestion` / `Next` / `Branch` / `AffectsTable` 엣지.

### Step 7 — Post Processing

- **`summarize_module_step`** — Function ↔ Module `file_path + start_line` 매칭 →
  멤버 함수 summary 종합 → `prompts/<strategy>/module_summary.yaml` LLM 1회 →
  `Module.summary / stereotype / logical_name` 적재.
- **`enrich_table_description_step`** — 함수별 `tableInsights` 누적 → 테이블별로
  candidates 모아 `prompts/<strategy>/table_description.yaml` LLM 1회 →
  `Table.analyzed_description` + `Column.analyzed_description` (DDL/카탈로그 1차
  description 과 격리).
- **`enrich_metadata_from_catalog_step`** (DBMS only) — 외부 catalog HTTP →
  Table/Column 메타 보강. `ROBO_CATALOG_ENABLED=true` 이고 HTTP 실패 시 raise.
- **`enrich_lineage_from_catalog_step`** (DBMS only) — catalog ETL 리니지 →
  `ReadsWrites` 엣지 추가 (매칭 실패 시 `__catalog__:` prefix 가상 노드).
- **`embed_step`** — `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`
  로 Table.description / Column.description / Function.summary 임베딩 → Neo4j
  `embedding` property. `ROBO_PIPELINE_EMBED_ENABLED=true` 일 때만.

---

## 6. 출력 / 산출물

### 6.1 디렉토리 구조 — `ROBO_LOG_DIR/` (기본 `./logs`)

매 run 시작 시 **`logs/` 통째 rmtree** 후 재생성 (`_reset_logs_root`).

```
logs/
├── analysis.run/latest/
│   ├── analyzer.log               # INFO+ — phase/tool/todo/node/edge mirror (file_enabled=true)
│   ├── error.log                  # WARNING+ — 사고 추적 전용 (file_enabled=true)
│   ├── tool_events.jsonl          # Agent 도구 호출 NDJSON 적재
│   ├── usage.jsonl                # 모든 LLM 호출 토큰/비용 NDJSON (agent + yaml_rule 둘 다)
│   ├── usage_summary.json         # usage.jsonl 집계 (total + per_agent_model)
│   ├── agent_<name>.json          # 에이전트 invoke 별 turn 누적 (prompt_logger)
│   └── artifacts/
│       ├── ast.json               # step1 + step2 결과 (schemas/tables/columns + functions/modules + 구조 엣지)
│       ├── linking.json           # step4 결과 (R/W 엣지 + Calls + unresolved + scan_result)
│       ├── analysis_contexts.json # step5 결과 (function_id → AnalysisContext)
│       └── analysis.json          # step6+7 결과 (summary 있는 functions + modules)
└── llm_prompts/latest/
    └── ...                         # 룰별 LLM 입력 미리보기 (prompt_logger.write_rule_record)
```

### 6.2 NDJSON 스트림 이벤트 — `application/x-ndjson`

| `type` | 의미 |
|--------|------|
| `phase_event` | step 시작/완료 (`event=start/node_done/complete`, `node=<step>`, `run_id`) |
| `tool_event` | Agent 도구 호출 친화 메시지 |
| `todo_update` | TodoListMiddleware 의 todo 진행 (`todos: [{content, status, activeForm}]`) |
| `node_event` | 그래프 노드 생성/갱신 (label / id / props) |
| `relationship_event` | 그래프 엣지 생성/갱신 (rel_type / from / to) |
| `error` | 오류 (silent fail 금지 — `_run_in_background` 가 명시 emit) |
| `warning` | 경고 |
| `stream_end` | 스트림 종료 신호 — consumer break |

> `emit()` 가 동시에 `logger.info` 호출 → 모든 NDJSON 이벤트가 자동으로
> `analyzer.log` 에 적재 (트렁크 — 호출처마다 따로 log_info 안 부름).

### 6.3 Neo4j 그래프 스키마

**노드 13 종 — `shared/neo4j/nodes/`** (라벨은 UPPER_SNAKE):
- DB: `SCHEMA` (key: `db,name`), `TABLE` (`fqn`), `COLUMN` (`fqn`)
- 코드: `FUNCTION` (`function_id`), `MODULE` (`module_id`), `PACKAGE` (`package_id`),
  `VARIABLE` (`variable_id`), `CONSTANT` (`constant_id`), `DATA_TYPE` (`datatype_id`)
- Example Mapping: `RULE` (`rule_hash`), `EXAMPLE` (`example_id`), `QUESTION` (`question_id`)

**엣지 19 종 — `shared/neo4j/edges/` + `shared/neo4j/rel_types.py`**:

| 구분 | 엣지 |
|------|------|
| 코드 구조 | `CALLS` · `PARENT_OF` · `HAS_FUNCTION` · `HAS_VARIABLE` · `HAS_MEMBER` · `BELONGS_TO_PACKAGE` |
| 코드 참조 | `REFERENCES` · `INIT_BY` |
| 코드↔DB | `READS` · `WRITES` |
| DB 구조 | `HAS_COLUMN` · `BELONGS_TO` · `FK` |
| Example Mapping | `HAS_RULE` · `HAS_EXAMPLE` · `HAS_QUESTION` · `NEXT` · `BRANCH` · `AFFECTS_TABLE` |
| (보조) | `RULE_FLOW` · `AFFECTS_TABLE` |

> 새 노드/엣지 추가 = `data_models/graph_nodes.py` (또는 `graph_edges.py`) +
> `shared/neo4j/nodes/<new>.py` (또는 `edges/<new>.py`) 2곳만.

### 6.4 산출 검증 예시 (2026-05-27 측정, 2 파일 분석)

```json
// logs/analysis.run/latest/usage_summary.json
{
  "total": { "input_tokens": 149520, "output_tokens": 2588, "cached_tokens": 0,
             "total_tokens": 152108, "cost_usd": 0.0, "calls": 24 },
  "per_agent_model": [
    {"agent": "scan",                          "calls": 7,  "input_tokens": 88489},
    {"agent": "rule:large_function_one_liner", "calls": 13, "input_tokens": 52977},
    {"agent": "rule:module_pre_analysis",      "calls": 2},
    {"agent": "rule:module_summary",           "calls": 2}
  ]
}
```

`artifacts/analysis.json` 의 `modules[].summary` 예 (`핵심:` 마커 + 내용):
> 핵심: 실시간 인증 이력 관리 및 은행/EDI 검증 기반의 인증 요청 처리
> 입력된 인증 요청 정보를 검증한 후, 관련 코드 상세 정보를 조회하고 ...

---

## 7. 디렉토리 구조

```
robo-analyzer-refact/
├── main.py                         # FastAPI 진입점 (app = FastAPI + analyze_router)
├── analysis_pipelines.py           # Strategy declare — Framework / DBMS 두 파이프라인 한 파일
├── requirements.txt                # 패치버전 고정
├── .env / .env.example / .env.production
│
├── api/
│   └── analyze_router.py           # POST /robo/analyze 1 개
│
├── cli_scripts/
│   ├── analyze_data_dir.py         # ★ 운영자 표준 진입점
│   ├── baseline_runner.py          # 여러 프로젝트 일괄 + 종합 보고서
│   ├── check_byte_identical.py     # prompts/ SHA256 CI 검증
│   ├── refresh_pricing.py          # llm_pricing.yaml 갱신 보조
│   ├── reset_neo4j_schema.py       # Neo4j 스키마 1회 리셋
│   ├── strategy_detector.py        # 확장자 → strategy 자동 (CLI 공용)
│   └── prompt_hashes.json          # prompts/ SHA256 보존
│
├── analysis_flow/                  # 7 step LangGraph 노드
│   ├── step1_parse_ddl/
│   ├── step2_load_ast/
│   │   ├── antlr_json_reader.py    # antlr JSON → 노드/엣지/call_sites
│   │   └── reference_linker.py     # REFERENCES / INIT_BY 결정론 매칭
│   ├── step3_scan_project/
│   │   ├── scan_codebase_step.py   # Framework only
│   │   ├── scan_agent.py           # Agent 1 회 invoke (run_structured_agent)
│   │   ├── scan_verify_tool.py     # verify_table_regex
│   │   └── scanner_input_collector.py
│   ├── step4_linking/
│   │   ├── link_dbms_tables_step.py        # DBMS only — DML range → DDL 매칭
│   │   ├── stage1_regex_grep/
│   │   │   ├── link_deterministic_step.py
│   │   │   └── regex_table_linker.py
│   │   ├── stage2_extract_calls/
│   │   │   ├── link_calls_by_name_step.py  # Framework
│   │   │   ├── link_calls_by_regex_step.py # DBMS
│   │   │   └── call_regex_linker.py
│   │   └── stage3_residual_patterns/
│   │       ├── link_residual_patterns_step.py    # Framework only
│   │       └── residual_pattern_loop.py
│   ├── step5_build_context/
│   │   ├── build_context_step.py
│   │   ├── framework_context_builder.py    # 항등
│   │   ├── dbms_context_builder.py         # parent/sibling 트리
│   │   └── large_function_one_liner.py     # 큰 함수 압축 LLM
│   ├── step6_analyze_function/
│   │   ├── analyze_function_step.py        # 메인 — 모듈 pre + DAG + 3분할
│   │   ├── analysis_order.py               # Bottom-up rounds
│   │   ├── call_cycles.py                  # Tarjan SCC
│   │   ├── module_pre_analysis.py          # Framework: 헤더→intent
│   │   ├── range_body.py                   # ancestor_context / body_for
│   │   └── three_split_llm_fanout.py       # 3분할 fan-out 헬퍼
│   └── step7_post_processing/
│       ├── summarize_module_step.py
│       ├── enrich_table_description_step.py
│       ├── enrich_metadata_from_catalog_step.py # DBMS only
│       ├── enrich_lineage_from_catalog_step.py  # DBMS only
│       └── embed_step.py
│
├── agent_tools/                    # Agent 도구 (scan / residual 공용)
│   ├── agent_output_models.py      # ScanResult / ResidualPatternResult
│   ├── code_inspection_tools.py    # list_functions / list_skills / read_skill
│   ├── filesystem_tools.py         # ls / glob_search / grep / read_file / read_file_range
│   └── path_boundary.py            # project_root 경계 강제
│
├── data_models/
│   ├── project_and_run.py          # Project / Run dataclass
│   ├── graph_nodes.py              # Schema/Table/Column/Function/Module/Package/Variable/Constant/DataType + Rule/Example/Question
│   ├── graph_edges.py              # 19 종 엣지 dataclass
│   └── analysis_context.py         # AnalysisContext (Framework/DBMS 공통)
│
├── shared/
│   ├── config/
│   │   ├── env_catalog.py          # ★ ROBO_* env 단일 진입점 (ENV 싱글톤)
│   │   └── tool_messages.py
│   ├── workflow_runner/
│   │   ├── run_executor.py         # execute_run() — Run 1회 실행 (rmtree latest → workflow → artifact dump)
│   │   ├── langgraph_workflow.py   # build_langgraph_workflow — strategy.steps 로 StateGraph
│   │   ├── workflow_state.py       # WorkflowState TypedDict
│   │   ├── step_config.py          # step 안에서 config configurable 접근 헬퍼
│   │   └── artifact_dumper.py      # artifacts/*.json 4 종 dump
│   ├── ddl_parser/
│   │   ├── ddl_parser.py           # sqlglot 다중 방언
│   │   └── schema_resolver.py      # FK 자동 추론
│   ├── lang/
│   │   ├── call_classifier.py      # internal / external / 드롭 분류
│   │   ├── ontology.py + ontology_roles.yaml  # antlr type → 라벨/stereotype
│   │   └── source_ext.py
│   ├── llm/
│   │   ├── llm_client.py           # build_llm_client (provider 자동 인식)
│   │   ├── llm_config_loader.py    # llm_configs/<name>.yaml 로딩
│   │   ├── llm_fanout.py           # gather_with_warmup
│   │   ├── llm_output_models.py    # SummaryOutput / TableInsightsOutput / RulesExamplesOutput / ModuleSummaryOutput / TableDescription
│   │   ├── llm_pricing.py          # USD 계산
│   │   ├── llm_pricing.yaml        # provider/model → 단가
│   │   ├── structured_agent.py     # ★ run_structured_agent (submit 도구 wrap → ToolStrategy hang 회피)
│   │   ├── yaml_llm_runner.py      # run_yaml_rule (_safe_format 으로 yaml 안 `{..}` 안전)
│   │   ├── token_usage.py
│   │   ├── usage_jsonl_writer.py   # usage.jsonl append
│   │   ├── cost_budget.py          # CostBudget — $ 회로 차단기
│   │   └── middleware/             # cache_friendly_order / call_limit / cost_tracking / standard_stack / strip_cache_control / strip_dynamic_system_prompt / todo_tracker / tool_event_emitter
│   ├── neo4j/
│   │   ├── neo4j_client.py         # 비동기 driver + UNWIND 배치 + tenacity 재시도
│   │   ├── schema_constraints.py   # ensure_schema / wipe_graph
│   │   ├── rel_types.py            # 관계 타입 단일 정의처
│   │   ├── nodes/<13 종>.py        # upsert Cypher
│   │   └── edges/<19 종>.py        # upsert Cypher
│   ├── logging_and_stream/
│   │   ├── logger.py               # log_info / log_warning / log_error + attach/detach_run_file_handlers
│   │   ├── ndjson_stream.py        # NDJSONStream + logger mirror
│   │   ├── prompt_logger.py        # agent_<name>.json + rule_record
│   │   ├── message_filters.py      # 비밀 마스킹
│   │   └── regex_patterns.py
│   ├── example_mapping/
│   │   ├── builder.py              # 3분할 결과 → Rule/Example/Question + 엣지 (결정론)
│   │   └── graph_writer.py         # Neo4j 적재
│   ├── source_file_cache.py        # file_path → 텍스트 라인 캐싱
│   └── catalog_http.py             # DBMS catalog HTTP 헬퍼
│
├── prompts/
│   ├── common/large_function_one_liner.yaml  # step5 큰 함수 압축
│   ├── framework/                  # 7 yaml — _common(extends 베이스) + module_pre_analysis + 5 룰
│   ├── dbms/                       # 6 yaml — _common + 5 룰
│   └── agents/                     # scan_agent_system.md + residual_pattern_system.md
│
├── skills/                         # Agent 가 read_skill 로 읽는 자유 문서
│   ├── _templates/SKILL.md
│   ├── ejb-cmp/SKILL.md
│   ├── jpa-standard/SKILL.md
│   ├── mybatis-ibatis/SKILL.md
│   ├── ofbiz/SKILL.md
│   └── proframe-c-pfm-dbio/SKILL.md  # ★ 테스트 데이터 zapamcom*.c 대응
│
├── llm_configs/
│   ├── README.md                   # yaml 스키마 가이드
│   ├── qwen36_sglang_local.yaml    # ★ 현재 .env 활성 (timeout 600s)
│   ├── qwen36_openrouter.yaml
│   ├── gpt54_mini_openai.yaml
│   └── gpt54_nano_openrouter.yaml
│
└── .github/workflows/byte_identical.yml   # prompts/ SHA256 CI 검증
```

---

## 8. 환경변수 — 단일 진입점 `shared/config/env_catalog.py`

모든 ROBO_* env 는 영역별 dataclass 로 묶여 `ENV` 싱글톤으로 노출. 코드 어디서나
`from shared.config.env_catalog import ENV; ENV.pipeline.max_concurrency`.

명명 규칙: `ROBO_<영역>_<이름>`. 단위 suffix `_SECONDS / _MS / _TOKENS / _USD / _PCT / _RATIO`.

### LLM (`ENV.llm`)
| env | 기본 | 설명 |
|-----|------|------|
| `ROBO_LLM_CONFIG` | `""` | `llm_configs/<name>.yaml` 의 name (필수) |
| `ROBO_LLM_API_KEY` | `""` | 현 config provider 의 비밀 (필수) |

### Cache (`ENV.cache`)
| env | 기본 | 설명 |
|-----|------|------|
| `ROBO_CACHE_ENABLED` | `false` | 운영기 OFF 권장 |

### Agent (`ENV.agent`)
| env | 기본 | 설명 |
|-----|------|------|
| `ROBO_AGENT_SCAN_MAX_CALLS` | 30 | Scan Agent LLM 호출 한도 |
| `ROBO_AGENT_RESIDUAL_MAX_CALLS` | 15 | 잔여 패턴 루프 한도 |
| `ROBO_AGENT_CONTEXT_EDITING_TRIGGER_TOKENS` | 80000 | context editing 트리거 |
| `ROBO_AGENT_SCAN_REGEX_MIN_OP_RATIO` | 0.5 | op_token 인식률 최소 통과선 |

### Budget (`ENV.budget`)
| env | 기본 | 설명 |
|-----|------|------|
| `ROBO_BUDGET_RUN_USD` | 20.0 | run 당 USD 임계 ($400 사고 재발 방지) |

### Neo4j (`ENV.neo4j`)
| env | 기본 |
|-----|------|
| `ROBO_NEO4J_URI` | `bolt://localhost:7687` |
| `ROBO_NEO4J_USER` | `neo4j` |
| `ROBO_NEO4J_PASSWORD` | `""` |
| `ROBO_NEO4J_DATABASE` | `neo4j` |
| `ROBO_NEO4J_UNWIND_BATCH_SIZE` | 2000 |
| `ROBO_NEO4J_RETRY_MAX_ATTEMPTS` | 5 |
| `ROBO_NEO4J_RETRY_BACKOFF_SECONDS` | 2.0 |

### Pipeline (`ENV.pipeline`)
| env | 기본 | 설명 |
|-----|------|------|
| `ROBO_PIPELINE_MAX_CONCURRENCY` | **30** | step6 함수 분석 round 안 동시 LLM (2026-05-28 8→30 상향) |
| `ROBO_PIPELINE_FILE_CONCURRENCY` | 4 | 파일 단위 동시성 |
| `ROBO_PIPELINE_BIG_CONTEXT_THRESHOLD_TOKENS` | 1000 | 이상 → large_function_one_liner LLM |
| `ROBO_PIPELINE_RESIDUAL_SAMPLE_SIZE` | 30 | 잔여 패턴 라운드당 샘플 |
| `ROBO_PIPELINE_RESIDUAL_MAX_ROUNDS` | 3 | 잔여 패턴 최대 라운드 |
| `ROBO_PIPELINE_EMBED_ENABLED` | true | embed_step on/off |

### Catalog (`ENV.catalog`)
| env | 기본 | 설명 |
|-----|------|------|
| `ROBO_CATALOG_ENABLED` | true | DBMS catalog HTTP on/off (off 시 step skip, on+HTTP 실패 시 raise) |

### Log (`ENV.log`)
| env | 기본 | 설명 |
|-----|------|------|
| `ROBO_LOG_DIR` | `./logs` | 통째 rmtree 후 재생성 |
| `ROBO_LOG_MASK_SECRETS` | true | API key/password mask |
| `ROBO_LOG_FILE_ENABLED` | true | analyzer.log + error.log 파일 핸들러 (운영기 false 권장) |
| `ROBO_LOG_LEVEL` | INFO | DEBUG/INFO/WARNING/ERROR |

### Data (`ENV.data`)
| env | 기본 |
|-----|------|
| `ROBO_DATA_DIR` | `./data` |

---

## 9. 디자인 원칙 (M 단위 리팩토링 결과)

1. **결정론 우선**: AST 파싱·DDL 파싱·R/W 결정론 grep·Calls 결정론 분류·Bottom-up
   DAG 정렬·Example Mapping 빌더는 모두 **LLM 0**. LLM 은 의미가 필요한 곳에만.
2. **분기 0**: Strategy 패턴 — `analysis_pipelines.py` 의 `Strategy(steps=...)` 만
   declare 하면 LangGraph 빌더가 if 분기 없이 그래프 구성. 새 strategy 추가 = 한 곳만.
3. **단일 진실 (env_catalog)**: 모든 ROBO_* env 가 `shared/config/env_catalog.py` 1곳.
   매직 넘버 X. 새 env 추가 = 이 파일 1곳만 수정.
4. **silent fail 금지**: 모든 step 의 실패는 `log_error` + `stream.emit(error)` + raise.
   조용한 빈 결과 returning 금지 (잔여 패턴 큐 미해결 비움 사고 방지).
5. **KV 캐시 친화**: 3분할 system 본문 byte-identical 강제 (`prompts/<strategy>/_common.yaml`
   ← `extends`). prompts/ SHA256 은 `cli_scripts/prompt_hashes.json` 에 박혀 CI 가 변경 감지.
6. **출력 스키마**: `llm_output_models.py` 는 명시 Pydantic 모델만. `RulesExamplesOutput.examples`
   의 `given` / `then` 은 **자연어 단일 문자열** — schema 강제 부작용 (옛 `list[KeyValueItem]`
   반복 폭주 / 자유 키 `dict[str, str]` 공백 폭주) 둘 다 차단 (2026-05-30 사고 후 단순화).
7. **submit 도구 패턴**: Agent 출력 강제는 LangChain `response_format` 직접 전달 대신
   `_wrap_pydantic_as_submit_tool` 로 wrap. qwen+SGLang xgrammar 동시 마스킹 hang 회피.

---

## 10. 자주 부딪치는 사항

- **antlr JSON 디렉토리 없음** — `step2_load_ast` 가 `FileNotFoundError` raise.
  → `c:/uEngine/robo/antlr-parser` 먼저 실행해 `data/analysis/` 채우기.
- **`logs/` 내용이 다음 run 에 사라짐** — 정상. `_reset_logs_root` 가 매 run 통째 rmtree.
- **Neo4j 그래프가 매 run 비워짐** — 정상. `wipe_graph()` 가 `DETACH DELETE` 강제
  (테스트 정책). 옛 run 잔재로 인한 검증 사각지대 차단.
- **Scan Agent 가 도구 호출 안 함** — `submit_scan` 호출 누락 시 `ValueError` raise.
  system prompt 의 도구 호출 안내 확인 (`prompts/agents/scan_agent_system.md`).
- **`run_yaml_rule` 의 `{...}` 치환 오류** — `_safe_format` 이 알려진 키만 치환하므로
  yaml 안 자유 텍스트의 `{key:val}` 영향 0 (2026-05-28 도입).
- **timeout 5분 초과** — `qwen36_sglang_local.yaml` 의 `timeout_seconds: 600` (2026-05-28
  300→600 상향, function_rules_examples 큰 출력 1900+ 토큰 5~10분 실측).

---

## 11. 관련 문서

- [`llm_configs/README.md`](llm_configs/README.md) — LLM yaml 스키마
- [`skills/_templates/SKILL.md`](skills/_templates/SKILL.md) — 새 skill 작성 템플릿
- [`skills/proframe-c-pfm-dbio/SKILL.md`](skills/proframe-c-pfm-dbio/SKILL.md) — 테스트 데이터 대응 skill (시퀀스/본R/W 정규식 분리, lookbehind)
- `c:/tmp/robo-pipeline-study/INDEX.md` — 영역별 학습노트 (A 오케스트레이션 / B parse·load / C scan·linking / D context·analyze / E post·skills·prompts / F1 llm / F2 neo4j·logging)
- `C:\Users\roede\.claude\plans\2026-05-21-pipeline-test-fixes-plan.md` — 단일 진실 플랜
- `C:\Users\roede\.claude\plans\2026-05-20-refact-overhaul-plan.md` — 재건축 M1~M16 이력
