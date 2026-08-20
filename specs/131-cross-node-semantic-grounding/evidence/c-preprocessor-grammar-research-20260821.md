# C preprocessor grammar 조사 저장 — 2026-08-21

**Scope**: C-055 — Analyzer function-macro source regex 제거

## 1. 현재 원인

Parser의 pinned `CLexer.g4`는 `MultiLineMacro|Directive`가 지시문 전체를 hidden token 하나로 만들고,
`CParser.g4`는 preprocessing-file/group/control-line/replacement-list를 “No ANTLR4 rule”로 비워 둔다.
따라서 `CAstListener`와 Analyzer `_FUNCTION_MACRO_RE`가 각각 directive 문자열을 다시 정규식으로 해석했다.
Parser가 코드를 먼저 읽는다는 사실만으로 syntax owner가 생기는 것은 아니다.

유입 계열은 C grammar 최초 `ec8a04f`, `CAstListener` directive 후처리, Analyzer `1aef9a30` call 분류다.
직전 Analyzer actual은 function-like macro call occurrence 76건을 제외했지만 이름별 정본 fact와 exact source
range가 없어 분류를 독립 검증할 수 없었다.

## 2. 1차 근거

- WG14 C23 working draft N3096 §6.10은 preprocessing-file/control-line과 object-like
  `# define identifier replacement-list`, function-like `# define identifier lparen ... ) replacement-list`를
  별도 문법으로 정의한다. `lparen`은 앞에 whitespace가 없는 `(`다.
  https://www.open-std.org/jtc1/sc22/wg14/www/docs/n3096.pdf
- WG14 DR 324는 translation phase 2에서 backslash-newline을 제거하고 phase 3에서 comment를 한 칸의
  공백으로 치환하되 newline은 보존한다고 재확인한다.
  https://www.open-std.org/jtc1/sc22/wg14/www/docs/dr_324.htm
- ANTLR 공식 lexer 문서는 mode/channel, Unicode property, code-point tokenization 계약을 설명한다.
  https://github.com/antlr/antlr4/blob/dev/doc/lexer-rules.md
- ANTLR 공식 parser 문서는 parser rule context/listener event와 start rule의 EOF 소비 계약을 설명한다.
  https://github.com/antlr/antlr4/blob/dev/doc/parser-rules.md

외부 grammar를 정답으로 그대로 복사하지 않는다. 표준 문법을 최소 owner 계약으로 옮기고 standard/GNU
반례와 actual corpus로 반증한다. 기존 regex 결과도 정답지로 사용하지 않는다.

## 3. 결정과 rollback 경계

main C grammar를 즉시 교체하지 않고, 본문 parse tree byte 계약을 유지하면서 별도 pinned
`CPreprocessorLexer.g4` + `CPreprocessorParser.g4`가 full decoded source를 parse한다. 전용 listener가
object/function macro와 name/parameter/replacement exact range를 common evidence IR `macro` fact로 발행한다.

translation phase 2 line splice는 logical parse 전에 제거하되 logical↔physical Unicode-code-point mapping을
보존한다. block comment는 lexer mode로 처리해 내부 newline을 유지한다. semantic token 자체가 splice로
물리적으로 비연속이면 contiguous range를 발명하지 않고 해당 directive를 explicit unresolved로 회계한다.
malformed/unclosed directive도 `population = emitted + explicitlyUnresolved`와 reason으로 닫는다.

Analyzer는 검증된 function-like macro fact set만 소비한다. C source를 다시 읽거나 `#define` regex를
fallback으로 사용하지 않는다. 다만 declaration fact를 call 시점 expansion 증명으로 과대 해석하지 않는다.
`#undef`/재정의/include/build predefined macro까지 ordered closure가 없으면 explicit unresolved다. 이 추가
생산자 의무는 후속 actual 반증에서 C-064로 분리했다. DBMS/다른 언어는 지원하지 않는 kind를
`unsupported|not_applicable`로 유지한다.

Parser producer는 독립 커밋으로 되돌릴 수 있고 main C grammar/generated classes에는 손대지 않는다. 다음
Analyzer consumer 커밋도 별도이므로 outcome 변화가 틀리면 consumer만 되돌릴 수 있다.

## 4. formal red

`SemanticEvidenceIrContractTest.cMacroFactsComeFromPreprocessorGrammarNotSourceRegex`는 object, 빈 함수형,
다중 parameter, variadic, line continuation, inactive conditional macro 6종을 고정했다. 최초 Parser 결과는
`expected 6, actual 0`이었다. 구현 도중 inactive macro가 active로 승격되는 presence gap도 별도 red로 드러났다.
최종 검증과 actual 회계는 `parser-c-macro-evidence-validation-20260821.md`가 소유한다.
