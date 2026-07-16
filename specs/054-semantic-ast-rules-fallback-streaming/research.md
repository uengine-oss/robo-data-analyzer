# Research: 임계값 시작값 근거 + 스트림 Coverage Matrix

**목적**: spec 054의 결정론 임계값·예산 시작값의 근거를 기록하고, 파이프라인 전체 스트림 커버리지를
감사한다. 모든 시작값은 C 실측(회귀 표본) 후 조정 대상이며, 조정 결과는 이 문서와 최종 보고에 남긴다.

## 1. 품질검사 임계값 시작값 (보존 run 근거)

`_runs/prompt-iteration-3/`의 실측(핸드오프 §7):

| 함수 | 원문 | 정상 Example | 이상 Example | 최다 반복 |
|---|---|---|---|---|
| load_target_orders | 144줄, if 13 | ~7 고유 | 60개(53 반복) | "결제누락→제외" 24회 |
| get_code_name | 79줄, if 47 | 9(깨끗) | 47/30/42 흔들림 | — |
| calc_discount | 137줄, if 25 | 21~22 | 47개 | 특정 Rule 동일 29회 |

**도출한 시작값**:

- **정규화 중복 비율 ≥ 20%** → 이상. (load_target_orders 88%는 명확 이상, 정상 0~5%와 큰 여유. get_code_name
  정상 run은 0에 가까움.)
- **동일 의미 그룹 ≥ 3회** → 이상. (24회·29회는 명백. 정상은 "같은 결과 값 묶음"이라도 given 집합으로
  1행에 합쳐지므로 3회 이상 완전중복이 정상 경로에서 드묾.)
- **examples 길이가 스키마 캡(80)에 정확히 도달** → 잘림 의심 이상. (과거 30 캡에 딱 닿던 신호를 새 캡에서도
  동일 원리로 감지.)
- **지연시간(100초+)**: 로그 관찰 신호로만. 단독 이상 판정 금지(핸드오프 §7 사용자 결정).

**주의**: get_code_name(if 47)이 9개로 깨끗한 run도 있었고 load_target_orders(if 13)가 터졌다 → **if 개수·
줄 수로 사전 분할하지 않는다.** 최초 전체 분석 결과 기반 판정이 사용자 선택.

## 2. 조각 예산 시작값

- 예산 단위: **문자 길이 근사**(외부 tokenizer 미도입, 원칙 I·의존성 최소화). 근사식: `approx_tokens ≈
  len(text)/A` (A는 코드 평균 문자/토큰, 시작값 3.5 — 영문·기호 코드 기준. 실측 후 조정).
- 분할 시작 임계: 함수 프롬프트 입력 근사 토큰이 **~4,000** 초과 시 분할 착수. (load_target_orders 입력
  13,474 토큰은 명백 초과. 정상 소형 함수는 미달 → 미분할.)
- 조각 목표 크기: **~2,000 토큰** 상당. 상위 제어문 서브트리가 이 안이면 통째로, 초과 시만 한 단계 재귀.
- 위 세 값(A, 착수 임계, 조각 목표)은 research 시작값이며 C 실측으로 조정한다.

## 3. max_length 결정 (FR-022)

- `RuleItem.examples`: 30 → **80**. 근거: 관측 정상 최댓값 47(calc_discount 이상 run) + get_code_name 정상
  9~변동 47. 80은 정상 폭을 여유 있게 덮으면서 수백 개 폭주(4·5차 사고)는 여전히 차단.
- `RulesExamplesOutput.rules` / `BatchRulesItem.rules`: 30 유지. Rule 개수 폭주는 관측된 적 없음(실제 2~7개).
- 둘 다 "provider 출력 폭주 안전망"이지 "정상적으로 많은 것을 막는 비즈니스 규칙"이 아님을 docstring에 명시.

## 4. 스트림 Coverage Matrix (FR-027)

핸드오프 §12 기준. "충분" = 짧은 결정론 단계라 phase+완료로 족함. "보강" = 다건 loop/외부 HTTP/LLM/DB
index wait라 start/done/failed 필요.

| 구간 | 현재 | 판정 | 조치 |
|---|---|---|---|
| step1 DDL 파싱 | graph+message | 충분 | (결정론 단기) 유지 |
| step2 AST 로드 | graph+message | 충분 | 유지 (+제어문 보존은 graph emit로 자연 노출) |
| step3 scan | tool stream+message | 충분 | 유지 |
| step4 결정론 linking | graph+message | 충분 | 유지 |
| call linking | message 위주 | 검토 | 다건이면 batch throttle emit 추가 검토 |
| step5 context loop | 내부 진행 약함 | **보강** | 다건 loop start/done 추가 |
| fetch_table_samples | scope만, chunk 추적 없음 | **보강** | chunk별 start/done/failed |
| step6 analyze | 풍부 + (신규)폴백 | 충분 | 폴백 이벤트 신규(stream-events.md) |
| summarize_module | scope/track/node | 충분 | 유지 |
| enrich_table_description | scope/track, 갱신 emit 불명 | 검토 | 갱신 node emit 여부 확인 후 결정 |
| build_components | scope/track, 갱신 emit 불명 | 검토 | 갱신 graph emit 여부 확인 후 결정 |
| build_glossary | 내부 상세 약함 | **보강** | LLM 다건 start/done/failed |
| health_metrics | 내부 stream 없음/약함 | **보강** | 결정론 다건이면 요약 진행 emit |
| enrich_lineage_from_catalog | 내부 stream 없음/약함 | **보강** | 다건 loop start/done/failed |
| step7 embed | code batch 있음; dim check/index create/await/fingerprint는 로그만 | **보강** | index 관련 processing emit (사용자 직접 지적) |

**보강 확정 구간**: step5 context loop, fetch_table_samples, build_glossary, health_metrics,
enrich_lineage_from_catalog, embed index. **검토 구간**(call linking, enrich_table_description,
build_components)은 구현 시 실제 emit 여부를 코드로 확인해 matrix를 확정하고, 미보강 잔여는 사유와 함께
[[todo]]에 기록(누락을 완료로 위장 금지).
