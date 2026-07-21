# ROBO Data Analyzer

> **레거시 코드베이스를 "읽지 않고" 이해한다.**
> 오래된 소스 코드(자바·C·파이썬 등) 또는 Oracle PL/SQL 패키지를 정적 분석해
> **Neo4j 지식 그래프**로 바꾸는 파이프라인. 코드 구조 + 호출 관계 + 코드↔테이블
> 읽기/쓰기 + 함수·모듈 요약 + **비즈니스 규칙** + **부품(컴포넌트) 군집** + **건강지표**
> 까지 한 번에 뽑아낸다.

---

## ⚠️ 최근 변경 (2026-07-18) — 읽고 시작하세요

전수 DRY·정합 조사와 세 서비스 구조 리팩터에서 확인된 위반을
[045](specs/045-graph-integrity-restore/spec.md),
[046](specs/046-dry-single-truth/spec.md),
[059](specs/059-shared-graph-ownership/spec.md),
[062](specs/062-analyzer-wide-naming-audit/spec.md)로 시정했다.

| 무엇이 바뀌었나 | 왜 | 스펙 |
|---|---|---|
| **그래프 reset은 Analyzer 소유 노드만 지운다** — `graph_owner='analyzer'` 범위에서 `DETACH DELETE`. Data Fabric의 `DataSource`와 다른 서비스 데이터는 보존한다 | 프로필·서비스 소유권 격리. Analyzer 자체는 매 run full rebuild를 유지한다 | [059](specs/059-shared-graph-ownership/spec.md) |
| **원문(`code_text`) 은 step2 가 모든 노드에 부여** (코드노드·파일·변수·상수·데이터타입) | 옛 규칙(step6 에서만 코드노드에)은 선언 노드의 노드 패널을 비게 했다. **spec 016 FR-004 supersede** | [045](specs/045-graph-integrity-restore/spec.md) |
| **소스 read 실패가 로그로 표면화** + `code_text_missing` 카운터 | 종전엔 로그 0건으로 삼켜져 "성공"으로 끝났다 (헌법 IV 위반) | [045](specs/045-graph-integrity-restore/spec.md) |
| **`analysis.json` 에 원문이 안 담긴다** — 원문의 집은 `ast.json` 하나 | 같은 원문 842 KB 가 두 파일에 중복이었다 | [045](specs/045-graph-integrity-restore/spec.md) |
| **전략 분기 0** — `analyze_code_step` 이 `Strategy.code_analyzer` 를 **주입**받는다 | 헌법 II 복원 (`strategy.name == "dbms"` 하드분기 제거) | [046](specs/046-dry-single-truth/spec.md) |
| **지원 확장자·루틴 라벨·MERGE 쿼리·소스 캐시 단일 진실화** | 헌법 III 복원. 확장자는 **실제로 갈라져 있었다**(4종 vs 14종) → `.kt` 프로젝트가 **0노드 "성공"** 으로 끝났다. 이제 **명시적 실패** | [046](specs/046-dry-single-truth/spec.md) |
| **죽은 코드 삭제** — 모듈 1 · 함수 4 · 환경변수 키 6 (특히 `ROBO_LOG_MASK_SECRETS` 는 **효과 없는 거짓 설정**이었다) | | [046](specs/046-dry-single-truth/spec.md) |

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
| **출력** | Neo4j 그래프(UPSERT) + `output/analysis/latest/artifacts/*.json` 5종 + `usage_summary.json` + `health_report.json` + NDJSON 스트림 |
| **엔진** | FastAPI + LangGraph StateGraph + LangChain/deepagents Agent |
| **2개 Strategy** | `framework`(응용코드) / `dbms`(PL/SQL) — 확장자로 자동 감지, `--strategy`로 override |
| **LLM 정책** | 모델+배포환경 1셋 = `llm/configs/<name>.yaml` 1개. `.env`의 `ROBO_LLM_CONFIG` 1줄로 전환 |
| **결정론 우선** | DDL·AST 파싱 / 링킹 grep / 호출 분류 / Bottom-up DAG / 컴포넌트 군집 / 건강지표 = **LLM 0회**. LLM은 "의미"가 필요한 곳에만 |
| **실행 단위** | `/robo/analyze` 1회 = LangGraph workflow 1회 = `Run` 1개. 매 Run 시작 시 Analyzer 소유 그래프 reset + `output/` rmtree |
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
analyzer 는 매 Run 시작에 `graph_owner='analyzer'`인 노드만 `DETACH DELETE`하고 `ensure_schema()`로
제약/인덱스를 재확인한다(idempotent). 같은 DB를 공유하는 Data Fabric의 `DataSource`와 다른 서비스
소유 노드는 보존된다. Analyzer 소유 그래프 안에서는 full-rebuild 정책을 유지한다(spec 059).
HTTP 헤더 `X-Neo4j-*` 로 요청별 override 도 가능
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

