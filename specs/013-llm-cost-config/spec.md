# Feature Specification: LLM Configuration, Cost Governance & Structured Agent Output

**Feature Branch**: `013-llm-cost-config`

**Created**: 2026-06-15

**Status**: Backfilled (reverse-engineered)

**Input**: User description: "단일 파일 LLM config (`llm_configs/<name>.yaml`, `.env` 한 줄 `ROBO_LLM_CONFIG` 로 전환·코드 무변경), provider 비종속 클라이언트, run 당 USD 회로 차단기, 토큰/비용 회계(usage.jsonl + usage_summary.json), submit-tool wrap 패턴으로 구조화 출력 강제(qwen+SGLang grammar-mask hang 회피), yaml 룰 러너, 모든 ROBO_* env 단일 진입점(env_catalog ENV)."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 한 줄로 LLM provider / 배포 환경 전환 (Priority: P1)

운영자가 사내 SGLang 으로 돌리던 분석을 OpenRouter(또는 OpenAI 직접/Anthropic 직접)로 바꾸고 싶다. `.env` 의 `ROBO_LLM_CONFIG` 한 줄만 다른 yaml 이름으로 바꾸면 끝 — 파이썬 코드는 한 줄도 안 고친다. provider 별로 다른 api_base / 모델명 / reasoning 끄는 법 / 비표준 sampling 인자(top_k·min_p·enable_thinking 등)는 전부 그 yaml 1개 안에 박혀 있다.

**Why this priority**: 모델·배포 환경 교체는 가장 잦은 운영 변경이고, 코드 수정 없이 처리돼야 회귀 위험이 0 이다. 나머지 기능(비용·구조화 출력)이 모두 이 클라이언트 위에서 동작한다.

**Independent Test**: 두 개의 yaml(`qwen36_sglang_local`, `qwen36_openrouter`)을 두고 `ROBO_LLM_CONFIG` 만 바꿔가며 `build_llm_client()` 가 서로 다른 endpoint/모델로 정상 클라이언트를 만드는지 확인. 코드 diff 0.

**Acceptance Scenarios**:

1. **Given** `ROBO_LLM_CONFIG=qwen36_sglang_local`, **When** 클라이언트를 빌드, **Then** yaml 의 api_base/model/extra_body 가 그대로 적용된 OpenAI 호환 클라이언트가 생성된다.
2. **Given** `provider: anthropic` yaml, **When** 빌드, **Then** `reasoning_effort` 가 Anthropic `thinking` 인자로 자동 변환된다 (코드 분기 없음 — 표준 키만).
3. **Given** `ROBO_LLM_CONFIG` 미설정 또는 없는 이름, **When** 빌드, **Then** 사용 가능한 config 목록을 담은 명시적 `RuntimeError` 가 난다 (silent 처리 X).

---

### User Story 2 - 폭주하는 비용을 자동으로 멈춘다 (Priority: P1)

과거 한 번의 잘못된 run 이 약 $400 을 태운 사고가 있었다. 이제 모든 LLM 호출(Agent 내부 포함 + Agent 외부 단순 호출)이 비용을 누적하고, run 당 한도(`ROBO_BUDGET_RUN_USD`, 기본 $20)를 넘는 순간 예외를 던져 파이프라인을 자동 중단한다. 동시에 호출 1건마다 `usage.jsonl` 에 한 줄씩 적재되고 run 끝에 `usage_summary.json` 으로 집계된다.

**Why this priority**: 무한 루프/출력 폭주가 청구서로 직결되는 LLM 파이프라인에서 비용 회로 차단기는 안전장치다. 측정 사각지대(집계 누락)가 생기면 차단기 자체가 무력화되므로 모든 진입점이 같은 회계를 거쳐야 한다.

**Independent Test**: `ROBO_BUDGET_RUN_USD` 를 매우 작게 설정하고 run 을 돌려 한도 초과 시 `BudgetExceededError` 로 중단되는지, `usage.jsonl` 줄 수가 LLM 호출 수와 일치하는지, `usage_summary.json` 의 total 이 줄 합과 맞는지 확인.

**Acceptance Scenarios**:

1. **Given** 누적 비용이 한도 미만, **When** LLM 호출 후 `add_sync(cost)`, **Then** 누적만 되고 계속 진행한다.
2. **Given** 누적 비용이 한도 초과, **When** 다음 호출 비용 가산, **Then** `spent > limit` 메시지와 함께 `BudgetExceededError` 가 raise 되어 run 이 멈춘다.
3. **Given** PRICING 에 없는 모델, **When** 비용 계산, **Then** 비용 0 + 모델당 1회 경고(측정 사각지대 위험 명시) — 조용히 넘어가지 않는다.

---

### User Story 3 - 탐색 에이전트의 구조화 출력을 멈춤 없이 강제 (Priority: P2)

