# Qwen agent hang 격리 시험

frentis-ai-model (SGLang 호스팅 Qwen3.6) 의 분석 파이프라인에서 agent (tool calling +
structured response_format) 호출만 hang 되는 증상을 5 시나리오 격리 시험으로 원인
영역을 좁히기 위한 도구. **분석 본체 코드와 격리** — 본체 변경 없이 실험.

## 증상 (재현 확인됨)

- 단순 LLM 호출은 정상 (수십 회 빠르게 응답)
- agent 첫 3~4 라운드도 정상 (작은 응답)
- 그 다음 structured response 생성 시점에 **첫 1~2 토큰만 보내고 무응답**
  (TCP 살아있는데 다음 토큰 안 옴, 입력 8000 토큰, 죽은 상태)
- 서버 측 에러 응답 없음, generation 만 멈춤 → 클라이언트 stream chunk timeout
- 시도한 옵션 다 같은 증상: `max_tokens` 128K→20K / streaming on·off / `enable_thinking` on·off

## 시나리오 매트릭스 (5)

| # | tool calling | response_format | 입력 토큰 | 의도 |
|---|------|-----------------|----------|------|
| **A** | OFF | OFF | ~8K | 기준선 — LLM 자체 정상 동작 확인 |
| **B** | ON  | OFF | ~8K | tool calling 만 hang 인지 |
| **C** | ON  | ON  | ~8K | **현재 hang 패턴 재현** |
| **D** | OFF | ON  | ~8K | response_format 만 단독으로 hang 인지 |
| **E** | ON  | ON  | ~1K | C 동일 옵션 + 작은 입력. 입력 크기가 trigger 인지 |

## 사용

### 환경

- `.env` 의 `ROBO_LLM_CONFIG=qwen36_sglang_local` (frentis-ai-model 가리키는 yaml)
- `ROBO_LLM_API_KEY` 설정 (실제 SGLang 서버 호출용)
- Python 의존성: 분석 본체 그대로 (`requirements.txt`)

### 단일 시나리오

```bash
python -m experiments.qwen_agent_diagnose.diagnose --scenario C
python -m experiments.qwen_agent_diagnose.diagnose --scenario A --timeout 30
```

### 5 시나리오 일괄 (권장 — 첫 진단)

```bash
python -m experiments.qwen_agent_diagnose.diagnose --all --timeout 60
```

순서 = A→B→D→E→C (정상 기대 우선, 현재 hang C 마지막).

### 결과

`experiments/qwen_agent_diagnose/results.jsonl` 에 누적 (한 줄 = 1 시도).
각 라인:

```json
{
  "scenario": "C",
  "tool_calling": true,
  "response_format": true,
  "input_tokens_estimate": 2000,
  "status": "timeout",
  "elapsed_sec": 60.05,
  "first_token_sec": 0.84,
  "chunk_count": 2,
  "output_preview": "{\"sum",
  "error_type": "TimeoutError",
  "error_message": "60s 안에 응답 종료 X",
  "timestamp": "2026-05-27T18:30:00"
}
```

`first_token_sec` 와 `chunk_count` 가 핵심 — **첫 토큰 받고 0~2 chunk 후 멈추면 hang
확정** (서버 측 grammar-guided sampling 멈춤 또는 tool parser 미설정 영향).

## 결과 해석 (패턴 → 영역)

| A | B | D | E | C | 진단 |
|---|---|---|---|---|------|
| ✅ | ✅ | ✅ | ✅ | ❌ | tool + response_format **결합** 만 hang. SGLang grammar sampling 과 tool parser 충돌 의심 |
| ✅ | ❌ | ✅ | — | ❌ | tool calling 자체 문제. `--tool-call-parser qwen` 옵션 미설정 의심 |
| ✅ | ✅ | ❌ | — | ❌ | response_format (structured output) 자체 문제. JSON grammar sampling 영향 |
| ✅ | ❌ | ❌ | — | ❌ | tool / response_format 둘 다 문제. SGLang 빌드 옵션 의심 |
| ✅ | ✅ | ✅ | ✅ | ✅ | hang 재현 X (환경 변경됨 — 운영팀 변경 사항 확인) |

## 운영팀 협업 — 서버측 확인 항목

격리 시험으로 영역 좁힌 후 운영팀에 같이 봐주실 수 있는 항목:

1. **SGLang launch 옵션** — `--tool-call-parser qwen` (또는 `qwen25`) 박혀있나?
   Qwen 공식 문서에서 필수라 명시.
2. **k8s pod 로그 hang 시점** — 시나리오 C 가 timeout 으로 멈춘 직후 서버측
   generation 로그 (`POST /v1/chat/completions` 직후 → `forward_batch_generation_*`
   같은 SGLang 내부 로그) 가 어디서 끝나는지.
3. **`--grammar-backend` 옵션** — structured output 의 JSON 강제 sampling 이
   어떤 backend (`xgrammar` / `outlines` / `llguidance`) 인지. 일부 backend 가
   tool calling 과 결합 시 hang 사례 있음.
4. **메모리·KV cache 상태** — hang 시점에 KV cache 가 가득 차거나 OOM 직전인지.

## 본체 적용

원인 영역 좁혀지면:

1. `.env` 또는 yaml 에 `agent_use_tools` / `agent_use_structured_response` 토글 추가
2. `shared/llm/structured_agent.py` 에 두 옵션 분기 추가 (현재 항상 둘 다 강제)
3. 본체 분석 파이프라인이 hang 회피 모드로 동작 → 응답 파싱은 코드 책임으로 이전
4. 회귀 검증 — frentis-ai-model 단독으로 38 함수 분석 완주

## 본체 격리 보증

- 본체 yaml (`prompts/framework/*.yaml`) 안 건드림 — 자체 `prompts.py` 사용
- 본체 응답 모델 (`SummaryOutput` 등) 안 건드림 — 자체 `MiniSummaryOutput` 사용
- 본체 코드 (`structured_agent.py` / `yaml_llm_runner.py`) 안 import — `build_llm_client` 만
- 결과 jsonl 도 별도 위치 (`experiments/qwen_agent_diagnose/results.jsonl`)