### 2.2 Strategy 자동 감지 — `pipeline/languages.py`

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
python scripts/analyze_data_dir.py

# 1 프로젝트만 (source/ 하위 폴더 이름)
python scripts/analyze_data_dir.py <project_name>

# strategy 강제 override
python scripts/analyze_data_dir.py <project_name> --strategy dbms
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
  "project_root": "d:/work/robo/project/data/source/<name>",  # (역호환) 받되 무시(inert) — 소스는 항상 SETTINGS.analysis_data.dir/source
  "strategy": "framework",            # "framework" | "dbms"
  "ddl_dir": "d:/work/robo/project/data/ddl",                 # 선택
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

### 3.3 보조 CLI (`scripts/`)

| 스크립트 | 용도 |
|---------|------|
| `analyze_data_dir.py` | ★ 운영자 표준 진입점 (위 3.1) |
| `check_byte_identical.py` | `llm/prompts/**` SHA256 ↔ `prompt_hashes.json` 검증 (CI 보호장치) |
| `refresh_pricing.py` | `llm/usage/llm_pricing.yaml` 갱신 보조 (현재 yaml + 공식 URL 안내) |
| `reset_neo4j_schema.py` | Neo4j 제약/인덱스 1회 리셋 (오염 정리 — 평상시 불필요) |

---

## 4. 파이프라인 — Strategy 별 step 흐름

`pipeline/strategies.py` 의 `Strategy` 데이터클래스가 step 함수 tuple 을 **declare 만**
하면, `pipeline/execution/langgraph_builder.py:build_langgraph_workflow()` 가 **if 분기 0개** 로
LangGraph StateGraph 노드를 순서대로 추가한다. step1~step8 **폴더(8 phase)** 구조가 흐름
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
> 새 strategy 추가 = `pipeline/strategies.py` 의 `Strategy(...)` declare 만. 코드 분기 0.

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
`NEXT`(DBMS 구문 실행 흐름) · `REFERENCES` · `INIT_BY` + state `call_sites`(raw — step5 분류) · `import_refs`.
**실패 정책**: AST 디렉토리 없으면 명시 `FileNotFoundError` (silent skip 금지).

### Step 3 — External file loading

프로젝트 inventory에서 SQL/XML·설정·artifact·문서를 안전하게 분류하고, `PROJECT_FILE`과
bounded chunk/document 구조로 정규화한다. source/DDL/AST의 기존 책임은 침범하지 않으며
확정할 수 없는 파일과 키는 `UNRESOLVED_REF`로 보존한다. 두 strategy가 공유하는 결정론 단계다.

### Step 4 — `scan_codebase_step` (Framework only, LLM Agent 1 회)

