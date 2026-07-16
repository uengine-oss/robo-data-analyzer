# ROBO Data Analyzer

> **레거시 코드베이스를 "읽지 않고" 이해한다.**
> 오래된 소스 코드(자바·C·파이썬 등) 또는 Oracle PL/SQL 패키지를 정적 분석해
> **Neo4j 지식 그래프**로 바꾸는 파이프라인. 코드 구조 + 호출 관계 + 코드↔테이블
> 읽기/쓰기 + 함수·모듈 요약 + **비즈니스 규칙** + **부품(컴포넌트) 군집** + **건강지표**
> 까지 한 번에 뽑아낸다.

---

## ⚠️ 최근 변경 (2026-07-13) — 읽고 시작하세요

전수 DRY·정합 조사([보고서](docs/작업보고내용/2026-07-13-analyzer-DRY조사.md))에서 **헌법 위반 5종**이 확인되어 두 스펙으로 시정했다.

| 무엇이 바뀌었나 | 왜 | 스펙 |
|---|---|---|
| **그래프 wipe 가 라벨을 안 가린다** — `MATCH (n) DETACH DELETE n`. 같은 DB 의 **catalog·architect 데이터도 함께 삭제**된다 (분석 후 재등록 필요) | 헌법 *"매 run = full rebuild"* 복원. 옛 `DataSource` 예외는 자식(`:Schema`/`:Table`/`:Column`)을 못 살려 **껍데기만 남는 반쪽 상태**였다 | [045](specs/045-graph-integrity-restore/spec.md) |
| **원문(`code_text`) 은 step2 가 모든 노드에 부여** (코드노드·파일·변수·상수·데이터타입) | 옛 규칙(step6 에서만 코드노드에)은 선언 노드의 노드 패널을 비게 했다. **spec 016 FR-004 supersede** | [045](specs/045-graph-integrity-restore/spec.md) |
| **소스 read 실패가 로그로 표면화** + `code_text_missing` 카운터 | 종전엔 로그 0건으로 삼켜져 "성공"으로 끝났다 (헌법 IV 위반) | [045](specs/045-graph-integrity-restore/spec.md) |
| **`analysis.json` 에 원문이 안 담긴다** — 원문의 집은 `ast.json` 하나 | 같은 원문 842 KB 가 두 파일에 중복이었다 | [045](specs/045-graph-integrity-restore/spec.md) |
| **전략 분기 0** — `analyze_code_step` 이 `Strategy.code_analyzer` 를 **주입**받는다 | 헌법 II 복원 (`strategy.name == "dbms"` 하드분기 제거) | [046](specs/046-dry-single-truth/spec.md) |
| **지원 확장자·루틴 라벨·MERGE 쿼리·소스 캐시 단일 진실화** | 헌법 III 복원. 확장자는 **실제로 갈라져 있었다**(4종 vs 14종) → `.kt` 프로젝트가 **0노드 "성공"** 으로 끝났다. 이제 **명시적 실패** | [046](specs/046-dry-single-truth/spec.md) |
| **죽은 코드 삭제** — 모듈 1 · 함수 4 · env 키 6 (특히 `ROBO_LOG_MASK_SECRETS` 는 **효과 없는 거짓 설정**이었다) | | [046](specs/046-dry-single-truth/spec.md) |

---

## 0. 한눈에 — 무엇을 / 누구에게 / 무슨 결과

### 0.1 이게 뭐 하는 거냐 (비개발자용 30초 설명)

수십만 줄짜리 **오래된 시스템**을 물려받았는데, 만든 사람은 떠났고 문서도 없다고 하자.
이 도구는 사람이 코드를 한 줄씩 읽는 대신, **자동으로 코드를 훑어 "지도"를 그려준다.**

- 어떤 함수가 어떤 함수를 부르는지 (호출 지도)
- 어떤 코드가 어떤 **DB 테이블**을 읽고/쓰는지 (데이터 흐름)
- 각 함수·모듈이 **무슨 일을 하는지** (자연어 요약)
- 코드 속에 숨은 **업무 규칙**(예: "잔액이 0보다 크면 출금 허용") (Example Mapping)
- 비슷한 일을 하는 함수끼리 묶은 **부품 단위**와 그 이름
- 어디가 **위험한지**(너무 크거나·얽혀있거나·테이블을 너무 많이 만지는 함수)

### 0.2 입력 → 출력 (한 장 요약)

```
 [입력]                          [ROBO Analyzer]                      [출력]
 ─────────                       ──────────────                      ─────────
 소스 코드 디렉토리   ─┐                                    ┌─►  Neo4j 지식 그래프
 (*.c *.java *.py …)   │     ┌──────────────────────┐       │     (노드 12종 · 엣지 16종)
                       ├──►  │  LangGraph 11~13 step │  ──►  ┤
 DDL (*.sql)           │     │  결정론 + 선택적 LLM   │       ├─►  artifacts/*.json (5종)
 antlr AST (*.json)   ─┘     └──────────────────────┘       │     + usage_summary.json
                                                            └─►  실시간 NDJSON 스트림
```

### 0.3 핵심 사실

| 항목 | 내용 |
|------|------|
| **입력 대상(타겟)** | 레거시 **응용 코드**(Framework: 자바/C/파이썬/Go/TS 등) 또는 **DBMS**(Oracle PL/SQL 패키지) |
| **누구를 위해** | 레거시를 인수해 **현대화·재구축·마이그레이션**해야 하는 아키텍트/개발자 (downstream: robo-architect 그래프 뷰어) |
| **출력** | Neo4j 그래프(UPSERT) + `logs/analysis.run/latest/artifacts/*.json` 5종 + `usage_summary.json` + `health_report.json` + NDJSON 스트림 |
| **엔진** | FastAPI + LangGraph StateGraph + LangChain/deepagents Agent |
| **2개 Strategy** | `framework`(응용코드) / `dbms`(PL/SQL) — 확장자로 자동 감지, `--strategy`로 override |
| **LLM 정책** | 모델+배포환경 1셋 = `llm/configs/<name>.yaml` 1개. `.env`의 `ROBO_LLM_CONFIG` 1줄로 전환 |
| **결정론 우선** | DDL·AST 파싱 / 링킹 grep / 호출 분류 / Bottom-up DAG / 컴포넌트 군집 / 건강지표 = **LLM 0회**. LLM은 "의미"가 필요한 곳에만 |
| **실행 단위** | `/robo/analyze` 1회 = LangGraph workflow 1회 = `Run` 1개. 매 Run 시작 시 그래프 wipe + `logs/` rmtree |
| **위치** | `d:/work/robo/project/robo-data-analyzer` (origin: `github.com/uengine-oss/robo-data-analyzer`) |

