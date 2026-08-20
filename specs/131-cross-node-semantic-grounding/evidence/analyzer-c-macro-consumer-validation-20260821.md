# Analyzer C macro evidence consumer 검증 원장 — 2026-08-21

## 1. 책임 경계와 의미 계약

이 슬라이스는 Parser v1 `macro` fact를 Analyzer가 strict 검증·보존하고, 함수형 macro와 이름이 같은 call을
근거 없는 external/internal/framework edge 또는 제외로 확정하지 않는 데까지 승인한다. Analyzer의 source
정규식 재파싱은 제거한다.

다만 `#define` declaration fact는 특정 call 시점의 effective preprocessing state와 같지 않다. 그 상태는
ordered `#undef`/재정의/include와 build predefined macro에 의존한다. 따라서 현재 payload만으로는 active
declaration도 “이 call이 확실히 macro expansion이다”라고 단정하지 않는다. 같은 source/name의 active
함수형 declaration은 `insufficient_macro_expansion_state`, conditional/unknown declaration은
`insufficient_conditional_macro_definition`으로 보존한다. inactive declaration과 object macro는 call을
억제하지 않는다. definitive exclusion은 Parser가 call 시점의 전처리 상태를 canonical evidence로 발행한
뒤에만 가능하다.

```text
exact source bytes
-> pinned Parser C-preprocessor grammar/listener
-> canonical macro declaration fact + presence
-> Analyzer hash/decode/range/ID/payload/completeness validator
-> exact-once macro_facts state
-> call sourceId + terminalName join
-> explicit unresolved outcome + macroFactIds
```

이 경계는 WG14 N3096 §6.10의 define/undef preprocessing directive와 translation-unit 전처리 순서에 따른다.
근거 원문과 채택/기각 기록은 `c-preprocessor-grammar-research-20260821.md`에 고정했다.

## 2. formal red와 구현

구현 전 red는 다음을 고정했다.

- grammar macro fact 인자 자체가 없어서 classifier가 받을 수 없음: 3/3 failure.
- macro payload의 exact name/kind/range/replacement 검증과 immutable typed fact가 없음: 5/18 failure.
- active declaration만으로 macro라고 제외하던 종전 happy path를 금지하고 expansion state 미확정을 요구.
- conditional `PFM_DBG` declaration을 framework runtime 목록이 덮어써 제외하지 못하도록 우선순위를 고정.
- object macro와 inactive function declaration은 call을 억제하지 않도록 반례를 고정.

구현 후 validator는 nonempty directive/name range, exact decoded name slice, `object|function`, ordered nonempty
parameter ranges, boolean variadic, nullable replacement subrange, canonical fact ID와 presence/completeness를
Neo4j publish 전에 검사한다. `ParseResult`와 step2 state는 183개 macro fact와 canonical ID를 그대로 운반한다.
call classifier에서는 `_FUNCTION_MACRO_RE`, `_function_macros_in_file`, `SourceFileCache` 인자를 전부 제거했다.
Analyzer가 C identifier 문법이나 `#define` line을 해석하는 경로는 이 소비자에 남지 않는다.

## 3. 자동 회귀

- 집중 step2/step5: `210 passed, 1 skipped, 19 subtests`.
- actual opt-in deterministic replay: `1 passed`.
- 권위 unittest: `1122 tests OK, skipped 6`.
- 전체 pytest: `1200 passed, 7 skipped, 367 subtests`.
- `compileall`, `git diff --check`: error 0.

## 4. actual APAS 결정론 전수 회계

허용된 `_runs/framework/analyzer-macro-consumer-fixture-20260821-r2/`만 read-only로 두 번 소비했다. LLM과
Neo4j read/write는 0회다.

| 항목 | 값 |
|---|---:|
| source files / CodeNodes | 23 / 7,921 |
| call facts / unique IDs | 12,113 / 12,113 |
| macro facts / unique IDs | 183 / 183 |
| object / function macro | 182 / 1 |
| active / conditional macro | 182 / 1 |
| call outcome exact-once | 12,113 / 12,113 |
| edge / excluded / preserved / unresolved | 869 / 11,050 / 33 / 161 |
| stdlib / framework / macro excluded | 6,918 / 4,132 / 0 |
| wrapper / macro unresolved | 85 / 76 |
| macro fact ledger SHA-256 | `b1f51f924272511d019adc6ff3bf831aa1445c66e113da1742fc278ee41ae42a` |
| resolution ledger SHA-256 | `61f6c05cf1efe59a76e5a693b52b791e9534a30a2aacb06437f0a8d702d74429` |
| deterministic replay | true |

종전 결과 대비 edge 869와 inactive 33은 동일하다. 정규식이 `function_macro`로 버린 76건만 excluded에서
unresolved로 이동해 excluded `11,126 -> 11,050`, unresolved `85 -> 161`이 됐다. 숫자를 늘리거나 새로운
target을 만든 변화가 아니다. 증명되지 않은 제외를 canonical ID별 미해결로 되돌린 것이다.

최종 실행 원장은 `_runs/framework/analyzer-macro-consumer-20260821-r1.json`이다.

## 5. 봉인 직접 의미 판정

Parser가 결과 전에 고정한 봉인 합집합을 Analyzer typed fact와 대조했다. call 526/526과 macro 34/34의
canonical ID, exact range/payload/presence가 모두 일치했다.

유일한 함수형 declaration은 fact
`bc5446c0cb047dee5a8f7ed1ef83f6a981e767660ef6ed7eb4914b8d3d803a48`이며
`source/zordmfax00001.c`의 `#ifdef PFM_DBG` 안에서 `#undef PFM_DBG` 뒤 1,739자 variadic 재정의를 한다.
원문 검색 78개 중 declaration 1개와 replacement 내부 `mpfm_dbg` 1개를 제외한 실제 `PFM_DBG(...)` call은
76개다. classifier의 macro unresolved 76행은 모두 이 source/name/fact ID에 exact-once 결합되고
`insufficient_conditional_macro_definition`이다. build configuration이 없으므로 이 76개가 실제 expansion인지
function call인지 한쪽으로 봉인 판정하지 않은 것이 정확한 결론이다.

## 6. 완료와 미완료

- 완료: C-055 Analyzer consumer, source regex 제거, strict macro payload 검증·운반, 76건 과잉 제외 제거.
- 미완료: call 시점 effective preprocessing state evidence. Parser가 ordered define/undef/include/build-config
  closure를 증명하기 전에는 definitive macro exclusion을 승인하지 않는다.
- 미완료: wrapper literal/symbol, DBMS effective CodeNode owner, Neo4j/API/MCP actual projection.
- 별도 pending: C-061 include/conditional 문자열 재파싱, C-062 type-name regex prepass, C-063 UCN/dialect
  preprocessing identifier identity.

따라서 이 슬라이스를 C 전처리 전체 해결이나 Analyzer E2E 정확도 합격으로 확대하지 않는다.