**입력**: `project_root` + state 의 `functions / tables` + `scanner_input_collector` 단서
(manifest / 확장자 카운트 / 카테고리).
**에이전트**: 단일 Scan Agent invoke (`scan_agent.py`). 도구: `ls / glob_search / grep /
read_file / read_file_range`(project_root 경계 강제) · `list_functions` · `list_skills` /
`read_skill`(`agents/skills/framework/<name>/SKILL.md`) · `verify_table_regex`(정규식→실제 소스 매치율 검증)
· (`write_todos` 는 deepagents 자동).
**시스템 프롬프트**: `agents/prompts/scan.md` (byte-identical 보존 — CI 검증).
**출력 강제**: `submit_scan` 도구 1회 → `ScanResult` (submit-도구 wrap 으로 qwen+SGLang grammar
mask hang 회피 — `structured_agent.py`).
**산출 `ScanResult`**: `table_access_patterns`(검증 통과분 — `<op_token>`/`<entity>` named group +
op→SELECT/INSERT/… 매핑) · `framework_functions`(호출 그래프 드롭 대상) · `call_wrappers`(인자가
실제 호출대상인 래퍼) · `codebase_notes`(step7 LLM prefix로 항상 주입 — KV 캐시 친화) · `ddl_transform`.
**한도**: `ROBO_AGENT_SCAN_MAX_CALLS`(기본 30) 초과 시 raise.

### Step 5 — Linking

#### 5-A `link_deterministic_step` (Framework, stage1, LLM 0)
`ScanResult.table_access_patterns` 의 검증 정규식으로 `RegexTableLinker` 가 project root
전체를 grep → `READS` / `WRITES` 엣지. 매칭 실패는 `unresolved_cases` 큐(stage3 가 소비).

#### 5-B `link_calls_by_name_step` (Framework, LLM 0)
step2 의 `call_sites` 를 코드 목록 대조로 분류: 프로젝트 함수 → `internal` `CALLS`,
`framework_functions` 런타임/매크로 → 드롭, 그 외 → `external` `CALLS`. `call_wrappers` 처리.

#### 5-B' `link_calls_by_ast_step` (DBMS, LLM 0)
antlr `CALL` 노드를 분류 → `CALLS`. 빌트인(`DBMS_`/`UTL_`/…) 드롭, 콜리 단순명이 정의 노드와
일치하면 internal, 그 외 external(`:FUNCTION:EXTERNAL` 스텁 생성), 자기루프 드롭. AST 라 문자열·주석 호출 오인식 0.

#### 5-B'' `link_dbms_tables_step` (DBMS, LLM 0)
**모든 노드의 자기 줄**(자기 범위 − 자식 줄범위) 에서 **DDL 테이블명 단어경계 검색**. 부모
(`MERGE INTO…USING(SELECT…)`)도 자기 줄 스캔 → 누락·중복 0. 첫 DML 키워드(SELECT/WITH/INSERT/
UPDATE/DELETE/MERGE)가 READS/WRITES 결정. 주석(`--`,`/* */`) 제거 후 매칭(문자열은 보존 — EXECUTE IMMEDIATE 리터럴 SQL 포착).

#### 5-C `link_residual_patterns_step` (Framework only, LLM Agent 라운드 루프)
stage1 의 `unresolved_cases` → **잔여 패턴 루프**(케이스 추적이 아니라 샘플에서 공통 패턴을
LLM 이 찾게 해 코드가 일괄 적용). 시스템 프롬프트 `agents/prompts/residual.md`,
라운드당 샘플 `ROBO_PIPELINE_RESIDUAL_SAMPLE_SIZE`(30) · 최대 라운드 `_RESIDUAL_MAX_ROUNDS`(3)
· 호출 한도 `ROBO_AGENT_RESIDUAL_MAX_CALLS`(15). `submit_residual` → `verify_table_regex` 통과분만 적용.
**state 정책**: `unresolved_cases` 는 reducer 없는 평키 — stage1 raw 를 stage3 최종 잔여로 덮어쓴다.

### Step 6 — Context

#### 6-A `build_context_step` (큰 함수만 LLM 1 회)
모든 `Function` 에 `AnalysisContext` 생성: 식별/위치/`reads·writes·callers·callees`(정렬 tuple)
/ `referenced_symbols`(REFERENCES+INIT_BY 결정론) / `one_liner`(본문 토큰 >
`ROBO_PIPELINE_BIG_CONTEXT_THRESHOLD_TOKENS`(1000) 면 `large_code_one_liner.yaml` 압축 LLM
1회(작은 모델) → callee 본문 통째 노출 차단). `context_builder.augment()`:
Framework와 DBMS의 기본 builder는 항등이며, DBMS 유효 트리는 step7에서 계산한다.