> 더 깊은 영역 노트는 메모리(`[[manual_overview]]` · `[[manual_run]]`) 및 학습노트 참조.

---

## 1. 외부 의존성 / 사전 준비

### 1.1 별도 repo — antlr-code-parser (소스를 직접 파싱하지 않음)

ROBO Analyzer 는 소스를 **직접 파싱하지 않는다.** 외부 Java 파서인 **antlr-code-parser**
가 미리 만들어 둔 **JSON AST** 를 `data/analysis/` 에서 읽기만 한다
(`pipeline/steps/step2_load_ast/antlr_json_reader.py`).

| repo | 위치(로컬) | 역할 |
|------|-----------|------|
| `robo-data-analyzer` | `d:/work/robo/project/robo-data-analyzer` | 본 파이프라인 (Python) |
| `antlr-code-parser` | `d:/work/robo/project/antlr-code-parser` | **Java 외부 파서** — 소스 → AST JSON |

origin: `github.com/uengine-oss/robo-data-analyzer` · `github.com/uengine-oss/antlr-code-parser`

### 1.2 Neo4j 서버 (필수)

UPSERT 가 매 step 마다 일어나므로 **Neo4j 인스턴스 필수**. `.env` 의
`ROBO_NEO4J_URI / USER / PASSWORD / DATABASE` 로 설정.
analyzer 는 매 Run 시작에 `MATCH (n) DETACH DELETE n` 으로 그래프를 wipe → `ensure_schema()`
로 제약/인덱스를 재확인한다(idempotent). **⚠️ 라벨을 가리지 않는다 — 같은 DB 를 공유하는 다른
서비스의 데이터(catalog 의 `:DataSource`/`:Schema`/`:Table`/`:Column`, architect 의 설계 그래프)도
함께 지워진다.** 헌법의 full-rebuild 정책상 의도된 동작이며, 해당 서비스는 분석 후 자기 데이터를
다시 등록/생성해야 한다 (spec 045). HTTP 헤더 `X-Neo4j-*` 로 요청별 override 도 가능
(Electron 데스크톱 임베드 대응 — `Neo4jOverride.from_headers()`).

### 1.3 LLM 엔드포인트

`llm/configs/<name>.yaml` (4개 동봉):

| yaml | provider | 비고 |
|------|----------|------|
| `qwen36_sglang_local` | sglang | 사내 SGLang 배포 (Qwen3.6) — 현재 `.env` 활성 (`frentis-ai-model`) |
| `qwen36_openrouter` | openrouter | 같은 모델을 OpenRouter 로 |
| `gpt54_mini_openai` | openai | OpenAI 본점 gpt-5.4-mini |
| `gpt54_nano_openrouter` | openrouter | gpt-5.4-nano (Scan Agent 환각 회피 대체용) |

> 새 config 추가 = yaml 1개 + `.env` 의 `ROBO_LLM_CONFIG=<name>` 1줄. 코드 변경 0.
> 자세한 스키마: [`llm/configs/README.md`](llm/configs/README.md).

### 1.4 Python 의존성

`requirements.txt` 는 코드가 실제 import 하는 패키지만 **범위(`>=`) 핀**으로 정리
(resolver 가 호환 최신 세트를 맞춤 — langchain **1.x** + deepagents 세트). 핵심:

- `langchain >=1.0` / `langchain-core / langchain-openai / langchain-anthropic >=1.0` / `langgraph >=1.0` / `langgraph-checkpoint >=2.1`
- `deepagents >=0.2` — Scan/잔여 패턴 에이전트 (TodoListMiddleware 등)
- `pydantic >=2.10` / `pydantic-settings >=2.7` — 출력 스키마 강제(환각 차단)
- `sqlglot >=27` — DDL 다중 방언 (Oracle/PG/MySQL/MSSQL)
- `antlr4-python3-runtime >=4.13` — antlr JSON 호환만 (파싱 자체는 외부 java)
- `neo4j >=5.27`
- **`leidenalg >=0.10` / `igraph >=0.11`** — `build_components_step` 컴포넌트 군집화(Leiden)
- `sentence-transformers >=3.3` — 임베딩 (`paraphrase-multilingual-MiniLM-L12-v2`)
- `fastapi >=0.115` / `uvicorn >=0.34`
- `tenacity >=9` — provider rate-limit / Neo4j TransientError 재시도
- `httpx >=0.28` / `aiohttp >=3.11`

설치:
```bash
cd d:/work/robo/project/robo-data-analyzer
pip install -r requirements.txt
```

---

## 2. 입력 데이터 규약

### 2.1 `ROBO_DATA_DIR` 구조 (CLI `analyze_data_dir.py` 기준)

```
ROBO_DATA_DIR/                  # 기본값 = robo-data-analyzer 옆 형제 ./data (cwd 무관, .env 의 ROBO_DATA_DIR 로 override)
├── ddl/
│   ├── schema.sql              # 공통 DDL — Oracle 방언 가능, sqlglot 다중 방언
│   └── *.sql
├── source/
│   ├── <project_a>/            # 분석 대상 1
│   │   └── *.c | *.java | *.py | ...
│   └── <project_b>/
└── analysis/                   # antlr-code-parser 가 미리 생성한 AST JSON (source/ 미러)
    └── <project_a>/
        └── *.json
```

- 하위 `<project_x>/` 없이 소스가 `source/` 직속이면 `source/` 자체가 1 프로젝트.
- DDL 디렉토리는 없어도 동작 (Table 노드 0개로 진행, R/W 엣지 안 생김).
- `analysis/` 가 없거나 비어 있으면 `step2_load_ast` 가 `FileNotFoundError` 명시 raise.

### 2.2 Strategy 자동 감지 — `pipeline/programming_languages.py`

`project_dir.rglob("*")` 의 확장자 기반:

- `.sql / .pkb / .pks / .pls / .ddl` → **`dbms`**
- `.java / .kt / .py / .go / .c / .cpp / .h / .hpp / .ts / .js / .rb / .rs / .php / .scala` 하나라도 → **`framework`** (우선)
- 둘 다 없으면 `framework` (안전 기본값)

`--strategy` CLI 인자 또는 API `strategy` 필드로 override 가능.

---

## 3. 실행 방법

