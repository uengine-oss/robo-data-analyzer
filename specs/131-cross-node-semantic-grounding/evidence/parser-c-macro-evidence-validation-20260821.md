# Parser C macro evidence 검증 원장 — 2026-08-21

## 1. 책임 경계와 red

C-055의 Parser 생산자 범위만 검증했다. Analyzer의 `_FUNCTION_MACRO_RE` 제거와 call outcome 변화는 다음
consumer 커밋의 책임이며 여기서 완료로 주장하지 않는다.

formal red `SemanticEvidenceIrContractTest.cMacroFactsComeFromPreprocessorGrammarNotSourceRegex`는 object,
빈 함수형, 다중 parameter, variadic, line splice, inactive conditional 정의 6건을 요구했고 최초 결과는
`expected 6, actual 0`이었다. 구현 중에는 조건부 branch 안의 directive 토큰 자체가 presence span의 빈틈에
놓여 inactive macro가 active로 승격되는 C-060도 `expected inactive, actual active`로 재현했다.

## 2. 구현 계약

- pinned `CPreprocessorLexer.g4` + `CPreprocessorParser.g4`가 full decoded source의 전처리 control-line을
  소유한다. main C parse tree는 바꾸지 않는다.
- translation phase 2의 `\\` + LF/CRLF/CR splice를 제거한 logical view와 physical Unicode-code-point
  half-open range mapping을 함께 유지한다.
- lexer mode가 block comment를 공백으로 취급하면서 내부 newline을 보존한다. 종료되지 않은 block comment,
  malformed `#define`, semantic token 자체가 splice로 비연속인 경우는 fact를 꾸미지 않고 explicit unresolved다.
- object/function-like 구분은 macro name 바로 뒤의 whitespace 없는 `(`라는 WG14 문법으로 한다. comment
  separation, GNU named variadic, 표준 variadic, empty replacement, C digraph `%:`도 계약에 포함했다.
- macro fact는 `nameRange`, `macroKind`, `terminalName`, ordered `parameterRanges`, `variadic`, nullable
  `replacementRange`만 발행한다. source text는 envelope에 한 번만 있고 payload는 range로 참조한다.
- 기존 `CAstListener`의 `#define` 정규식은 제거했다. 구형 uppercase object `DEFINE` node는 grammar fact에서만
  투영하며 함수형 macro 판단에는 사용하지 않는다.
- macro completeness는 `population = emitted + explicitlyUnresolved`를 지키며, C 이외 frontend는 계속
  `unsupported`다.

근거는 WG14 N3096 §6.10, WG14 DR 324의 translation phase 2/3 설명, ANTLR 공식 lexer mode/parser rule
계약이다. 외부 grammar를 정답으로 복사하지 않았고 합성·actual corpus에서 별도 반증했다.

## 3. 자동 회귀

- Parser 전체: 48 suites, 170 tests, failures 0, errors 0, skipped 10.
- 추가 edge 계약: object/function whitespace, comment separation, LF/CRLF/CR splice, retained newline in block
  comment, unterminated comment, malformed name/parameter list, GNU variadic, digraph, inactive conditional.
- C golden은 macro evidence가 지원되는 frontend라는 계약 때문에 zero-population status만
  `unsupported -> complete`로 갱신했다.
- pinned grammar SHA-256와 catalog SHA-256를 갱신하고 `LanguageCatalogValidationTest`로 대조했다.

## 4. actual 결정론 전수 회계

APAS C/H 23파일을 같은 격리 workspace에서 파일별 두 번 parse했고 AST/evidence byte replay가 모두
동일했다.

| 항목 | 값 |
|---|---:|
| source files | 23 |
| call facts | 12,113 |
| macro facts | 183 |
| object / function | 182 / 1 |
| active / inactive / conditional·unknown macro | 182 / 0 / 1 |
| explicitly unresolved macro | 0 |
| macro ID duplicates | 0 |
| macro fact ledger SHA-256 | `b1f51f924272511d019adc6ff3bf831aa1445c66e113da1742fc278ee41ae42a` |
| all-fact ledger SHA-256 | `0c90779d267b087dcc8a95d2f5e4f1173d19a74f319085db3005998a6299887e` |

최종 구현의 재검증 원장은 `_runs/framework/parser-macro-evidence-20260821-r3.json`, Analyzer 전달 fixture는
`_runs/framework/analyzer-macro-consumer-fixture-20260821-r2/`다. 실행 중 LLM·Neo4j read/write는 0회다.

## 5. 봉인 직접 의미 판정

결과를 보기 전에 source별 first/last macro, function-like, variadic, non-active, multiline, empty replacement의
합집합을 선택하는 규칙을 코드에 고정했다. actual 합집합은 canonical ID 기준 34건이다.

exact range에서 파생한 directive/name/parameters/replacement를 원문과 직접 대조했다. 34/34 모두 실제
macro definition이고 object/function 구분, terminal name, ordered parameters, replacement boundary가 일치했다.
유일한 함수형 macro `PFM_DBG(...)`는 1,739자 다중행 definition이며 parameter 0 + variadic, `#ifdef
PFM_DBG` 아래 conditional presence까지 일치했다. 표시용 `displayPreview`는 권위 payload가 아니고 exact
range에서 파생한 봉인 판독물이다.

## 6. 완료와 후속 상태

- 완료: C-055 Parser macro producer, C-060 directive presence gap.
- 후속 완료: Analyzer macro consumer와 `_FUNCTION_MACRO_RE` 제거, outcome ledger actual 변화는
  `analyzer-c-macro-consumer-validation-20260821.md`에서 별도 승인했다.
- 미완료(C-064): declaration fact만으로는 call 시점 effective preprocessing state를 증명하지 못한다.
  ordered define/undef/include/build configuration closure의 Parser evidence가 필요하다.
- 새로 분리: C-061 C include/conditional directive 문자열 재파싱, C-062 project typedef/type-name source regex
  prepass, C-063 UCN·언어 dialect를 포함한 canonical preprocessing identifier identity. 이들은 현재 macro
  fact actual의 ASCII identity를 뒤집지 않지만 범용 owner 원칙 위반이므로 숨기지 않고 후속 책임으로 남긴다.