#### 6-B `fetch_table_samples_step` (옵션·전략독립, LLM 0)
catalog `sample-context` 엔드포인트로 테이블별 **실제 샘플 행**(최대 5행) 수집 → state
`table_sample_data`. 100개씩 청크 병렬 HTTP. `ROBO_CATALOG_ENABLED=false` / `datasource` 미지정 /
테이블 0개 면 graceful skip. 청크 실패 시 그 테이블만 샘플 없이 진행(분석 계속).

### Step 7 — `analyze_code_step` (모듈 사전분석 + Bottom-up DAG + fan-out)

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
     (`llm/prompts/framework/_common.yaml` ← `extends`).
   - **DBMS — batch 3분할 분석** (`batch_analyze.py` + `batch_planner.py`): 같은 유효 부모의 형제
     함수를 입력 토큰 `ROBO_PIPELINE_BATCH_MAX_TOKENS`(1500) 한도로 **배치(그룹)** 로 묶고,
     framework 처럼 관심사별 3콜(`code_summary_batch` / `code_rules_examples_batch` /
     `code_table_insights_batch`)로 분석 — 콜당 "한 관심사 × 노드 리스트" 평탄 출력(출력 폭주 방지),
     `node_key`(줄범위)로 fan-in.
4. **즉시 적재**: 함수 1개 완료 즉시 `update_summaries()` + `node_event`/`relationship_event`
   NDJSON emit. 배치 누적 없음.
5. **Example Mapping 빌더** (`pipeline/steps/step7_analyze_code/example_mapping_builder.py`, 결정론): 결과 → `RULE`(공유,
   `rule_hash`) / `EXAMPLE` / `QUESTION` 노드 + `HAS_RULE` / `HAS_EXAMPLE` / `HAS_QUESTION` /
   `AFFECTS_TABLE` 엣지. 구문 실행 순서 `NEXT`는 Step 2 AST 적재 결과이며 Example Mapping 관계가 아니다.

### Step 8 — Post Processing

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
  `output/analysis/latest/health_report.json`(risk 분포 + top 20 위험 함수).
  임계: LOC≥300 & fan_out≥15 → god_flag; in_cycle 또는 god_flag → high.
- **`enrich_lineage_from_catalog_step`** (DBMS only) — catalog `/lineage` → `READS`/`WRITES`
  엣지 추가(매칭 실패 시 `__catalog__:<source>` 가상 노드).
- **`embed_step`** — `paraphrase-multilingual-MiniLM-L12-v2` 로 Table/Column.description +
  Function.summary 임베딩 → Neo4j `embedding` property. `ROBO_PIPELINE_EMBED_ENABLED=true` 일 때만.

---

## 6. 출력 / 산출물

### 6.1 디렉토리 구조 — `ROBO_RUN_OUTPUT_DIR/` (기본 `./output`)

매 run 시작 시 **`output/` 통째 rmtree** 후 재생성
(`pipeline/execution/run_executor.py:_reset_run_output_root`).