### 3.1 CLI — 가장 자주 쓰는 진입점

```bash
cd d:/work/robo/project/robo-data-analyzer

# source/ 안 모든 프로젝트 일괄 분석
python cli_scripts/analyze_data_dir.py

# 1 프로젝트만 (source/ 하위 폴더 이름)
python cli_scripts/analyze_data_dir.py <project_name>

# strategy 강제 override
python cli_scripts/analyze_data_dir.py <project_name> --strategy dbms
```

인자: `target`(생략 시 source/ 전체) + `--strategy {framework,dbms}`(생략 시 자동 감지).
NDJSON 스트림이 콘솔에 실시간 출력 (`[PHASE]` / `[TOOL ]` / `[TODO ]` / `[ERROR]` /
`[WARN ]` / `[END  ]`). Windows cp949 콘솔 대응 — `sys.stdout.reconfigure(utf-8)` 자동.

### 3.2 FastAPI 서버

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
# 또는
python -m main
```

**엔드포인트 — `POST /robo/analyze` (분석 1개) + `POST /robo/analyze/control` (제어) + `GET /` (health).**

```http
POST /robo/analyze
Content-Type: application/json
X-Neo4j-Uri / X-Neo4j-User / X-Neo4j-Password / X-Neo4j-Database   # 선택 — 요청별 Neo4j override

{
  "project_root": "d:/work/robo/project/data/source/<name>",  # (역호환) 받되 무시(inert) — 소스는 항상 ENV.data.dir/source
  "strategy": "framework",            # "framework" | "dbms"
  "ddl_dir": "d:/work/robo/project/data/ddl",                 # 선택
  "has_xml_mapper": false,
  "locale": "ko",                     # "ko" | "en"
  "datasource": null                  # 선택 — catalog 샘플 행 추출용 datasource 명 (null = skip)
}

→ 200 OK, Content-Type: application/x-ndjson, long-lived stream
```

```http
POST /robo/analyze/control
{ "action": "pause" | "resume" | "stop" }
→ { "success": true, "action": "...", "status": { "isPaused": bool, "isStopped": bool } }
   (진행 중 분석 없으면 409 "no active analysis")