탐색 도구를 든 에이전트가 끝에 반드시 정해진 Pydantic 스키마로 결과를 내야 한다. 단, qwen+SGLang(xgrammar) 환경에서 LangChain 의 기본 `response_format`(ToolStrategy + `tool_choice="any"`)을 쓰면 [도구 N + 응답 스키마] 동시 grammar mask 로 디코더가 hang 한다. 이를 피하려고 응답 Pydantic 모델을 "탐색 끝에 정확히 한 번 호출하는 submit 도구"로 wrap 해서 도구 목록 마지막에 붙인다. 에이전트가 그 도구를 호출하면 결과가 state 에 적재되고 루프가 명시적으로 종료된다.

**Why this priority**: 핵심 분석 산출물의 형식 신뢰성을 보장하면서 특정 로컬 모델 스택의 hang 을 회피한다. P1 두 기능이 동작해야 의미가 있으므로 P2.

**Independent Test**: 더미 도구 + 응답 모델로 에이전트를 1회 invoke 해서 submit 도구 경유로 모델 인스턴스가 반환되는지, 그리고 submit 미호출로 끝나면 명시적 `ValueError`(silent 실패 금지)가 나는지 확인.

**Acceptance Scenarios**:

1. **Given** 응답 Pydantic 모델, **When** `run_structured_agent` 실행, **Then** 모델 필드들이 submit 도구의 keyword 인자로 노출되고 호출 결과가 `response_format` 인스턴스로 반환된다.
2. **Given** 에이전트가 submit 도구를 한 번도 호출하지 않고 종료, **When** 결과 추출, **Then** `ValueError` 로 표면화된다 (system prompt 안내 누락 진단 메시지 포함).

---

### Edge Cases

- `ROBO_LLM_CONFIG` 빈 값 / 파일 없음 / yaml 최상위가 dict 아님 / `provider`·`model` 누락 → 각각 명시적 `RuntimeError`.
- provider≠anthropic 인데 `api_base` 비어 있음 → endpoint 예시를 담은 `RuntimeError`.
- hosted API(OpenAI/Anthropic)에 로컬 전용 `extra_body` 키가 들어가면 400 BadRequest — 코드가 자동 주입하지 않고(분기 사고 차단) yaml 운영자 책임, 실패는 명시 로그.
- 응답 metadata 에서 모델명 추출 실패 → `"unknown"` 으로 적재 + 경고 (집계는 계속).
- `cached_tokens` 미노출 환경(SGLang `--enable-cache-report` OFF) → latency 기반 추정기로 fallback (정확도 낮음 명시).
- OpenRouter/Anthropic 이 모델 ID 에 붙이는 날짜 suffix(`-20260415` 등) → 가격 매칭 시 자동 strip 후 base 이름 매칭.
- `usage.jsonl` 적재 실패(OSError) → 호출 성공으로 승격하지 않되 silent skip 금지(log_warning).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: 시스템은 `llm_configs/<name>.yaml` 1개 = 모델+배포환경 1묶음 으로 LLM 설정을 외부화해야 하며, `.env` 의 `ROBO_LLM_CONFIG=<name>` 한 줄 변경만으로 코드 수정 없이 전환 가능해야 한다.
- **FR-002**: 시스템은 provider 비종속이어야 한다 — `anthropic` 은 직접 SDK, 그 외(`openai`/`openrouter`/`sglang`/`vllm`/자유 명명)는 모두 OpenAI 호환 경로로 처리.
- **FR-003**: 시스템은 `reasoning_effort`(none/low/medium/high) 표준 키만 provider 별로 자동 변환해야 한다 (OpenAI o-series 인자 / Anthropic thinking budget). 로컬 모델 비표준 키는 yaml `extra_body` 운영자 명시이며 코드가 자동 주입하지 않는다.
- **FR-004**: API key 는 yaml 에 절대 박지 않고 `.env` 의 `ROBO_LLM_API_KEY` 로만 받아야 한다 (secret manager 친화, yaml 은 commit 대상).
- **FR-005**: config 로딩 시 누락 필드·잘못된 형식·없는 파일은 사용 가능한 config 목록 등을 담은 명시적 `RuntimeError` 여야 한다 (silent 처리 금지).
- **FR-006**: 시스템은 run 1회당 `CostBudget` 1개로 모든 LLM 호출 비용을 누적하고, `ROBO_BUDGET_RUN_USD`(기본 20.0) 초과 시 `BudgetExceededError` 를 raise 해 파이프라인을 자동 중단해야 한다 ($400 사고 재발 방지).
- **FR-007**: 시스템은 모든 LLM 호출(Agent 미들웨어 경로 + Agent 외부 yaml 룰 경로) 1건당 `usage.jsonl` 에 한 줄(agent·model·input/output/cached/total tokens·latency·cost_usd)을 적재해야 한다 — 측정 사각지대 금지.
- **FR-008**: run 종료 시 `usage.jsonl` 을 `usage_summary.json` 으로 집계해야 한다 — `total` + `per_agent_model`(agent×model 별 토큰/비용/호출수).
- **FR-009**: 토큰 사용량 추출은 LangChain `usage_metadata` 표준으로 provider 차이를 흡수해야 하며, `cached_tokens` 미노출 환경은 latency 기반 추정으로 fallback 한다.
- **FR-010**: 비용 계산 가격은 `llm_pricing.yaml` 단일 진실이어야 하고, 모델 매칭은 정확 → 날짜 suffix strip → provider prefix 제거 → substring 순이며, 매칭 실패 모델은 비용 0 + 모델당 1회 경고.
- **FR-011**: 구조화 출력은 응답 Pydantic 모델을 "끝에 한 번 호출하는 submit 도구"로 wrap 해 강제해야 하며, LangChain 자동 ToolStrategy(`tool_choice="any"`) 진입을 차단해 qwen+SGLang xgrammar hang 을 회피한다.
- **FR-012**: 에이전트가 submit 도구를 호출하지 않고 종료하면 명시적 `ValueError` 로 표면화해야 한다 (silent 실패 금지).
- **FR-013**: yaml 룰 러너는 `prompts/<strategy>/<name>.yaml`(+`extends`/`_common` 으로 분할 그룹의 system 본문 byte-identical 보존, KV 캐시 prefix 적중) 을 로딩해 단순 LLM 호출을 수행하고, 같은 `append_usage_record` 헬퍼로 회계를 통일해야 한다.
- **FR-014**: 모든 `ROBO_*` 환경변수는 `env_catalog.py` 의 `ENV` 싱글톤 단일 진입점으로만 접근해야 하며, 명명 규약 `ROBO_<영역>_<이름>` + 단위 suffix(`_SECONDS`/`_TOKENS`/`_USD`/`_PCT`/`_RATIO`)를 따라야 한다 (새 env = 이 파일 1곳만 수정).
- **FR-015**: 모든 LLM 예외(401/429/400/timeout/network)는 silent 차단 금지 — 명시 로그 후 그대로 raise 해야 한다.

