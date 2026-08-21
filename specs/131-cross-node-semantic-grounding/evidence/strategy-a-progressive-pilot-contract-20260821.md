# Strategy A 실제 데이터 단계 검증 계약

**Status**: stage 1 sealed before current-model result access
**Date**: 2026-08-21

## 결론

Strategy A는 Python 관계식 `given`/`then_relation`이 실제 원문 의미를 보존하는지 확인하는 실험이다.
제품 배선으로 승격하지 않고, 실제 shopmall 자료의 작은 영역부터 세 층을 모두 통과할 때만 확대한다.

## 불변 입력

| 입력 | actual |
|---|---:|
| 실제 source | 11 files, sealed SHA-256 mismatch 0 |
| 봉인 모집단 | 20 functions |
| 실제 prompt records / exact rows | 77 / 519 |
| sample SHA-256 | `f4867936998b0cc6d0be21b286b3d78734388045511221a56febbe6ad9e9f268` |
| gold SHA-256 | `cdcdd19efa08a88aedd40918c8c33b4de7e0cbbc09a868821e7e9b56a2f8a7ec` |
| 역사 runner SHA-256 | `db5ea924ae3193644527d907b157e3f370d179ebcecbb9adf6099dd370a0aa58` |
| 역사 raw result SHA-256 | `8c14d1928a6fea3afe4720a6cd343fe5eacff8b772f4d43bd9009a7b1d24f962` |
| 역사 summary SHA-256 | `9c01436e3602da477555f66503ea7fc8faff9f7ecf6326a3c8b1858bcaf4c45d` |

원본 source, sample, gold, prompt ledger, 역사 runner/result/summary는 수정하지 않는다.

## stage 1 봉인

| 영역 | 함수 | actual records | exact rows | gold cases |
|---|---|---:|---:|---:|
| complex | `member_authenticate` | 8 | 62 | 8 |
| medium | `apply_point_balance` | 4 | 30 | 6 |
| state transition | `next_ship_status` | 2 | 10 | 3 |
| negative control | `db_commit` | 1 | 2 | 1 |
| 합계 | 4 functions | 15 | 104 | 18 |

선정은 current-model 결과를 열기 전에 했다. 기존 층마다 구조 의무가 가장 많은 어려운 대상을 우선하고,
단순 코드에 없는 업무 의미를 만들지 않는지 확인하는 음성대조를 별도로 포함했다. selected prompt ledger
SHA-256은 `75292142cb95b9980b1d3064857a517cbfbfcb4dd43ab4fbebe36a224ee4182e`다.

## 안전 실행 계약

- 출력은 `specs/131-cross-node-semantic-grounding/_runs/framework/strategy-a/` 아래 새 run directory에만 쓴다.
- 기존 `run_strategy_a_v3.py`와 tracked 역사 evidence를 수정·truncate하지 않는다.
- repeats=1, selected actual records=15, 중복·누락=0이다.
- schema에는 exact row key만 두고 arbitrary `maxLength`를 두지 않는다.
- provider output 한계를 content cap으로 성공 처리하지 않는다. finish reason, raw/partial, exact record/row를
  보존하고 해당 record를 실패 또는 unavailable로 회계한다.
- content retry와 LLM repair/judge는 없다. transport 이전 실패만 호출 전 상태로 구분하고 조용히 재호출하지 않는다.
- actual system/user/schema bytes와 SHA-256, raw/parsed, usage, latency, finish reason을 record별로 보존한다.
- Neo4j·Analyzer pipeline·Architect를 실행하거나 변경하지 않는다.

## stage 1 합격과 확대

1. 자동 계약: transport/JSON/schema와 15 record exact-once가 모두 닫힌다.
2. 결정론 회계: 104 expected rows = parsed rows + explicit failed/unavailable rows, residual 0, duplicate 0이다.
3. 식 검증: non-empty required expression은 Python AST로 parse되고 symbol/literal은 해당 row evidence에 grounded다.
4. 직접 의미 판정: 18 sealed gold case를 source→evidence→actual prompt→response로 읽어 exact/partial/
   contradiction/fabrication/omission/needs_context로 판정한다.
5. fabrication 또는 contradiction이 하나라도 있거나, required row omission/residual이 하나라도 있으면 확대하지 않는다.
6. 네 조건을 모두 통과할 때만 stage 2의 20 functions/77 records/519 rows를 current model 1회 실행한다.

이전 모델은 같은 stage 1 입력의 역사 3회 결과를 잘라 비교한다. 현재 모델은 1회이므로 동일 입력의 단발
정확도는 비교할 수 있지만 현재 모델의 반복 안정성은 미검증이다.

## 보고서

최종 비교는 `D:\work\robo\docs\작업보고내용`에 2026-08-21 날짜로 작성한다. 비개발자가 읽을 수 있게
전략 A, 이전 모델, 현재 모델, 원문 의미 대조, 확대 판정만 설명하고 C-080 등 기존 개선 작업은 각각 한 줄
상태로 남긴다.