```

"동시 분석 1개" 모델(payload 에 run_id 없음). 일시정지는 같은 세션 안의 soft hold —
서버를 끄면 run 도 끝난다(디스크 재개 X).

> 진행 상태 폴링 엔드포인트는 **없다.** 진행은 `/analyze` 의 NDJSON 스트림 단일 채널.
> 클라이언트가 stream 을 끊으면 그 run 종료.

### 3.3 보조 CLI (`cli_scripts/`)

| 스크립트 | 용도 |
|---------|------|
| `analyze_data_dir.py` | ★ 운영자 표준 진입점 (위 3.1) |
| `baseline_runner.py` | 여러 프로젝트 일괄 + 종합 `baseline_summaries.json` 생성 |
| `check_byte_identical.py` | `llm/prompts/**` SHA256 ↔ `prompt_hashes.json` 검증 (CI 보호장치) |
| `refresh_pricing.py` | `llm/client/usage/llm_pricing.yaml` 갱신 보조 (현재 yaml + 공식 URL 안내) |
| `reset_neo4j_schema.py` | Neo4j 제약/인덱스 1회 리셋 (오염 정리 — 평상시 불필요) |

---

## 4. 파이프라인 — Strategy 별 step 흐름

`pipeline/analysis_pipelines.py` 의 `Strategy` 데이터클래스가 step 함수 tuple 을 **declare 만**
하면, `pipeline/execution/langgraph_builder.py:build_langgraph_workflow()` 가 **if 분기 0개** 로
LangGraph StateGraph 노드를 순서대로 추가한다. step1~step7 **폴더(7 phase)** 구조가 흐름
그대로 드러나며, 실제 실행 step 함수는 Framework 11개 / DBMS 13개.

> tuple 로 묶인 step 들은 **병렬 그룹**(LangGraph superstep) — 한 단계에서 동시에 fan-out 후
> 다음 step 에서 합류. 공유 `llm_semaphore` 로 서버 총 동시수를 제어.

### 4.1 Framework Pipeline

```
parse_ddl_step
 → load_ast_step
 → scan_codebase_step              ★ Framework only — Scan Agent 1 회
 → link_deterministic_step          (stage1) — 정규식 grep, LLM 0
 → link_calls_by_name_step          ★ Framework only — 호출 결정론 분류 + wrapper, LLM 0
 → link_residual_patterns_step      ★ Framework only — 잔여 패턴 루프 (Agent 라운드)
 → build_context_step               (큰 함수 one_liner LLM, 그 외 결정론)
 → fetch_table_samples_step         (옵션·전략독립 — catalog 샘플 행, analyze 전)
 → analyze_code_step                (module pre-analysis + Bottom-up DAG + 3분할 fan-out)
 → ┌ summarize_module_step       ┐  ← 병렬 그룹 (4 step 동시, 공유 semaphore)
   │ enrich_table_description_step │
   │ build_components_step         │  ★ Leiden 군집 → component_id 속성 SET + 이름 LLM
   └ health_metrics_step          ┘  ★ 건강지표 (결정론·LLM 0)
 → embed_step                       (ROBO_PIPELINE_EMBED_ENABLED=true 일 때만)
```

### 4.2 DBMS Pipeline

```
parse_ddl_step
 → load_ast_step
 (scan_codebase_step 없음 — DBMS 는 Scan Agent 안 씀)
 → link_dbms_tables_step            ★ DBMS only — DML range → DDL 테이블 결정론 매칭
 → link_calls_by_ast_step           ★ DBMS only — AST CALL 노드 분류 (빌트인·자기루프 드롭)
 (잔여 패턴 루프 없음 — DBMS 는 에이전트 링킹 안 씀)
 → build_context_step               (parent/sibling 트리 컨텍스트 보강)
 → fetch_table_samples_step
 → analyze_code_step                (형제 함수 batch 를 framework 처럼 3분할(요약/테이블/비즈로직) 콜로 — ROBO_PIPELINE_BATCH_MAX_TOKENS)
 → summarize_module_step
 → enrich_table_description_step
 → enrich_lineage_from_catalog_step ★ DBMS only — catalog ETL 리니지
 → build_components_step
 → health_metrics_step
 → embed_step
```

> `enrich_metadata_from_catalog_step` 은 **제거됨** — 미존재 엔드포인트(`GET /tables/<fqn>/metadata`)
> 기반이라 동작 X. 테이블 메타(샘플)는 `fetch_table_samples_step` (sample-context) 로 대체.
> 새 strategy 추가 = `pipeline/analysis_pipelines.py` 의 `Strategy(...)` declare 만. 코드 분기 0.

---

## 5. Step 별 상세 — 입력 / 동작 / 출력

각 step 함수 signature 는 표준 `async def step(state: dict, config: RunnableConfig) -> dict`.
`step_utils` 로 `codebase / run / cost_budget / neo_client / stream / strategy / llm_semaphore / pause` 접근.

### Step 1 — `parse_ddl_step` (LLM 0)

**입력**: `codebase.ddl_dir`(또는 `codebase.root`) 안의 `*.sql`.
**파서**: `sqlglot` 다중 방언 → `pipeline/steps/step1_parse_ddl/ddl_parser.py`. FK 는 **DDL 에 명시된 것만** 추출한다
(자동추론 없음 — 옛 `schema_resolver.py` 는 호출부 0건이라 삭제, spec 046).
**출력 노드/엣지**: `SCHEMA` · `TABLE` · `COLUMN` 노드 + `HAS_COLUMN` · `BELONGS_TO` · `FK` 엣지.
state 누적 + Neo4j UNWIND 배치 UPSERT.

### Step 2 — `load_ast_step` (LLM 0)

**입력**: `data/analysis/<project>/**/*.json` (antlr-code-parser 가 미리 생성).
**파서**: `antlr_json_reader.py` — 언어별 ontology 매핑. `reference_linker.py` 가
REFERENCES / INIT_BY 결정론 식별자 매칭.
**출력 노드**: `FUNCTION` · `MODULE` · `PACKAGE` · `VARIABLE` · `CONSTANT` · `DATA_TYPE`.
**출력 엣지**: `PARENT_OF` · `HAS_MEMBER`(그릇→루틴/데이터) · `BELONGS_TO`(그릇→폴더) ·
`NEXT`(DBMS 구문 실행 흐름) · `REFERENCES` · `INIT_BY` + state `call_sites`(raw — step4 분류) · `import_refs`.
**실패 정책**: AST 디렉토리 없으면 명시 `FileNotFoundError` (silent skip 금지).

### Step 3 — `scan_codebase_step` (Framework only, LLM Agent 1 회)

**입력**: `project_root` + state 의 `functions / tables` + `scanner_input_collector` 단서
(manifest / 확장자 카운트 / 카테고리).
**에이전트**: 단일 Scan Agent invoke (`scan_agent.py`). 도구: `ls / glob_search / grep /
read_file / read_file_range`(project_root 경계 강제) · `list_functions` · `list_skills` /
`read_skill`(`skills/<name>/SKILL.md`) · `verify_table_regex`(정규식→실제 소스 매치율 검증)
· (`write_todos` 는 deepagents 자동).
**시스템 프롬프트**: `prompts/agents/scan_agent_system.md` (byte-identical 보존 — CI 검증).
**출력 강제**: `submit_scan` 도구 1회 → `ScanResult` (submit-도구 wrap 으로 qwen+SGLang grammar
mask hang 회피 — `structured_agent.py`).
**산출 `ScanResult`**: `table_access_patterns`(검증 통과분 — `<op_token>`/`<entity>` named group +
op→SELECT/INSERT/… 매핑) · `framework_functions`(호출 그래프 드롭 대상) · `call_wrappers`(인자가
실제 호출대상인 래퍼) · `codebase_notes`(step6 LLM prefix 로 항상 주입 — KV 캐시 친화) · `ddl_transform`.
**한도**: `ROBO_AGENT_SCAN_MAX_CALLS`(기본 30) 초과 시 raise.

### Step 4 — Linking

#### 4-A `link_deterministic_step` (Framework, stage1, LLM 0)
`ScanResult.table_access_patterns` 의 검증 정규식으로 `RegexTableLinker` 가 project root
전체를 grep → `READS` / `WRITES` 엣지. 매칭 실패는 `unresolved_cases` 큐(stage3 가 소비).

#### 4-B `link_calls_by_name_step` (Framework, LLM 0)
step2 의 `call_sites` 를 코드 목록 대조로 분류: 프로젝트 함수 → `internal` `CALLS`,
`framework_functions` 런타임/매크로 → 드롭, 그 외 → `external` `CALLS`. `call_wrappers` 처리.

#### 4-B' `link_calls_by_ast_step` (DBMS, LLM 0)
antlr `CALL` 노드를 분류 → `CALLS`. 빌트인(`DBMS_`/`UTL_`/…) 드롭, 콜리 단순명이 정의 노드와
일치하면 internal, 그 외 external(`:FUNCTION:EXTERNAL` 스텁 생성), 자기루프 드롭. AST 라 문자열·주석 호출 오인식 0.

#### 4-B'' `link_dbms_tables_step` (DBMS, LLM 0)
**모든 노드의 자기 줄**(자기 범위 − 자식 줄범위) 에서 **DDL 테이블명 단어경계 검색**. 부모
(`MERGE INTO…USING(SELECT…)`)도 자기 줄 스캔 → 누락·중복 0. 첫 DML 키워드(SELECT/WITH/INSERT/
UPDATE/DELETE/MERGE)가 READS/WRITES 결정. 주석(`--`,`/* */`) 제거 후 매칭(문자열은 보존 — EXECUTE IMMEDIATE 리터럴 SQL 포착).

#### 4-C `link_residual_patterns_step` (Framework only, LLM Agent 라운드 루프)
stage1 의 `unresolved_cases` → **잔여 패턴 루프**(케이스 추적이 아니라 샘플에서 공통 패턴을
LLM 이 찾게 해 코드가 일괄 적용). 시스템 프롬프트 `prompts/agents/residual_pattern_system.md`,
라운드당 샘플 `ROBO_PIPELINE_RESIDUAL_SAMPLE_SIZE`(30) · 최대 라운드 `_RESIDUAL_MAX_ROUNDS`(3)
· 호출 한도 `ROBO_AGENT_RESIDUAL_MAX_CALLS`(15). `submit_residual` → `verify_table_regex` 통과분만 적용.
**state 정책**: `unresolved_cases` 는 reducer 없는 평키 — stage1 raw 를 stage3 최종 잔여로 덮어쓴다.

### Step 5 — Context

#### 5-A `build_context_step` (큰 함수만 LLM 1 회)
모든 `Function` 에 `AnalysisContext` 생성: 식별/위치/`reads·writes·callers·callees`(정렬 tuple)
/ `referenced_symbols`(REFERENCES+INIT_BY 결정론) / `one_liner`(본문 토큰 >
`ROBO_PIPELINE_BIG_CONTEXT_THRESHOLD_TOKENS`(1000) 면 `large_code_one_liner.yaml` 압축 LLM
1회(작은 모델) → callee 본문 통째 노출 차단). `context_builder.augment()`:
Framework=항등, DBMS=`DbmsContextBuilder` 가 parent/sibling 트리 메타를 `extra` 에 채움.

#### 5-B `fetch_table_samples_step` (옵션·전략독립, LLM 0)
catalog `sample-context` 엔드포인트로 테이블별 **실제 샘플 행**(최대 5행) 수집 → state
`table_sample_data`. 100개씩 청크 병렬 HTTP. `ROBO_CATALOG_ENABLED=false` / `datasource` 미지정 /
테이블 0개 면 graceful skip. 청크 실패 시 그 테이블만 샘플 없이 진행(분석 계속).

### Step 6 — `analyze_code_step` (모듈 사전분석 + Bottom-up DAG + fan-out)

0. **모듈 사전분석** (Framework only — `module_pre_analysis.yaml`): 모듈 헤더 주석 → `Module.intent`.
   공통 context prefix 로 주입.
1. **cycle 식별**: `CALLS`+`PARENT_OF` 입력. **반복형 Tarjan SCC**(`call_cycles.py`) →
   `is_in_cycle` 메타(cycle 내부는 callee summary 없이 분석).
2. **Bottom-up rounds**: leaf → root 정렬. 같은 round 는 `asyncio.Semaphore`
   (`ROBO_PIPELINE_MAX_CONCURRENCY`, **기본 30**) + `as_completed`, round 간 순차. 첫 함수 warmup
   (`await run_summary`) 후 나머지 병렬 — KV prefix 캐시 워밍.
3. **함수 분석**:
   - **Framework — 3분할 fan-out** (`three_split_llm_fanout.py`):
     **#1 `code_summary`**(항상 → `SummaryOutput`: 요약 + stereotype + logical_name) →
     `gather(`**#2 `code_rules_examples`**(항상 → rules+examples+questions)`,`
     **#3 `code_table_insights`**(R/W 테이블 있을 때만 → table+column insights)`)`.
     3분할 입력 = [공통 context byte-identical] + [#3 전용 자식 rules]. KV 캐시 prefix 적중
     (`prompts/framework/_common.yaml` ← `extends`).
   - **DBMS — batch 3분할 분석** (`batch_analyze.py` + `batch_planner.py`): 같은 유효 부모의 형제
     함수를 입력 토큰 `ROBO_PIPELINE_BATCH_MAX_TOKENS`(1500) 한도로 **배치(그룹)** 로 묶고,
     framework 처럼 관심사별 3콜(`code_summary_batch` / `code_rules_examples_batch` /
     `code_table_insights_batch`)로 분석 — 콜당 "한 관심사 × 노드 리스트" 평탄 출력(출력 폭주 방지),
     `node_key`(줄범위)로 fan-in.
4. **즉시 적재**: 함수 1개 완료 즉시 `update_summaries()` + `node_event`/`relationship_event`
   NDJSON emit. 배치 누적 없음.
5. **Example Mapping 빌더** (`pipeline/steps/step6_analyze_code/example_mapping_builder.py`, 결정론): 결과 → `RULE`(공유,
   `rule_hash`) / `EXAMPLE` / `QUESTION` 노드 + `HAS_RULE` / `HAS_EXAMPLE` / `HAS_QUESTION` /
   `NEXT` / `BRANCH` / `AFFECTS_TABLE` 엣지. (per-function 정보는 `HAS_RULE` 엣지 속성)

### Step 7 — Post Processing

- **`summarize_module_step`** — Function ↔ Module `file_path + 줄범위` 매칭(미매칭은 파일레벨
  MODULE) → 멤버 함수 summary 종합 → `module_summary.yaml` LLM 1회 → `MODULE.summary / stereotype`.
- **`enrich_table_description_step`** — 함수별 `table_insights` 를 테이블별로 누적 →
  `table_description.yaml` LLM 1회 → `TABLE.analyzed_description` + `COLUMN.analyzed_description`
  (DDL/카탈로그 1차 description 과 격리).
- **`build_components_step`** ★ — 내부 `CALLS` 엣지만으로 **Leiden 군집**(leidenalg/igraph, seed=42
  결정론, 최소 크기 2). 컴포넌트마다 멤버 headline 모아 `component_naming.yaml` LLM 1회 →
  이름. **합성 노드 없음** — 각 멤버 `FUNCTION` 노드에 `component_id`/`component_name` 속성을
  배치 UNWIND SET (멤버십 단일 진실). 그룹↔그룹 관계는 `CALLS`+`component_id` 로 온디맨드 파생.
- **`health_metrics_step`** ★ (결정론·LLM 0) — `FUNCTION` 마다 `fan_in / fan_out / loc /
  in_cycle / table_coupling / god_flag / risk(low·medium·high)` 계산 → Neo4j SET +
  `logs/analysis.run/latest/health_report.json`(risk 분포 + top 20 위험 함수).
  임계: LOC≥300 & fan_out≥15 → god_flag; in_cycle 또는 god_flag → high.
- **`enrich_lineage_from_catalog_step`** (DBMS only) — catalog `/lineage` → `READS`/`WRITES`
  엣지 추가(매칭 실패 시 `__catalog__:<source>` 가상 노드).
- **`embed_step`** — `paraphrase-multilingual-MiniLM-L12-v2` 로 Table/Column.description +
  Function.summary 임베딩 → Neo4j `embedding` property. `ROBO_PIPELINE_EMBED_ENABLED=true` 일 때만.

---

## 6. 출력 / 산출물

### 6.1 디렉토리 구조 — `ROBO_LOG_DIR/` (기본 `./logs`)

매 run 시작 시 **`logs/` 통째 rmtree** 후 재생성 (`executor.py:_reset_logs_root`).

```
logs/
├── analysis.run/latest/
│   ├── analyzer.log               # INFO+ (ROBO_LOG_FILE_ENABLED=true 일 때)
│   ├── error.log                  # WARNING+ — 사고 추적 전용
│   ├── usage.jsonl                # 모든 LLM 호출 토큰/비용 NDJSON (agent + yaml_rule)
│   ├── usage_summary.json         # usage.jsonl 집계 (total + per_agent_model)
│   ├── health_report.json         # ★ 함수 건강지표 (risk 분포 + top 위험 함수)
│   └── artifacts/
│       ├── ast.json               # step1+2 (schema/table/column + function/module/… + 구조 엣지)
│       ├── linking.json           # step4 (reads_writes + calls + unresolved + scan_result)
│       ├── analysis_contexts.json # step5 (code_id → AnalysisContext)
│       ├── analysis.json          # step6+7 (summary + rules/examples/questions; 컴포넌트는 FUNCTION.component_id 속성)
│       │                          #   ★ 원문(code_text)은 안 담는다 — 원문의 집은 ast.json 하나 (spec 045)
│       └── graph_extras.json      # 매핑 안 된 state 키 auto-dump (신규 키 누락 방지 catch-all)
└── llm_prompts/latest/
    └── ...                         # 룰별 LLM 입력 미리보기 (prompt_logger)