```
output/
├── analysis/latest/
│   ├── analyzer.log               # INFO+ (ROBO_RUNTIME_EVENT_FILE_ENABLED=true 일 때)
│   ├── error.log                  # WARNING+ — 사고 추적 전용
│   ├── usage.jsonl                # 모든 LLM 호출 토큰/비용 NDJSON (agent + yaml_rule)
│   ├── usage_summary.json         # usage.jsonl 집계 (total + per_agent_model)
│   ├── health_report.json         # ★ 함수 건강지표 (risk 분포 + top 위험 함수)
│   └── artifacts/
│       ├── ast.json               # step1+2 (schema/table/column + function/module/… + 구조 엣지)
│       ├── linking.json           # step5 (reads_writes + calls + unresolved + scan_result)
│       ├── analysis_contexts.json # step6 (code_id → AnalysisContext)
│       ├── analysis.json          # step7+8 (summary + rules/examples/questions; 컴포넌트는 FUNCTION.component_id 속성)
│       │                          #   ★ 원문(code_text)은 안 담는다 — 원문의 집은 ast.json 하나 (spec 045)
│       └── graph_extras.json      # 매핑 안 된 state 키 auto-dump (신규 키 누락 방지 catch-all)
└── llm-prompts/latest/
    └── ...                         # 룰별 LLM 입력 미리보기 (tracing)
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

### 6.3 MCP 레거시 참조 도구

`/robo/mcp`는 compact JSON 텍스트를 반환하는 두 도구를 제공한다. 먼저
`cluster_retrieve(query, database?)`로 ID·요약·관계 단서·RULE/EXAMPLE 목록을 받고,
검토할 ID만 `node_detail(node_id, database?)`로 원문 코드·파일·시작/끝 줄·실제 관계·
TABLE 컬럼까지 조회한다. 목록 응답에는 원문 코드와 전체 컬럼이 포함되지 않는다.

### 6.4 Neo4j 그래프 스키마 — 노드 12종 / 엣지 16종

**노드 12종 (`graph/nodes/` 12파일, 라벨 UPPER_SNAKE)** — 모든 노드 식별 키 = **`id` 하나
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

**엣지 15종 (`graph/schema/rel_types.py` 단일 정의처)**:

| 구분 | 엣지 |
|------|------|
| 코드 구조 | `CALLS`(scope) · `PARENT_OF` · `HAS_MEMBER`(그릇→루틴/데이터) · `BELONGS_TO`(그릇→폴더) · `NEXT`(DBMS 구문 흐름) |
| 코드 참조 | `REFERENCES` · `INIT_BY` |
| 코드↔DB | `READS`(dml_type) · `WRITES`(dml_type) |
| DB 구조 | `HAS_COLUMN` · `BELONGS_TO` · `FK` |
| Example Mapping | `HAS_RULE` · `HAS_EXAMPLE` · `HAS_QUESTION` · `AFFECTS_TABLE` |

> 새 노드/엣지 = `graph/schema/graph_nodes.py`(또는 `graph_edges.py`) + `graph/nodes/<new>.py`
> (또는 `graph/edges/<new>.py`) + `graph/schema/rel_types.py` 상수. 관계명은 이 파일에서만 정의한다.

---

## 7. 디렉토리 구조

```
robo-data-analyzer/
├── main.py · api/                  # FastAPI, REST, MCP, NDJSON 공개 진입점
├── shared/
│   ├── config/settings.py          # ROBO_* 환경 설정 단일 진실
│   ├── events/                     # 런타임 로그·처리 이벤트·NDJSON 스트림
│   └── files/cache.py              # run-scoped 소스 파일 캐시
├── pipeline/
│   ├── strategies.py · languages.py · ontology_roles.yaml
│   ├── models/               # 분석 입력·상태·LLM 출력 계약
│   ├── execution/                 # run 수명주기·상태·제어·artifact·delta stream
│   └── steps/step1_parse_ddl … step8_post_processing
├── graph/
│   ├── schema/                    # 노드·관계 dataclass, ID, 관계명, 제약
│   ├── nodes/ · edges/            # Neo4j 영속 어댑터
│   └── neo4j_client.py · node_upsert.py · connection_context.py
├── agents/                         # 자율 Agent 워크플로와 전용 자산
│   ├── prompts/ · skills/ · tools/ · middleware/
│   └── scan.py · external.py · residual.py · runtime.py · contracts.py
├── integrations/                   # Catalog 등 외부 서비스 연동
├── llm/                            # 모델 호출 인프라
│   ├── configs/ · prompts/ · runners/ · usage/
│   └── client.py · fanout.py · messages.py · filters.py · tracing.py
├── search/
│   ├── corpus/                    # 코퍼스·임베딩 재료·Neo4j reader
│   ├── query/                     # 용어집·재작성·심판·검색 LLM
│   └── ranking/                   # recall·fusion·rerank·retriever
├── scripts/{analysis,graph_maintenance,llm_maintenance}/
├── tests/{agents,api,eval,graph,pipeline,search,shared,structure}/
└── specs/                         # 완료 기능의 변경 당시 계약 이력
```

---

## 8. 환경변수 — 단일 진입점 `shared/config/settings.py`

모든 ROBO_* 환경변수는 영역별 dataclass로 묶여 `SETTINGS` 싱글톤으로 노출됩니다.
`from shared.config.settings import SETTINGS; SETTINGS.analysis_pipeline.max_concurrency`.
명명 규칙 `ROBO_<영역>_<이름>`, 단위 suffix `_SECONDS / _TOKENS / _USD / _RATIO`.

### LLM (`SETTINGS.analysis_llm`)
| 환경변수 | 기본 | 설명 |
|-----|------|------|
| `ROBO_LLM_CONFIG` | `""` | `llm/configs/<name>.yaml` 의 name (필수) |
| `ROBO_LLM_API_KEY` | `""` | 현 config provider 비밀 (필수) |

### Cache (`SETTINGS.llm_response_cache`)
| `ROBO_CACHE_ENABLED` | `false` | SQLite LLM 응답 캐시 (운영기 OFF 권장) |
|---|---|---|

### Agent (`SETTINGS.analysis_agents`)
| 환경변수 | 기본 | 설명 |
|-----|------|------|
| `ROBO_AGENT_SCAN_MAX_CALLS` | 30 | Scan Agent LLM 호출 한도 |
| `ROBO_AGENT_RESIDUAL_MAX_CALLS` | 15 | 잔여 패턴 루프 한도 |
| `ROBO_AGENT_CONTEXT_EDITING_TRIGGER_TOKENS` | 80000 | context editing 트리거 |
| `ROBO_AGENT_SCAN_REGEX_MIN_OP_RATIO` | 0.5 | op_token 인식률 최소 통과선 (미달 정규식 reject) |

### Budget (`SETTINGS.run_budget`)
| `ROBO_BUDGET_RUN_USD` | 20.0 | run 당 USD 회로차단기 (초과 raise — $400 사고 재발 방지) |
|---|---|---|

### Neo4j (`SETTINGS.graph_database`)
| 환경변수 | 기본 |
|-----|------|
| `ROBO_NEO4J_URI` | `bolt://localhost:7687` |
| `ROBO_NEO4J_USER` / `ROBO_NEO4J_PASSWORD` / `ROBO_NEO4J_DATABASE` | `neo4j` / `""` / `neo4j` |
| `ROBO_NEO4J_RETRY_MAX_ATTEMPTS` / `ROBO_NEO4J_RETRY_BACKOFF_SECONDS` | 5 / 2.0 |

