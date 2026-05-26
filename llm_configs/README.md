# LLM Config — 모델 / 배포 환경별 설정

## 무엇

이 폴더의 YAML 1개 = **모델 + 배포 환경 1개의 설정 묶음**.

예시:
- `qwen36_sglang_local.yaml` — Qwen3.6 모델을 사내 SGLang 으로 배포한 환경
- `qwen36_openrouter.yaml` — 같은 모델을 OpenRouter 로 호출하는 환경
- `gpt4o_openai.yaml` (예시) — OpenAI 직접 gpt-4o
- `claude4_anthropic.yaml` (예시) — Anthropic 직접 Claude

`.env` 에 어떤 config 쓸지 이름만 박음:

```bash
# .env
ROBO_LLM_CONFIG=qwen36_sglang_local    # llm_configs/qwen36_sglang_local.yaml 사용
ROBO_LLM_API_KEY=<secret>               # 비밀만 별도 (secret manager 친화)
```

## 왜 분리?

**문제**: 같은 모델이라도 배포마다 인자 다름.

| 항목 | 사내 SGLang | OpenRouter |
|---|---|---|
| api_base | `http://ai-server...:30000/v1` | `https://openrouter.ai/api/v1` |
| model 이름 | `frentis-ai-model` | `qwen/qwen3.6-35b-a3b` |
| reasoning 끄기 | `chat_template_kwargs.enable_thinking=false` | `reasoning_effort=none` |
| `top_k` / `min_p` / `presence_penalty` | 받음 | 무시 |

→ 환경별 YAML 1개로 정리하면 **`.env` 1줄 변경 = 환경 전환** 완료.

## 새 config 추가하는 법 (3단계)

### 1. YAML 파일 생성

`llm_configs/<name>.yaml` (예: `myco_vllm_qwen.yaml`):

```yaml
provider: vllm                  # anthropic 외는 모두 OpenAI 호환 처리
api_base: http://my-server:8000/v1
model: Qwen/Qwen3-72B-Instruct
model_small: ""                 # 작은 모델 안 쓰면 빈 값 (model 사용)
max_tokens: 8192
timeout_seconds: 300
verify_ssl: true                # 사내 자체 인증서면 false
sampling:
  temperature: 0.0
  top_p: 1.0
reasoning_effort: none          # none / low / medium / high
extra_body:                     # provider 비표준 인자 (없으면 {} )
  top_k: 20
  min_p: 0.0
```

### 2. `.env` 의 `ROBO_LLM_CONFIG` 변경

```bash
ROBO_LLM_CONFIG=myco_vllm_qwen
```

### 3. 끝 — 코드 변경 0

## YAML 스키마 (필드 의미)

| 필드 | 필수 | 의미 |
|---|---|---|
| `provider` | ✅ | `openai` / `anthropic` / `openrouter` / `sglang` / `vllm` / 자유 명명. ``anthropic`` 외는 모두 OpenAI 호환 처리. |
| `api_base` | provider=anthropic 외 ✅ | LLM endpoint URL. anthropic 은 SDK default 사용. |
| `model` | ✅ | 기본 모델 이름 (provider 형식 그대로). |
| `model_small` | 옵션 | 작은 모델 (큰 함수 one_liner 등). 빈 값이면 `model` 사용. |
| `max_tokens` | ✅ | 최대 output 토큰. |
| `timeout_seconds` | ✅ | LLM 호출 타임아웃. |
| `verify_ssl` | ✅ | SSL 검증 (사내 자체 인증서면 `false`). |
| `sampling.temperature` | ✅ | 표준 sampling — 모든 provider 공통. |
| `sampling.top_p` | ✅ | 표준 sampling — 모든 provider 공통. |
| `reasoning_effort` | ✅ | `none` / `low` / `medium` / `high`. **cross-provider 추상화** — 코드가 provider 별 키로 자동 변환. |
| `extra_body` | ✅ (비울 수 있음) | provider 비표준 인자 dict. `{}` 이면 미전달. |

## `reasoning_effort` cross-provider 매핑

**표준 키만 코드가 자동 변환** — 비표준 키는 yaml 운영자 책임.

| provider | 처리 |
|---|---|
| OpenAI (o-series, gpt-5) | `reasoning_effort=none` 미전달 (= default). `low/medium/high` 만 전달 — **자동** |
| Anthropic (Claude 3.7+) | `thinking={"type":"disabled"}` (none) / budget=1k/4k/16k (low/med/high) — **자동** |
| SGLang / vLLM / 로컬 | yaml `extra_body.chat_template_kwargs.enable_thinking: false` 운영자 직접 명시. 코드 자동 주입 X (hosted API 거부 400 사고 차단) |
| OpenRouter | OpenAI 표준으로 normalize — `reasoning_effort` 그대로. 단 backend 마다 호환성 다름, 안정성 보장 X |

## 비밀 (API key) 분리 정책

YAML 에 API key 박지 **X**. `.env` 의 `ROBO_LLM_API_KEY` 만 사용.

이유:
- YAML 은 git commit 대상 (운영 정책 공유)
- API key 는 secret manager / 환경별로 다름
- 운영자 / 환경 분리 가능

## 동작 검증

```bash
# 새 config 작성 후
python -c "
from shared.llm.llm_config_loader import load_llm_config
cfg = load_llm_config('<your_config_name>')
print(cfg)
"
```

누락 필드 / 잘못된 형식 = 명시 오류 (silent 처리 X).