```

### 6.2 NDJSON 스트림 이벤트 — `application/x-ndjson`

| `type` | 의미 |
|--------|------|
| `phase_event` | step 시작/완료 (`event=start/node_done/stopped/complete`, `node`, `run_id`) |
| `tool_event` | Agent 도구 호출 (tool / message / elapsed_ms / error) |
| `todo_update` | TodoListMiddleware 의 todo 진행 |
| `processing_event` | step 처리 단위 진행 (function / module / table / split) |
| `node_event` | 그래프 노드 생성/갱신 (op=merge, label / id / props) |
| `relationship_event` | 그래프 엣지 생성/갱신 (op=merge, rel_type / from / to) |
| `error` | 오류 (silent fail 금지 — 명시 emit) |
| `warning` | 경고 |
| `stream_end` | 스트림 종료 신호 — consumer break |

> `emit()` 이 동시에 `logger.info` 호출 → 모든 NDJSON 이벤트가 자동으로 `analyzer.log` 에 적재.

### 6.3 Neo4j 그래프 스키마 — 노드 12종 / 엣지 16종

**노드 12종 (`shared/neo4j/nodes/` 12파일, 라벨 UPPER_SNAKE)** — 모든 노드 식별 키 = **`id` 하나
(종류 무관 균일)**. 함수·메서드·구문은 모두 `CodeNode` dataclass 1종이며 라벨만 자기 antlr type
(예: `METHOD` / `FUNCTION` / `PROCEDURE` / `SELECT` / `IF` …, 열린 집합):

| 구분 | 노드 (key) |
|------|-----------|
| DB | `SCHEMA`(id) · `TABLE`(id) · `COLUMN`(id) |
| 코드 | `CodeNode`(id, 라벨=antlr type) · `MODULE`(id) · `PACKAGE`(id) · `VARIABLE`(id) · `CONSTANT`(id) · `DATA_TYPE`(id) |
| Example Mapping | `RULE`(id) · `EXAMPLE`(id) · `QUESTION`(id) |

> 컴포넌트(부품)는 합성 `:COMPONENT` 노드로 만들지 않는다 — Leiden 군집 멤버십은
> **`FUNCTION.component_id`/`component_name` 속성**이 단일 진실(`build_components_step`).
> 이름·그룹간 관계는 거기서 파생(시각화=hull 오버레이, 그룹↔그룹 호출=`CALLS`+`component_id` 온디맨드 GROUP BY).

**엣지 16종 (`shared/neo4j/rel_types.py` 단일 정의처)**:

| 구분 | 엣지 |
|------|------|
| 코드 구조 | `CALLS`(scope) · `PARENT_OF` · `HAS_MEMBER`(그릇→루틴/데이터) · `BELONGS_TO`(그릇→폴더) · `NEXT`(DBMS 구문 흐름) |
| 코드 참조 | `REFERENCES` · `INIT_BY` |
| 코드↔DB | `READS`(dml_type) · `WRITES`(dml_type) |
| DB 구조 | `HAS_COLUMN` · `BELONGS_TO` · `FK` |
| Example Mapping | `HAS_RULE` · `HAS_EXAMPLE` · `HAS_QUESTION` · `NEXT` · `BRANCH` · `AFFECTS_TABLE` |

> 새 노드/엣지 = `data_models/graph_nodes.py`(또는 `graph_edges.py`) + `shared/neo4j/nodes/<new>.py`
> (또는 `edges/<new>.py`) + `rel_types.py` 상수. 관계명은 `rel_types.py` 에서만 정의(Cypher 직박 금지).

---

## 7. 디렉토리 구조

```
robo-data-analyzer/
├── main.py                         # FastAPI 진입점 (GET / + POST /robo/analyze[,/control])
├── pipeline/analysis_pipelines.py           # Strategy declare — Framework / DBMS 두 파이프라인 한 파일
├── requirements.txt                # 범위(>=) 핀 — langchain 1.x + deepagents
├── .env / .env.example / .env.production
│
├── api/analyze_router.py           # POST /robo/analyze (+ /control)
│
├── cli_scripts/
│   ├── analyze_data_dir.py         # ★ 운영자 표준 진입점
│   ├── baseline_runner.py · check_byte_identical.py · refresh_pricing.py · reset_neo4j_schema.py
│   └── prompt_hashes.json          # prompts/ SHA256 보존
│
├── pipeline/steps/                  # 7 phase(step1~step7) LangGraph 노드
│   ├── step1_parse_ddl/parse_ddl_step.py
│   ├── step2_load_ast/{antlr_json_reader, reference_linker, load_ast_step}.py
│   ├── step3_scan_project/{scan_codebase_step, scan_agent, scan_verify_tool, scanner_input_collector}.py
│   ├── step4_linking/
│   │   ├── link_dbms_tables_step.py                    # DBMS only
│   │   ├── stage1_regex_grep/{link_deterministic_step, regex_table_linker}.py
│   │   ├── stage2_extract_calls/{link_calls_by_name_step, link_calls_by_ast_step, call_linker}.py
│   │   └── stage3_residual_patterns/{link_residual_patterns_step, residual_pattern_loop}.py
│   ├── step5_build_context/
│   │   ├── build_context_step.py · framework_context_builder.py · dbms_context_builder.py
│   │   ├── large_code_one_liner.py · fetch_table_samples_step.py
│   ├── step6_analyze_code/
│   │   ├── analyze_code_step.py                         # 메인 — 모듈 pre + DAG + 3분할/batch
│   │   ├── analysis_order.py · call_cycles.py(Tarjan) · module_pre_analysis.py
│   │   ├── range_body.py · three_split_llm_fanout.py    # Framework 3분할
│   │   └── batch_analyze.py · batch_planner.py          # DBMS batch 3분할
│   └── step7_post_processing/
│       ├── summarize_module_step.py · enrich_table_description_step.py
│       ├── build_components_step.py                     # ★ Leiden 컴포넌트
│       ├── health_metrics_step.py                       # ★ 건강지표
│       ├── enrich_lineage_from_catalog_step.py          # DBMS only
│       └── embed_step.py
│
├── agent_tools/                    # Agent 도구 (scan / residual 공용)
│   ├── code_inspection.py · filesystem.py · path_boundary.py · tool_messages.py
│
├── data_models/
│   ├── codebase.py · analysis_run.py                    # Codebase / Run dataclass
│   ├── graph_nodes.py · graph_edges.py                  # 노드 12 / 엣지 16 dataclass
│   ├── analysis_context.py · agent_output.py · code_analysis_output.py
│
├── shared/
│   ├── config/env_catalog.py       # ★ ROBO_* env 단일 진입점 (ENV 싱글톤)
│   ├── workflow/
│   │   ├── executor.py             # execute_run() — Run 1회 (rmtree latest → workflow → artifact dump)
│   │   ├── graph_builder.py        # build_langgraph_workflow — strategy.steps 로 StateGraph (tuple=병렬)
│   │   ├── state.py · step_utils.py · artifact_writer.py · pause_controller.py · run_registry.py
│   ├── ddl/parser.py                                  # sqlglot 다중 방언 (명시 FK 만)
│   ├── lang/
│   │   ├── strategy_detector.py · call_classifier.py · source_extension.py
│   │   ├── ontology.py + ontology_roles.yaml           # antlr type → 라벨/stereotype
│   │   └── stdlib/{c,java,python}_stdlib.json
│   ├── llm/
│   │   ├── client.py · config_loader.py · fanout.py · structured_agent.py(submit-wrap)
│   │   ├── yaml_llm_runner.py · embedder.py · pricing.py + llm_pricing.yaml
│   │   ├── cost_budget.py · token_usage.py · usage_jsonl_writer.py
│   │   └── middleware/{cache_friendly_order, call_limit, cost_tracking, standard_stack,
│   │                   strip_cache_control, strip_dynamic_system_prompt, todo_tracker, tool_event_emitter}.py
│   ├── neo4j/
│   │   ├── neo4j_client.py         # 비동기 driver + UNWIND 배치 + tenacity 재시도
│   │   ├── schema_constraints.py · rel_types.py · connection_context.py · props.py
│   │   ├── nodes/<12 파일>.py · edges/<15 파일, 관계 16종>.py   # 컴포넌트 합성 노드/엣지 없음 (component_id 속성)
│   ├── log/{logger, ndjson_stream, prompt_logger, processing_progress,
│   │        prompt_mask_patterns, cache_message_filters}.py
│   ├── example_mapping/{builder, graph_writer}.py       # 3분할 결과 → Rule/Example/Question (결정론)
│   ├── external/catalog_client.py                       # catalog HTTP
│   └── source_file_cache.py · content_text.py
│
├── prompts/
│   ├── common/large_code_one_liner.yaml
│   ├── framework/  _common(extends 베이스) + module_pre_analysis + code_summary +
│   │               code_rules_examples + code_table_insights + module_summary +
│   │               table_description + component_naming
│   ├── dbms/       _common(extends 베이스) + code_summary_batch + code_rules_examples_batch +
│   │               code_table_insights_batch + module_summary + table_description + component_naming
│   └── agents/     scan_agent_system.md + residual_pattern_system.md
│
├── skills/                         # Agent 가 read_skill 로 읽는 자유 문서
│   ├── _templates · ejb-cmp · jpa-standard · mybatis-ibatis · ofbiz · proframe-c-pfm-dbio  (각 SKILL.md)
│
├── llm/configs/
│   ├── README.md + qwen36_sglang_local(★현재 활성) / qwen36_openrouter / gpt54_mini_openai / gpt54_nano_openrouter.yaml
│
└── .github/workflows/byte_identical.yml   # prompts/ SHA256 CI 검증
```

---

## 8. 환경변수 — 단일 진입점 `shared/config/env_catalog.py`

모든 ROBO_* env 는 영역별 dataclass 로 묶여 `ENV` 싱글톤으로 노출.
`from shared.config.env_catalog import ENV; ENV.pipeline.max_concurrency`.
명명 규칙 `ROBO_<영역>_<이름>`, 단위 suffix `_SECONDS / _TOKENS / _USD / _RATIO`.

### LLM (`ENV.llm`)
| env | 기본 | 설명 |
|-----|------|------|
| `ROBO_LLM_CONFIG` | `""` | `llm/configs/<name>.yaml` 의 name (필수) |
| `ROBO_LLM_API_KEY` | `""` | 현 config provider 비밀 (필수) |

### Cache (`ENV.cache`)
| `ROBO_CACHE_ENABLED` | `false` | SQLite LLM 응답 캐시 (운영기 OFF 권장) |
|---|---|---|

### Agent (`ENV.agent`)
| env | 기본 | 설명 |
|-----|------|------|
| `ROBO_AGENT_SCAN_MAX_CALLS` | 30 | Scan Agent LLM 호출 한도 |
| `ROBO_AGENT_RESIDUAL_MAX_CALLS` | 15 | 잔여 패턴 루프 한도 |
| `ROBO_AGENT_CONTEXT_EDITING_TRIGGER_TOKENS` | 80000 | context editing 트리거 |
| `ROBO_AGENT_SCAN_REGEX_MIN_OP_RATIO` | 0.5 | op_token 인식률 최소 통과선 (미달 정규식 reject) |

### Budget (`ENV.budget`)
| `ROBO_BUDGET_RUN_USD` | 20.0 | run 당 USD 회로차단기 (초과 raise — $400 사고 재발 방지) |
|---|---|---|

### Neo4j (`ENV.neo4j`)
| env | 기본 |
|-----|------|
| `ROBO_NEO4J_URI` | `bolt://localhost:7687` |
| `ROBO_NEO4J_USER` / `ROBO_NEO4J_PASSWORD` / `ROBO_NEO4J_DATABASE` | `neo4j` / `""` / `neo4j` |
| `ROBO_NEO4J_RETRY_MAX_ATTEMPTS` / `ROBO_NEO4J_RETRY_BACKOFF_SECONDS` | 5 / 2.0 |