### Pipeline (`SETTINGS.analysis_pipeline`)
| 환경변수 | 기본 | 설명 |
|-----|------|------|
| `ROBO_PIPELINE_MAX_CONCURRENCY` | **30** | step7 round 안 + 병렬 후처리 그룹 동시 LLM |
| `ROBO_PIPELINE_BIG_CONTEXT_THRESHOLD_TOKENS` | 1000 | 이상 → large_code_one_liner LLM |
| `ROBO_PIPELINE_RESIDUAL_SAMPLE_SIZE` / `ROBO_PIPELINE_RESIDUAL_MAX_ROUNDS` | 30 / 3 | 잔여 패턴 라운드 |
| `ROBO_PIPELINE_BATCH_MAX_TOKENS` | 1500 | DBMS step7 형제 함수 batch 입력 토큰 한도 |
| `ROBO_PIPELINE_EMBED_ENABLED` | true | embed_step on/off (.env.production 에선 false 권장) |

### Catalog (`SETTINGS.catalog_api`)
| 환경변수 | 기본 | 설명 |
|-----|------|------|
| `ROBO_CATALOG_ENABLED` | true | catalog HTTP on/off (off=skip, on+실패=raise. 개별 404 는 정상) |
| `ROBO_CATALOG_BASE_URL` | `""` | catalog endpoint (enabled=true 면 필수) |
| `ROBO_CATALOG_TIMEOUT_SECONDS` | 120.0 | catalog HTTP timeout |
| `ROBO_CATALOG_TEST_DATASOURCE` | `""` | 테스트 전용 샘플 datasource (운영 경로 미사용) |

