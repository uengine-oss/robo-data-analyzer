# Structured output·SGLang 1차 자료 조사

**Date**: 2026-08-12
**Purpose**: T131-20/T131-21 설계에서 관측한 사실과 추론을 분리한다.

## 공식 자료에서 확인한 사실

1. SGLang structured outputs는 JSON Schema, regex, EBNF 중 요청당 한 제약을 사용하며 기본
   backend는 XGrammar다. 공식 문서는 grammar가 있어도 prompt에 원하는 형식을 명시하라고
   권한다. 따라서 schema를 만들었다는 이유로 output contract와 예시를 삭제하지 않는다.
   - https://projects.localizethedocs.org/sglang-docs-l10n/en-us/latest/advanced_features/structured_outputs.html
2. SGLang metrics는 서버를 `--enable-metrics`로 시작해야 하고 cache hit rate, prompt/generation
   token, TTFT/E2E latency 등을 노출한다. 현재 배포의 `/metrics`는 404였으므로 cache hit은
   관측하지 못했다.
   - https://docs.sglang.io/docs/references/production_metrics
3. 일반 temperature 0만으로 dynamic batching 환경의 실행 결정성이 보장되지 않는다.
   SGLang의 deterministic inference는 별도 `--enable-deterministic-inference`와 지원 backend를
   요구한다. 현재 과거 server info에는 이 옵션이 false로 기록돼 있으므로 actual run의 문자열
   일치성을 가정하지 않는다.
   - https://sgl-project.github.io/advanced_features/deterministic_inference.html
4. SGLang 논문은 RadixAttention으로 prefix KV를 재사용하고 compressed finite-state machine으로
   constrained decoding을 가속하는 구조를 설명한다.
   - https://arxiv.org/abs/2312.07104
5. XGrammar 논문은 context-free grammar 기반 구조 제약의 효율과 범용성을 설명한다. 이는 JSON
   shape/grammar 보장의 근거이지 코드 의미의 근거성·완전성을 보장한다는 증거가 아니다.
   - https://arxiv.org/abs/2411.15100

## 이 작업에 적용하는 결론

- dynamic JSON Schema는 required slot population과 status 조합을 provider-visible하게 강제한다.
- semantic correctness는 StructuralProfile ownership, grounding/completeness validators, 정답지,
  직접 의미 판정이 별도로 소유한다.
- stable system prefix와 variable suffix는 serialized prompt bytes로 직접 확인한다.
- metrics가 켜진 배포에서만 cache hit을 성능 근거로 기록한다. 그 외에는 prompt/schema bytes,
  input/output token, repetition, latency를 관측값으로 쓴다.
- exact 문자열 재현이 아니라 구조·근거·의미 판정 재현성을 검증 기준으로 삼는다.