### Pipeline (`ENV.pipeline`)
| env | 기본 | 설명 |
|-----|------|------|
| `ROBO_PIPELINE_MAX_CONCURRENCY` | **30** | step6 round 안 + 병렬 후처리 그룹 동시 LLM |
| `ROBO_PIPELINE_BIG_CONTEXT_THRESHOLD_TOKENS` | 1000 | 이상 → large_code_one_liner LLM |
| `ROBO_PIPELINE_RESIDUAL_SAMPLE_SIZE` / `ROBO_PIPELINE_RESIDUAL_MAX_ROUNDS` | 30 / 3 | 잔여 패턴 라운드 |
| `ROBO_PIPELINE_BATCH_MAX_TOKENS` | 1500 | DBMS step6 형제 함수 batch 입력 토큰 한도 |
| `ROBO_PIPELINE_EMBED_ENABLED` | true | embed_step on/off (.env.production 에선 false 권장) |

### Catalog (`ENV.catalog`)
| env | 기본 | 설명 |
|-----|------|------|
| `ROBO_CATALOG_ENABLED` | true | catalog HTTP on/off (off=skip, on+실패=raise. 개별 404 는 정상) |
| `ROBO_CATALOG_BASE_URL` | `""` | catalog endpoint (enabled=true 면 필수) |
| `ROBO_CATALOG_TIMEOUT_SECONDS` | 120.0 | catalog HTTP timeout |
| `ROBO_CATALOG_TEST_DATASOURCE` | `""` | 테스트 전용 샘플 datasource (운영 경로 미사용) |

