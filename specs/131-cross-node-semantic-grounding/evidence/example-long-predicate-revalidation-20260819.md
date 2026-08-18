# APAS 장문 Rule 조건 Example 누락 근본 수리 검증 — 2026-08-19

## 판정

실제 APAS 기준 run의 `conditional+outcome but no Example` 12건은 Rule·parser·Neo4j 저장 결함이
아니었다. accepted Rule에서 Example을 결정론 합성한 뒤 과거 LLM 출력 폭주 방어용 500자 leaf
상한을 다시 적용해, 575~1,976자의 parser-owned opaque predicate를 조용히 버린 것이 원인이다.

Example은 현재 LLM 0이며 Rule의 검증된 `cond/then` truth를 투영한다. 따라서 길이로 버리거나 C
연산자를 Analyzer에서 다시 파싱하지 않고, Rule과 같은 개행 없는 leaf를 무손실 보존하게 했다.

## source→저장 사슬

```text
parser-owned condition/effect
  → deterministic Rule cond/then (정상)
  → synthesize_examples._fits(term)
  → len(term) <= 500이면 보존, 아니면 조용히 제거 (결함)
  → Example []
  → graph HAS_EXAMPLE 없음 12건
```

수정 후 사슬:

```text
accepted Rule cond/then
  → synthesize_examples._fits(term): non-empty + no-newline만 검사
  → ExampleItem 동일 leaf 검증
  → graph EXAMPLE + HAS_EXAMPLE
  → judge_run이 accepted Rule 전수 재합성·graph exact 대조
```

## 자동·실제 검증

- red fixture: 80개 항을 가진 긴 opaque `F("...")` 조건은 수정 전 Example 0건이었다.
- 표적 계약: Example 합성·structured transport 24 tests 통과.
- Analyzer 전체: `Ran 1038 tests`, `OK (skipped=5)`.
- prompt ledger: `OK - 23 prompts byte-identical`.
- 기준 cold run: `20260818_224211_94292c026804`, cache 0, 567 calls, 710.9초.
- 수정 후 cold run: `20260818_230556_56dd57726596`, cache 0, 567 calls, 647.7초.
  Example 후처리만 바뀌었으므로 두 wall 차이를 성능 개선 인과로 주장하지 않는다.
- 두 run 모두 Rule 호출은 0이며 수정 후에도 모델 입력·출력 호출 수는 늘지 않았다.

`judge_run.py <final-run> --graph`:

```text
Rule obligation 4810 = trusted 4810 + Rule findings 0 + unavailable 0
graph RULE expected 4810 / actual 4810 / missing 0 / extra 0 / mismatch 0
HAS_EXAMPLE 3391
conditional+outcome Example missing 0
orphan Example 0
```

기준 graph의 HAS_EXAMPLE은 3,379였으므로 정확히 12개가 복구됐다. 이전 누락 좌표 12개를
현재 graph에서 직접 조회한 결과 Rule 12/12, Example 12/12가 존재했고 Example `then`과 Rule
`then`은 12/12 exact였다. 복구된 given의 최장 leaf 범위는 575~1,976자로 전부 과거 상한을
넘는다.

`evidence_preflight.py diff <final-run> --corpus <APAS>`:

```text
stored target 416 / rows 4810
current target 416 / rows 4810
difference 0
```

## 비영향·남은 축

- Rule cond/then, parser AST, prompt hash, LLM call 수, TABLE/COLUMN과 Architect 계약은 바꾸지 않았다.
- final run의 summary unverified finding 4건은 이 Example 결함과 별개이며 직접 의미 판정에서
  source·actual prompt·candidate·finding을 판정한다.
- 봉인 의미 40과 Architect actual GWT가 끝나기 전에는 전체 작업 완료가 아니다.
