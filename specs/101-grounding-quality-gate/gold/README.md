# gold set — 출력 충실도 감사 동결본 (2026-07-24)

`docs/핸드오프/analyzer-성능/05` 감사가 **실제로 대조한 입력·출력 원본**이다. 게이트 임계
판정(환각 29 · 누락 25)의 유일한 기준선이므로 저장소 안에 동결한다.

| 파일 | 내용 |
|---|---|
| `fn_00..23.json` | 24함수의 `prompt_user`(입력 전문) + `rules`/`questions`(그 때의 산출물) |
| `audit_results.json` | 함수별 감사 결과 + 적대적 검증 verdict. `conf_ungrounded` 합 29, `conf_missing` 합 25 |

## ⚠ 반드시 알아야 할 것

**`logs/llm-prompts/code_rules_examples.json` 의 현재 내용은 이 gold 가 아니다.**
그 로그는 이후 런이 덮었다(예: `zapamcom10080` 은 감사 당시 rules 1개, 이후 로그엔 0개).
현재 로그로 대조하면 항목이 어긋난다 — 반드시 이 폴더의 `fn_XX.json` 을 쓴다.

## 무효화 조건

`code_rules_examples` 프롬프트나 `RulesExamplesOutput` 스키마가 바뀌면 이 gold 는 **더 이상
같은 규칙을 가리키지 않는다.** 그 순간 29·25 라는 수치와 그것으로 세운 임계가 함께 무효다.
바꿀 때는 재감사 계획을 먼저 세운다(`specs/102-rule-anchor-citation/spec.md` §5·§6).