### Log (`ENV.log`)
| env | 기본 | 설명 |
|-----|------|------|
| `ROBO_LOG_DIR` | `./logs` | 통째 rmtree 후 재생성 |
| `ROBO_LOG_FILE_ENABLED` | true | analyzer.log + error.log 파일 핸들러 (운영기 false 권장) |
| `ROBO_LOG_LEVEL` | INFO | DEBUG/INFO/WARNING/ERROR (error.log 는 내부적으로 WARNING+ 강제) |

### Data (`ENV.data`)
| `ROBO_DATA_DIR` | robo-data-analyzer 형제 `data/` (computed, cwd 무관) |
|---|---|

---

## 9. 디자인 원칙

1. **결정론 우선**: AST·DDL 파싱·R/W grep·Calls 분류·Bottom-up DAG·Example Mapping 빌더·
   Leiden 컴포넌트 군집·건강지표 = 모두 **LLM 0**. LLM 은 의미가 필요한 곳에만.
2. **분기 0 (Strategy 패턴)**: `pipeline/analysis_pipelines.py` 의 `Strategy(steps=...)` declare 만 하면
   `graph_builder` 가 if 분기 없이 그래프 구성. 새 strategy = 한 곳만. (tuple = 병렬 그룹)
3. **단일 진실**: 모든 ROBO_* env = `env_catalog.py` 1곳. 관계명 = `rel_types.py` 1곳. 매직넘버 X.
4. **silent fail 금지**: 모든 step 실패는 `log_error` + `stream.emit(error)` + raise. 조용한 빈
   결과 returning 금지 (잔여 패턴 큐 미해결 비움 사고 방지).