### Key Entities *(include if feature involves data)*

- **LLM config yaml** (`llm_configs/<name>.yaml`): 모델+배포환경 1묶음. provider / model / model_small / api_base / max_tokens / timeout_seconds / verify_ssl / sampling / reasoning_effort / extra_body. `LLMConfig` dataclass 1 인스턴스로 매핑.
- **ENV 싱글톤** (`env_catalog.ENV`): 영역별 frozen dataclass 묶음 — LLM / CACHE / AGENT / BUDGET / NEO4J / PIPELINE / CATALOG / LOG / DATA. `ENV.<영역>.<이름>` 으로만 접근.
- **usage.jsonl**: 1 LLM 호출 = 1 줄. {agent, model, input/output/cached/total tokens, latency_ms, cost_usd}. Agent 미들웨어와 단순 호출이 동일 형식 적재.
- **usage_summary.json**: run 집계 산출물. `total` + `per_agent_model`(agent·model 키별 토큰/비용/calls).
- **CostBudget**: run 1회의 누적 비용 추적기. `limit_usd`(=`ROBO_BUDGET_RUN_USD`), `spent_usd`. 초과 시 `BudgetExceededError`.
- **submit-tool wrap**: 응답 Pydantic 모델을 args_schema 로 하는 `@tool`. 호출 시 결과를 state(`structured_response`)에 적재 + `goto="__end__"` 로 루프 종료.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: provider/배포 환경 전환에 필요한 파이썬 코드 변경량 = 0 줄 (`.env` 1줄 + 새 yaml 1개로 완료).
- **SC-002**: run 당 누적 비용이 `ROBO_BUDGET_RUN_USD` 를 넘으면 100% 자동 중단되며, 단일 run 이 한도(기본 $20)를 초과해 청구되는 사고 0건.
- **SC-003**: `usage.jsonl` 의 줄 수 = 실제 LLM 호출 수 (Agent 내부+외부 포함), 측정 누락 0 — `usage_summary.json` 의 `total.cost_usd` = 줄별 cost 합과 일치.
- **SC-004**: qwen+SGLang 스택에서 구조화 출력 에이전트 invoke 의 grammar-mask hang 0건, submit 미호출은 100% `ValueError` 로 표면화.
- **SC-005**: 새 `ROBO_*` env 추가 시 수정 파일 수 = 1 (`env_catalog.py`).

## Assumptions

- 본 기능은 기존 cross-cutting 인프라를 reverse-engineer 한 것으로, 새 동작이 아니라 현재 코드의 사실을 기술한다.
- LangChain(`langchain_openai.ChatOpenAI` / `langchain_anthropic.ChatAnthropic`) 과 `usage_metadata` 정규화에 의존한다.
- 운영자가 yaml 의 provider 별 호환성(특히 `extra_body` 비표준 키)을 책임진다 — 코드는 표준 키만 자동 처리한다.
- 비용 정확도는 `llm_pricing.yaml` 의 가격이 최신이라는 전제에 달려 있다 (`cli_scripts/refresh_pricing.py` 로 갱신).
- 비용 차단은 run(프로세스) 단위 소프트 가드다 — 호출 후 가산 방식이라 단일 호출의 사전 차단은 아니며, 디스크 영속 한도 추적은 범위 밖.
- `cached_tokens` latency fallback 추정은 정확도가 낮으므로 공식 노출 환경(SGLang `--enable-cache-report` ON 등)을 우선한다.