### Runtime events / run output (`SETTINGS.observability`, `SETTINGS.run_output`)
| 환경변수 | 기본 | 설명 |
|-----|------|------|
| `ROBO_RUN_OUTPUT_DIR` | `./output` | 통째 rmtree 후 재생성 |
| `ROBO_RUNTIME_EVENT_FILE_ENABLED` | true | analyzer.log + error.log 파일 핸들러 (운영기 false 권장) |
| `ROBO_RUNTIME_EVENT_LEVEL` | INFO | DEBUG/INFO/WARNING/ERROR (error.log 는 내부적으로 WARNING+ 강제) |

### Data (`SETTINGS.analysis_data`)
| `ROBO_DATA_DIR` | robo-data-analyzer 형제 `data/` (computed, cwd 무관) |
|---|---|

---

## 9. 디자인 원칙

1. **결정론 우선**: AST·DDL 파싱·R/W grep·Calls 분류·Bottom-up DAG·Example Mapping 빌더·
   Leiden 컴포넌트 군집·건강지표 = 모두 **LLM 0**. LLM 은 의미가 필요한 곳에만.
2. **분기 0 (Strategy 패턴)**: `pipeline/strategies.py` 의 `Strategy(steps=...)` declare 만 하면
   `langgraph_builder` 가 if 분기 없이 그래프 구성. 새 strategy = 한 곳만. (tuple = 병렬 그룹)
3. **단일 진실**: 모든 ROBO_* 환경변수 = `shared/config/settings.py` 1곳. 관계명 = `graph/schema/rel_types.py` 1곳. 매직넘버 X.
4. **silent fail 금지**: 모든 step 실패는 `log_error` + `stream.emit(error)` + raise. 조용한 빈
   결과 returning 금지 (잔여 패턴 큐 미해결 비움 사고 방지).
5. **KV 캐시 친화**: 3분할 system 본문 byte-identical 강제 (`llm/prompts/framework/_common.yaml`
   ← `extends`). 프롬프트 SHA256은 `llm/prompts/prompt_hashes.json`에 고정하고 전용 검사로 변경을 감지한다.
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
- **`output/` 내용이 다음 run 에 사라짐** — 정상. `_reset_run_output_root` 가 매 run 통째 rmtree.
- **Analyzer 그래프가 매 run 비워짐** — 정상. `wipe_graph()`가 `graph_owner='analyzer'` 범위만
  `DETACH DELETE`한다. Data Fabric의 DataSource와 다른 서비스 소유 데이터는 보존된다.
- **Scan Agent 가 도구 호출 안 함** — `submit_scan` 누락 시 raise. `agents/prompts/scan.md` 확인.
- **`leidenalg`/`igraph` import 오류** — `build_components_step` 가 명시 raise.
  → `pip install -r requirements.txt` 로 설치.
- **catalog 404** — 정상(개별 리소스 미존재). 단 `ROBO_CATALOG_ENABLED=true` 인데 연결 자체 실패면 raise.
- **`run_yaml_rule` 의 `{...}` 치환 오류** — `_safe_format` 이 알려진 키만 치환(yaml 자유 텍스트 영향 0).
- **timeout 5분 초과** — `qwen36_sglang_local.yaml` 의 `timeout_seconds: 600` (큰 출력 5~10분 실측).

---

## 11. 관련 문서

- [`llm/configs/README.md`](llm/configs/README.md) — LLM yaml 스키마
- [`agents/skills/templates/SKILL.md`](agents/skills/templates/SKILL.md) — 새 skill 작성 템플릿
- [`agents/skills/framework/proframe-c-pfm-dbio/SKILL.md`](agents/skills/framework/proframe-c-pfm-dbio/SKILL.md) — 테스트 데이터 대응 skill
- `python scripts/check_byte_identical.py` — 프롬프트 SHA256 검증