5. **KV 캐시 친화**: 3분할 system 본문 byte-identical 강제 (`prompts/framework/_common.yaml`
   ← `extends`). prompts/ SHA256 은 `prompt_hashes.json` 에 박혀 CI(`byte_identical.yml`)가 변경 감지.
6. **출력 스키마**: `RulesExamplesOutput.examples` 의 `given/when/then` 은 자연어 단일 문자열
   — schema 강제 부작용(반복 폭주·공백 폭주) 차단 (2026-05-30 사고 후 단순화).
7. **submit 도구 패턴**: Agent 출력 강제는 LangChain `response_format` 직접 전달 대신
   `structured_agent.py` 의 submit-도구 wrap — qwen+SGLang xgrammar 동시 마스킹 hang 회피.
8. **합성 노드 회피**: 컴포넌트(부품)는 `:COMPONENT` 합성 노드/엣지로 materialize 하지 않고
   **`FUNCTION.component_id`/`component_name` 속성**으로만 표현 (`build_components_step`) — 그래프에
   합성 노드 0 → 도달성·중심성·임팩트 분석 왜곡 없음. 이름·그룹간 호출은 거기서 파생(온디맨드 GROUP BY).

---

## 10. 자주 부딪치는 사항

- **antlr JSON 디렉토리 없음** — `step2_load_ast` 가 `FileNotFoundError` raise.
  → `d:/work/robo/project/antlr-code-parser` 먼저 실행해 `data/analysis/` 채우기.
- **`logs/` 내용이 다음 run 에 사라짐** — 정상. `_reset_logs_root` 가 매 run 통째 rmtree.
- **Neo4j 그래프가 매 run 비워짐** — 정상. `wipe_graph()` 의 `DETACH DELETE` (헌법: 매 run = full rebuild).
  **같은 DB 를 쓰는 catalog·architect 데이터도 함께 지워진다** — 분석 후 재등록 필요.
- **Scan Agent 가 도구 호출 안 함** — `submit_scan` 누락 시 raise. `scan_agent_system.md` 확인.
- **`leidenalg`/`igraph` import 오류** — `build_components_step` 가 명시 raise.
  → `pip install -r requirements.txt` 로 설치.
- **catalog 404** — 정상(개별 리소스 미존재). 단 `ROBO_CATALOG_ENABLED=true` 인데 연결 자체 실패면 raise.
- **`run_yaml_rule` 의 `{...}` 치환 오류** — `_safe_format` 이 알려진 키만 치환(yaml 자유 텍스트 영향 0).
- **timeout 5분 초과** — `qwen36_sglang_local.yaml` 의 `timeout_seconds: 600` (큰 출력 5~10분 실측).

---

## 11. 관련 문서

- [`llm/configs/README.md`](llm/configs/README.md) — LLM yaml 스키마
- [`skills/_templates/SKILL.md`](skills/_templates/SKILL.md) — 새 skill 작성 템플릿
- [`skills/proframe-c-pfm-dbio/SKILL.md`](skills/proframe-c-pfm-dbio/SKILL.md) — 테스트 데이터 대응 skill
- `.github/workflows/byte_identical.yml` — prompts/ SHA256 CI 검증
</content>
</invoke>
