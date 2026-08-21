# Parser C type-name syntax ownership audit — 2026-08-21

## 1. 판정 범위

- 결함 ID: C-062.
- 조사 기준: Parser `00fdf2a7c4ed7749110a61c6311396089e10c449`, Analyzer
  `2b003706b30ea1f41d88922bfccbad8e2498096c`.
- Architect는 조회·수정·stage·실행하지 않았다. 이 조사에서 file write는 이 원장과 현재 상태 갱신뿐이며
  DB write는 0회다.
- 이 문서는 구현 승인이 아니라 source→C grammar semantic predicate→AST/evidence→Analyzer 경계의 실제
  배선과 rollback 조건을 먼저 고정한다.

## 2. 현재 생산자와 소비자

```text
all project .c/.h decoded source
-> CLanguageModule.collectTypeNamesFromSource
   -> declaration regex 5개
   -> usage-shape regex 5개
   -> keyword/common-member 수동 제외
-> project-global collectedTypeNames
-> CParser.registerTypeNames(predefined=true)
-> IsTypedefName / IsCast / IsTypeSpecifier semantic predicates
-> C parse tree / CAstListener AST / call and conditional evidence
-> EvidenceIrSealer parseStatus + completeness
-> Analyzer source/AST closure -> graph/prompt/validator/Neo4j/API/MCP
```

이 집합은 분석 후 display나 검색 후보가 아니다. 동일 `Identifier` token을 declaration/type/cast로 볼지
expression으로 볼지 grammar가 선택하기 전에 주입되므로 AST 모집단과 call owner/range를 바꾸는 syntax
authority다.

## 3. 유입 commit과 실제 값

- source-wide declaration regex 계열: `99fdc5f`.
- usage-shape 추론: `4633f5f`.
- `COMMON_MEMBER_NAMES` 수동 오탐 제외: `c68f31e`.
- 현재 Parser actual test log: APAS 23 C source/header 0에서 project-global type 428개.
- 같은 23파일에 현 패턴을 독립 재적용한 1차 회계:

| 모집단 | 수 |
|---|---:|
| declaration-pattern 후보 | 11 |
| usage-pattern 후보 | 421 |
| declaration에 없는 usage-only 후보 | 416 |
| 합집합 | 427 |

428 대 427의 한 후보 차이는 Java와 독립 PowerShell/.NET regex 실행 차이로 아직 분해하지 않았으므로
동일하다고 주장하지 않는다. 그러나 416 usage-only라는 주 결론에는 영향이 없다. actual source에는 include
450회/고유 header 284개가 있지만 workspace header와 compilation command/toolchain trace가 모두 없다.
따라서 usage-only 이름을 authoritative typedef라고 증명할 입력 closure가 없다.

추가 역추적에서 같은 syntax authority에 두 번째 무근거 seed가 있음을 확인했다.

- `CParserBase`가 생성하는 `SymbolTable`은 C grammar keyword 외에 `size_t`/`FILE`/pthread/POSIX,
  `HANDLE`/`DWORD`/`WINAPI` 같은 Windows 이름과 `NULL`까지 source/header/build evidence 없이 모든
  translation unit의 predefined type/function specifier로 등록한다
  (`src/main/java/legacymodernizer/parser/antlr/c/SymbolTable.java:10-184`).
- 이 목록은 최초 C grammar 유입 `ec8a04f`부터 존재한다. `NULL`은 C syntax type이 아니라 보통 header가
  제공하는 macro이고, platform typedef도 해당 header와 target/toolchain configuration에서만 보인다.
- 따라서 C-062의 원인은 프로젝트 regex 하나가 아니라 **grammar predicate 앞에 주입되는 모든
  evidence-free name seed**다. regex만 제거하고 platform list를 남기면 같은 false-type 경로가 계속된다.

## 4. 확정 원인과 반례

현재 방식은 C 문법이 요구하는 scope/translation-unit/include 순서 대신 전체 project 파일의 문자열 모양을
한 집합으로 합친다. 그 결과 다음 반례를 원리상 구별할 수 없다.

1. 다른 translation unit에만 local typedef가 있는 동일 이름.
2. multiline block comment 안의 가짜 typedef. 현재 comment 제거 regex는 DOTALL이 아니다.
3. `struct S { ... } instance;`의 ordinary declarator를 `CLOSING_TYPEDEF`가 type으로 등록하는 경우.
4. `foo * bar;`가 declaration인지 multiplication expression인지 필요한 symbol environment 없이 usage 모양으로
   먼저 결정되는 경우.
5. 실제 typedef 이름이 수동 `COMMON_MEMBER_NAMES`에 들어 있어 버려지는 경우.
6. include 순서, conditional branch, `#undef`/macro-expanded include, compiler predefined type 차이.

패턴 수를 늘리거나 제외 목록을 보강하는 방식은 이 반례군을 닫지 못한다.

7. header 없이 `HANDLE *value;` 또는 `size_t value;`가 있는 translation unit을 현재 parser가
   platform/header provenance 없이 exact declaration으로 승인하는 경우.
8. `NULL *value;`를 type declaration으로 승인하는 경우. macro/object identifier와 type name을 같은
   predefined set으로 합친 직접 반례다.

## 5. 1차 근거 대조와 채택 경계

- WG14 working draft N2674 §6.2.1은 ordinary identifier/typedef의 scope가 declaration 위치와
  translation unit/block/prototype 경계에서 시작·종료하며 inner declaration이 outer entity를 숨긴다고
  규정한다. 프로젝트 전체 이름 합집합은 이 scope/order를 표현할 수 없다.
  - <https://www.open-std.org/JTC1/SC22/WG14/www/docs/n2674.pdf>
- WG14 C translation model(5.1.1.1/5.1.1.2)에서 translation unit은 source와 재귀 include를
  configuration에 따라 전처리한 뒤의 token stream이다. include search는 implementation-defined이고
  macro/conditional processing 뒤 syntax/semantic analysis가 일어난다. raw `.c` 파일들의 문자열 합집합은
  translation unit이 아니다.
  - <https://www.open-std.org/jtc1/sc22/wg14/www/docs/n1570.pdf>
- Clang 공식 JSON Compilation Database 계약은 한 source에도 서로 다른 compile action이 여러 개일 수
  있고 AST 도구가 translation unit을 파싱하려면 build system의 전체 compile invocation이 필요하다고
  명시한다. 현재 Parser의 action별 identity를 합치지 않는 C-064 계약과 일치한다.
  - <https://clang.llvm.org/docs/JSONCompilationDatabase.html>
- Clang frontend 공식 internals는 preprocessor가 include stack·macro spelling/expansion 위치·language
  options를 소유하고, lexer는 typedef와 variable을 구별하지 않으며 scope 정보로 parser가 결정한다고
  명시한다. 이것은 Analyzer regex나 project-global preseed가 아니라 configured token stream + parser
  scope가 owner여야 한다는 독립 근거다.
  - <https://clang.llvm.org/docs/InternalsManual.html#the-lexer-and-preprocessor-library>
- ANTLR grammars-v4의 현행 C grammar도 `typedefName`을 semantic predicate에 맡기므로 ANTLR 사용 자체가
  project-global guess를 정당화하지 않는다. local pinned grammar/base는 채택 전 formal fixture와 actual
  delta로 별도 검증한다.
  - <https://raw.githubusercontent.com/antlr/grammars-v4/master/c/CParser.g4>

채택 경계는 다음과 같다.

1. language grammar keyword는 grammar token/rule이 소유한다. header/platform/library 이름을 builtin으로
   하드코딩하지 않는다.
2. 현재 translation unit 안의 typedef는 grammar action이 declaration 완료 시점, lexical scope/order에
   따라 등록하며 listener/sealer가 exact definition/lookup evidence를 발행한다.
3. include/toolchain에서 들어온 type은 C-064의 configuration-specific authoritative trace가 제공한
   definition과 source mapping만 seed할 수 있다. compile action이 다르면 환경과 AST도 분리한다.
4. 환경이 없는데 type-name 여부가 parse 선택을 좌우하면 ordinary identifier로 확정하지 않고 exact
   coordinate를 가진 unresolved syntax decision으로 보존한다. 영향 owner만 unavailable로 격리할 수 있는
   closure 전에는 전체 AST를 `exact`로 승격하지 않는다.
5. ANTLR은 유지한다. 제거 대상은 grammar/listener가 아니라 grammar 앞에서 truth를 발명하는 regex와
   evidence-free platform seed다. 다른 compiler frontend는 authoritative configured trace provider 후보이지
   모든 toolchain의 단일 정답으로 하드코딩하지 않는다.
6. 사용자가 일부 파일만 빈번히 업로드하는 것은 정상 시나리오다. 함수 call이나 `struct` tag처럼 grammar
   role이 확정된 사용은 corpus definition 유무에 따라 내부 target 또는 제품 external로 결정론 분류한다.
   typedef 여부가 declaration/expression을 바꾸는 경우는 target 분류가 아니라 Parser AST JSON의 exact
   parse/evidence 진단으로 남긴다. `Unresolved`/`external-unbound`/가짜 external type 도메인 객체나 추측
   relation은 만들지 않는다.
7. 다른 translation unit의 함수 정의는 header가 없어도 현재 unit의 직접 function declaration과 external
   linkage로 연결될 수 있다. 반면 다른 `.c`의 typedef는 현재 translation unit에 자동 유입되지 않는다.
   WG14 N2674 §6.2.1/§6.2.2와 N843 §6.9의 translation-unit/external-definition 계약을 이 두 경우의
   분리 근거로 사용한다.

## 6. 분류와 구현 전 계약

- 분류: **실제 syntax/semantic truth 오염 가능성 + completeness 과대 주장**.
- 삭제만 하는 변경은 승인하지 않는다. 현재 416 usage-only 후보 중 실제 외부 header type도 많을 가능성이
  높아, 삭제 후 parser recovery/AST omission이 늘면 다른 방향의 의미 손실이 된다.
- 근본 owner는 language frontend의 grammar/listener와 configuration-specific include/build environment다.
- source에 실제 선언된 type은 grammar declaration과 lexical scope/order로만 등록한다.
- include에서 유입되는 type은 resolved include instance + build configuration + source declaration evidence로
  결박한다. project-global basename/usage inference는 금지한다.
- build/header가 없으면 type을 발명하지 않고 ambiguous occurrence와 missing evidence를 explicit unresolved로
  회계한다. 무엇을 보존할지 없는 상태에서 parse를 `exact`로 봉인하지 않는다.
- Parser producer와 Analyzer consumer는 독립 commit으로 나눠 각각 revert 가능해야 한다.

## 7. 구현 전 formal red

다음 반례를 source 이름에 의존하지 않는 계약 테스트로 먼저 고정한다.

1. 다른 translation unit의 local typedef가 include 없이 현재 file에 유입되지 않는다.
2. multiline comment의 가짜 typedef와 ordinary `} instance;`가 type seed가 되지 않는다.
3. header 없는 platform/POSIX 이름과 `NULL`은 source/config evidence 없이 type으로 승인되지 않는다.
4. 같은 file의 앞선 typedef는 declaration 완료 이후 같은 lexical scope에서만 type으로 해소된다.
5. inner ordinary declaration이 outer typedef를 가리는 C scope 반례를 보존한다.
6. 미해소 type-dependent parse choice는 range/decision ID/reason을 exact-once 발행하고 symbol completeness와
   parse trust가 조용한 `exact`가 되지 않는다.

고정 파일은 Parser
`src/test/java/legacymodernizer/parser/contract/CTypeNameEnvironmentContractTest.java`다.
구현 전 실행:

```text
.\mvnw.cmd -Dtest=CTypeNameEnvironmentContractTest test
Tests run: 4, Failures: 4, Errors: 0, Skipped: 0
```

- cross-TU fixture는 기대 IR `1.2.0` 대신 현재 `1.1.0`을 받았다.
- platform/NULL 및 missing-type fixture는 기대 lookup 1건마다 actual symbol fact 0건이었다.
- local typedef + inner ordinary shadow fixture는 기대 definition 2건 대신 actual 0건이었다.
- 실행 log에서 project-global preseed는 test source tree의 다른 fixture까지 합쳐 2→3→7개로 변했고,
  `size_t`, `HANDLE`, `ScopedAlias_4c82`, `CrossTuOnly_7f31`, comment/ordinary-shape 이름이 실제로 등록됐다.

따라서 red는 단순 version 기대 실패가 아니라 현재 producer가 source/config provenance와 lookup ledger를
발행하지 않으면서 test 실행 순서·다른 translation unit에 따라 grammar truth를 바꾸는 원래 결함을 재현한다.

## 8. Parser 구현 중간 검증

현재 working tree 구현은 project-global 10개 regex와 `SymbolTable`의 evidence-free platform/header/`NULL`
seed를 제거했다. C grammar keyword는 lexer token으로만 판정하고, translation-unit 내부 declarator·parameter·
enumerator의 definition과 semantic-predicate lookup을 IR `1.2.0` symbol fact로 발행한다. definition은 identifier
range, lexical `scopeRange`, full declarator 직후 `visibilityStartOffset`을 가진다.

focused test 변화:

```text
구현 전: Tests run: 4, Failures: 4
1차 구현: Tests run: 4, Failures: 2
scope listener 보강: Tests run: 4, Failures: 1
predicate ledger 판정 후: Tests run: 4, Failures: 0
scope/order 반례 추가 후: Tests run: 8, Failures: 0
```

마지막 1건의 실제 좌표는 platform fixture의 `size_t count_29ac;`였다. 기대한 unknown type token
`HANDLE`/`size_t`/`NULL` 3개 외에 `count_29ac`도 lookup fact였다. 전체 AST를 열어 확인한 결과
`parseStatus=partial`이고 `count_29ac`은 `parserDecision=ordinary_identifier`,
`resolutionStatus=unresolved`, predicate context=`typedef_name`이다. 이는 type으로 승인된 사실이 아니라
첫 unknown 때문에 ANTLR recovery가 다른 source token에 실제로 수행한 후속 semantic-predicate decision이다.
이를 숫자 3에 맞춰 삭제하지 않고 서로 다른 token decision으로 exact-once 보존하도록 계약을 정정했다.

추가 green 반례는 declaration point 전/후, function parameter의 body shadow, prototype parameter scope 종료,
inner enum ordinary identifier의 block-only shadow, grammar type specifier 뒤 일반 declarator, grammar가 확정한
call target을 포함한다. 최신 focused 결과는 10/10, 관련 evidence/golden 계약은 23/23, Parser 전체는
195 tests(failure 0, error 0, skipped 10)다. 그러나 이 자동 회귀는 아래 actual AST 회귀를 잡지 못했으므로
producer 승인 근거가 아니다. 이 진단의 Neo4j/API/MCP projection은 요구사항이 아니며 만들지 않는다.
Architect·Catalog·Fabric 수정과 DB write는 0회다.

사용자 정정으로 Analyzer 후단 계약도 단순화했다. Parser `unresolved_environment`와 symbol fact는 AST JSON의
좌표·parse/completeness 진단 정본으로만 유지한다. Analyzer는 IR을 strict validation하고 syntax-dependent
AST/owner가 닫히지 않으면 좌표가 있는 run finding으로 격리하거나 fail-closed한다. 이를 graph node/label/
relationship, 기존 owner의 symbol-binding property ledger, API/MCP 객체로 복제하지 않는다. LLM도 AST,
symbol/type/linkage/CALL/REFERENCE를 보정하지 않고 승인된 별도 natural-language semantic frame만 채운다.

추가 사용자 정정: C의 external linkage와 Analyzer 제품의 external target을 같은 말로 합치면 안 된다.
`a.c`의 global function definition과 `a.h` 또는 직접 declaration을 소비하는 `b.c`가 모두 corpus에 있고
linkage/type이 compatible하면 `b.c → a.c`는 내부 사용자 정의 CALL이다. header는 선언 전달 수단이지 필수
파일 형식이 아니다. `static`은 translation-unit 내부에 한정되고 typedef는 linkage 자체가 없으므로 이 규칙을
적용하지 않는다. 현재 Analyzer의 cross-TU call binder가 이 전체 조건을 실제로 소유하는지는 아직 코드·actual
대조 전이므로 C-067 audit 후보로 분리한다. 이름만 같은 project definition을 결합하는 방식은 승인하지 않는다.

## 9. actual APAS 반증 — 현재 삭제-only 구현은 미승인

같은 APAS 23개 C source를 기존 accepted Parser export와 현재 working-tree export로 다시 파싱했다.
IR `evidence` envelope을 제외한 AST 본문을 비교한 결과 **23/23 파일이 변경**됐고 구조 동일 파일은 0개였다.
전체 node delta는 다음과 같다.

| type | accepted | working tree | delta |
|---|---:|---:|---:|
| FUNCTION | 416 | 883 | +467 |
| FUNCTION_CALL | 12,113 | 11,680 | -433 |
| ASSIGNMENT | 2,038 | 1,941 | -97 |
| IF | 1,922 | 1,833 | -89 |
| ELSE | 763 | 712 | -51 |
| RETURN | 1,444 | 1,383 | -61 |
| TYPEDEF | 0 | 4 | +4 |

가장 큰 단일 반례는 `zapamcom10200.c`다. accepted AST node 929개가 working tree에서 271개가 됐다.
source line 162의
`static long a300_init_validation(zapamcom10200_io_t *itf);` 같은 prototype에서 configured header type을
모르자 Parser가 원래 function name 대신 parameter `itf`를 `FUNCTION`으로 만들었다. 그 뒤 실제 function
`zapamcom10200` 및 여러 body node가 사라졌다. 이 결과는 “추측 type을 제거했다”는 원칙만으로 승인할 수
없는 반대 방향의 구조 손실이다.

actual IR 두 replay의 canonical symbol ledger는 동일했다.

- source 23, definition 1,339, lookup 4,610, resolved 1,718, unresolved 2,892
- parse status: partial 22, exact 1; diagnostics 1,938; recoveries 5,414
- symbol ledger SHA-256:
  `0cd281b4e534bc99cb7fc8ac590e6d1b00df0a46e1f251ca1c047dad4b2315d0`
- report r1/r2 SHA-256:
  `01988fea0e653745d7ab91d5a2c9889ecccbd28e9a8bb2d3cd6d4c6f2883c8ae` /
  `9cda6639461d1495ae55fd72b370a2429a5f9ea31557d33d52254b2099350c1b`

봉인 40건은 현재 정렬 규칙상 전부 unresolved로 뽑혀 층화 직접 판정이 아니다. source를 직접 읽은 결과
실제 missing external type 후보와 평범한 assignment/member/call target에 대한 ANTLR 내부 negative predicate
질의가 섞여 있었다. 따라서 2,892건을 모두 “미제공 type”으로 해석하지 않는다.

폐기한 시도도 분리한다. `parseStatus=exact`이면 unresolved lookup을 전부 버리는 filter는 formal 반례에서
진짜 `MissingType *value`, declaration-point-before 결손까지 삭제해 3건을 실패시켰다. blanket filter는
재사용하지 않는다. grammar-owned call callee range와 정확히 결합한 내부 predicate 한 건만 제거한 현재
국소 수정은 10/10 green이지만, declaration-only context 회복과 block-item 진짜 ambiguity 분리가 없어서
actual 승인에는 불충분하다.

다음 red는 이름이나 suffix가 아니라 grammar context로 고정한다.

1. parameter declaration과 file-scope prototype처럼 declaration만 가능한 위치는 unknown type 좌표를
   unresolved 진단으로 남기되 function/declarator/body AST owner를 바꾸거나 뒤 source를 연쇄 손실하지 않는다.
2. `target(1);`, `object.member = value;`처럼 grammar가 expression을 확정한 위치는 type-name 결손으로 세지 않는다.
3. block의 `T *x;`처럼 declaration/multiplication 양쪽이 가능한 구문은 어느 한쪽을 exact truth로 발명하지
   않고 exact ambiguity 진단을 남긴다.
4. ambiguity 뒤의 독립 statement/control/return은 source-order로 계속 파싱돼야 한다.

ANTLR은 유지한다. 현행 grammars-v4도 `blockItem`을 `IsStatement/IsDeclaration`과 `typedefName` semantic
predicate로 고른다. Tree-sitter C는 동일 충돌을 GLR conflict로 명시하지만, 여러 parse 중 하나를 dynamic
precedence로 최종 선택하므로 그 자체를 authoritative replacement로 채택할 근거는 아직 없다. 대안 parser는
독립 반증 도구 후보일 뿐이며, 현재 채택 방향은 ANTLR grammar context와 exact ambiguity evidence다.

## 10. 아직 미검증

- 428개 각각의 declaration/include/usage provenance exact ledger.
- usage-only 416개 중 실제 외부 type과 ordinary identifier의 전수 분할.
- 위 actual delta의 각 changed coordinate가 declaration-only recovery, true block ambiguity, 다른 grammar
  recovery 중 무엇인지 전수 분할.
- grammar-owned type environment의 C scope shadowing, conditional include, duplicate translation-unit 반례.
- Analyzer IR 1.2 consumer가 parse/symbol ambiguity를 graph mutation 전에 어떻게 fail-closed 또는 run finding으로
  격리할지. Neo4j/API/MCP symbol 진단 projection은 비범위다.

이 항목을 고정하기 전에는 C-062를 완료로 표시하거나 Parser actual 정확도 향상을 주장하지 않는다.

## 11. grammar-owned 구조 보존 구현과 APAS 재검증

삭제-only 구현을 승인하지 않고, 이름 목록이 아니라 ANTLR 문법 대안 자체를 비교하는 formal 반례를
추가했다. 최종 `CTypeNameEnvironmentContractTest` 18건은 다음 경계를 함께 고정한다.

- parameter/member/file-scope declaration처럼 declaration 대안만 완주하는 unknown identifier는 exact
  좌표의 `type_name + unresolved_environment`로 남기고 함수 owner/body를 보존한다.
- block item은 `declaration`과 `statement`, cast 경계는 type-name cast와 ordinary expression을 같은 token
  stream에서 독립 파싱한다. 한쪽만 완주하거나 더 긴 source를 소비할 때만 그 구조를 선택하고, 동일하면
  ordinary truth로 확정하지 않은 ambiguity 진단을 남긴다.
- speculative parse에서 문제의 identifier 한 좌표만 type-name 후보로 바꾼다. 초기 구현처럼 뒤의 모든
  unknown identifier를 type으로 강제하면 함수 body의 ordinary call까지 오염되어 두 대안이 모두 실패한다.
- 복제하지 않은 `CommonToken` 객체를 speculative stream에 재사용하면 token index가 원본 stream에서도
  바뀌어 실제 parse 좌표와 구조가 손상된다. 각 token을 `new CommonToken(token)`으로 복제한 stream만 쓴다.
- assignment/member access/call callee처럼 문법이 expression role을 확정한 negative predicate 평가는 symbol
  결손 모집단에서 제거한다. 반환형 생략 구형 C 함수 반례는 function declarator 이름을 type으로 만들지 않는다.

이 과정에서 기존 platform fixture 해석도 정정했다. `size_t count_29ac;`에서 미해소 type 후보는
`size_t`이고, `count_29ac`은 grammar가 만든 ordinary declarator definition이다. 이를 두 번째 unresolved
lookup으로 세던 recovery 결과는 승인된 truth가 아니며 최신 구현에서는 definition으로 보존된다.

검증 결과:

```text
focused CTypeNameEnvironmentContractTest: 18/18
focused + SemanticEvidenceIrContractTest: 30/30
Parser full Maven: 203 tests, failures 0, errors 0, skipped 10
```

actual APAS 중간 반증과 최종 결과는 다음과 같다.

| run | FUNCTION | FUNCTION_CALL | ASSIGNMENT | IF | ELSE | RETURN | diagnostics/recovery |
|---|---:|---:|---:|---:|---:|---:|---:|
| 최초 삭제-only | 883 | 11,680 | 1,941 | 1,833 | 712 | 1,383 | 1,938 / 5,414 |
| block/parameter 보강 r3 | 406 | 11,699 | 1,954 | 1,846 | 717 | 1,402 | 잔여 cast 연쇄 손실 |
| cast 보강 r4 | 416 | 12,113 | 2,038 | 1,922 | 763 | 1,444 | 1 / 2 |
| 단일-candidate external 보강 r5 | 416 | 12,113 | 2,038 | 1,922 | 763 | 1,444 | 0 / 0 |
| accepted compatibility AST | 416 | 12,113 | 2,038 | 1,922 | 763 | 1,444 | — |

r4의 잔여 한 좌표는 `FILE *` 반환형이 `null *`로 바뀐 함수였다. external declaration 보조 parse가
현재 identifier뿐 아니라 뒤 body의 unknown call까지 type으로 취급해 type/ordinary 양쪽을 모두 거부한 것이
원인이었다. candidate 좌표 하나만 바꾸자 반환형·signature·body가 모두 복구되고 r5의 parser diagnostic과
ANTLR recovery가 0/0이 됐다.

r5 보고서는 `_runs/framework/parser-c-type-name-evidence-20260821-r5.json`, Analyzer fixture는
`_runs/framework/parser-c-type-name-fixture-20260821-r4`다. actual symbol 회계는 4,659 = definition 2,624 +
lookup 2,035, lookup은 resolved 755 + unresolved 1,280이고 symbol ledger SHA-256은
`5d8b1f5ec3b77a01e705cc5b73e4be80cecf116a46027e2b945e0734d002b793`다.

accepted export와 IR envelope을 제외한 AST `children` 전수 비교는 20/23 byte-equivalent다. 다른 3파일의
유일한 node delta는 source line과 직접 대조한 실제 `typedef` 4개다.

- `apascrtu0010t02.c:172` 1개
- `zapamcom10011.c:41` 1개
- `zordmfax00001.c:169,203` 2개

기존 의미 node category의 누락·추가 delta는 모두 0이다. 이 시점에는 symbol fact 4,659개의 층화 봉인 직접
판정과 Analyzer IR 1.2 strict consumer가 미검증이었고, 후속 결과는 §12~13에 분리해 기록한다.

## 12. 봉인 symbol 40건 직접 판정

선정은 결과 열람 전에 `SemanticEvidenceActualCorpusTest.stratifiedSymbolSelection()`으로 고정했다. 각
stratum 목표 8건을 canonical fact ID 순으로 먼저 뽑고, 모집단이 8보다 작은 typedef definition의 잔여 2건은
같은 고정 rank/fact ID 순서의 unresolved로 채웠다. r6의 최종 구성은 unresolved 10, source-declaration
resolved lookup 8, grammar-context lookup 8, typedef definition 6, ordinary definition 8이다.

판정 전에 r6의 exact source line과 payload를 40건 모두 열었다. unresolved 10건의 고유 type 이름 6개는
해당 translation unit 전체에서 definition-shaped occurrence가 0임을 별도로 확인했다. resolved lookup 8건은
payload의 `definitionFactId`를 같은 envelope definition으로 역조인해 definition line이 lookup보다 앞서고
7건 ordinary/1건 typedef decision이 source와 일치함을 확인했다. grammar-context 8건은 모두 builtin `char`
뒤 ordinary declarator, definition 14건은 모두 실제 typedef/declarator/parameter declaration이다.

| # | canonical fact ID | source coordinate | stratum | 직접 기대 | 판정 |
|---:|---|---|---|---|---|
| 1 | `001b84a7afc570d7acb0fd27a0ba8009fce9922652961b24be5050f9a4517b12` | `source/zapamcom10080.c:1644` | unresolved | declaration-only type, same-TU definition 없음 | exact |
| 2 | `012e07af9b9e6f9fe79f4d229265e0fb8ab2a8584ba99c9d5716fbb2699912b2` | `source/zapamcom10100.c:1857` | unresolved | declaration-only type, same-TU definition 없음 | exact |
| 3 | `01328acf8f5af2a6d1dd0929cd6673c57c49801b2255447c211fae86038d17f9` | `source/zapamcom10020.c:3681` | unresolved | declaration-only type, same-TU definition 없음 | exact |
| 4 | `015cdf4edf23bf73262bc2390842c1b895b261de14b219a49d0597d27b11cbe7` | `source/zapamcom10090.c:229` | unresolved | declaration-only type, same-TU definition 없음 | exact |
| 5 | `0171bd25d240ca4604785aafdc063aeae9a1d0532196d1da0a30c3dde4784863` | `source/zapamcom10200.c:1550` | unresolved | declaration-only type, same-TU definition 없음 | exact |
| 6 | `0175572d22764133e2cf6b31caa539aa5ee8ff170134065ebc5bd0a45b38635d` | `source/zapamcom10020.c:335` | unresolved | declaration-only type, same-TU definition 없음 | exact |
| 7 | `01b7781c166f940be42a6f84f7ce5c5b3de191392e97c0874ff758299b3c278e` | `source/zapamcom10020.c:340` | unresolved | declaration-only type, same-TU definition 없음 | exact |
| 8 | `02005cdfa471ac4a804d3306939cb21eba46dfe21f29c1c1a64d7019612ad861` | `source/zapamcom10090.c:4089` | unresolved | declaration-only type, same-TU definition 없음 | exact |
| 9 | `004dbe3be9d0fefc323e267358695098c25421a78731beefadd28996809a2179` | `source/zapamcom10090.c:343` | source-declaration lookup | ordinary definition line 204 | exact |
| 10 | `019e08fe9e7ff64fc54f9d4c7eb24fd356b06d3f8487addd934f88ac1b66210b` | `source/zapamcom10200.c:1168` | source-declaration lookup | ordinary definition line 838 | exact |
| 11 | `01a1898e07c15f4e1c704bc3577a4f696d421ab051319c7a736c9e667cb80233` | `source/zordmfax00001.c:2105` | source-declaration lookup | ordinary definition line 2098 | exact |
| 12 | `02b9c1c45e6d7afaa43c59cc127d6d2713404efa629b075d767e5bea3e206279` | `source/zapamcom10020.c:2634` | source-declaration lookup | ordinary definition line 231 | exact |
| 13 | `02e73bd642bf72b7a4841a6d69445f8c67d8f31d08fcba4858094c5d7f512203` | `source/zapamcom10020.c:1416` | source-declaration lookup | ordinary definition line 231 | exact |
| 14 | `031e0e59003a4383ff437499310c297ca982618be032678fd99be95e37f18302` | `source/apascrtu0010t02.c:187` | source-declaration lookup | typedef definition line 172 | exact |
| 15 | `032b5be37d16fc567e62c3214869f567a2589bb1fb18d0fbb94ff2eb417430c2` | `source/zngmmcap50000.c:1223` | source-declaration lookup | ordinary definition line 941 | exact |
| 16 | `035469591c798edddf50cf450ef711d3da033328856b07f237db3b8ceaa81722` | `source/zapamcom10020.c:1434` | source-declaration lookup | ordinary definition line 231 | exact |
| 17 | `04f4b8b013eb4108145d166f08e328857067849a8c57bb4739d5e0e4c1124784` | `source/apascrtu0010t02.c:167` | grammar-context lookup | `char` 뒤 ordinary declarator | exact |
| 18 | `0c28c481c0ca61e10214a0f19c5f6bc2b18e6a81581a2b9ff22b63f9de6a43c7` | `source/apascrtu0010t02.c:164` | grammar-context lookup | `char` 뒤 ordinary declarator | exact |
| 19 | `18ba4acf625b4ebe59535920def8f8054d2f2bbbc34be5b3536eb68fd079eb5e` | `source/apascrtu0010t02.c:124` | grammar-context lookup | `char` 뒤 ordinary declarator | exact |
| 20 | `1f5f1a9f87e9042dcbb9be35b3f4ce6e1a200bce5dbbc1ffd79fbf2507740d21` | `source/zordmfax00001.c:216` | grammar-context lookup | `char` 뒤 ordinary declarator | exact |
| 21 | `1fbf5dbafe68ed8cfa4fa68095b5dbccea82ab1ecab4c756680dc3b64d5edd22` | `source/zordmfax00001.c:207` | grammar-context lookup | `char` 뒤 ordinary declarator | exact |
| 22 | `209bd18c0a9ca1cf6a89ac51013cec6dc34768cf76cdc33a1edc8768a601edb4` | `source/zordmfax00001.c:228` | grammar-context lookup | `char` 뒤 ordinary declarator | exact |
| 23 | `20d01252574bd4fb49cd6af10a36af96d998f99adecba448174a08abc0e62099` | `source/zapamcom10020.c:226` | grammar-context lookup | `char` 뒤 ordinary declarator | exact |
| 24 | `22dc7d9422a3be8bc8d2741360b7645697a38d8e6c746262dbfa9614b280e93c` | `source/zordmfax00001.c:219` | grammar-context lookup | `char` 뒤 ordinary declarator | exact |
| 25 | `11b5ba46de7cebd707c10c6eb0f6a4fad6ea1c8587366cfa3e01ab0f1c20d508` | `source/apascrtu0010t02.c:113` | typedef definition | explicit typedef declarator | exact |
| 26 | `2990fb51fe8c5a6c75fc3b464ebf2f73f4fd3a3f9b1624a99e02718da4694ab9` | `source/apascrtu0010t02.c:172` | typedef definition | explicit typedef declarator | exact |
| 27 | `54846cb9ff0a645c8e3e7e992800ef7b3dc6c555f0ca0ad7c5809303b3cfb337` | `source/zordmfax00001.c:203` | typedef definition | explicit typedef declarator | exact |
| 28 | `63bfe1edc17ad505ab11e11119a8ef7a16d4827a148d6980026223168354e03a` | `source/zapamcom10011.c:41` | typedef definition | explicit typedef declarator | exact |
| 29 | `952a5b568b1f14210a295c2c737453a706d456d876e3ebdb9475c81db9fa6119` | `source/zapamcom10020.c:227` | typedef definition | explicit typedef declarator | exact |
| 30 | `bd31ac632fbf60cdb86c41032caf177cfe21e71ef4c07f610abadbddbab387ff` | `source/zordmfax00001.c:169` | typedef definition | explicit typedef declarator | exact |
| 31 | `0010f2b5b0bce895be89aa291a4f5f722634a5036fd5951b5ca4094da82264bb` | `source/zordmutl00010.c:1772` | ordinary definition | local `long` declarator | exact |
| 32 | `0038984335717b77a0240ea357185a3e478b73f6b598363d624b47aaf67fe2c5` | `source/apascrtu0010t02.c:2305` | ordinary definition | local typed declarator | exact |
| 33 | `00888af1dcd4ce414b78da01a8f29bddc578f04e120557a0b95f7482a78c9060` | `source/zapamcom10010.c:374` | ordinary definition | function parameter | exact |
| 34 | `00939485782b21519ebe9c6d0f009e26a8ab1de9be63bc0f4ef466c335e75088` | `source/zapamcom10020.c:4663` | ordinary definition | local `long` declarator | exact |
| 35 | `00bf86d84faebec6dc7232d131adfc88f18ee804230b410d791b053715055189` | `source/zapamcom10140.c:2170` | ordinary definition | local typed declarator | exact |
| 36 | `00cb76fb00e33163fd70247fe2f4789e132502b5ad2c40d9b7679821cae704e6` | `source/zapamcom10150.c:133` | ordinary definition | local `long` declarator | exact |
| 37 | `00eda27eb5ca14b4d982efb2c9ed89690da3317bb25aaf1f4f5806ea894fb6aa` | `source/apascrtu0010t02.c:3120` | ordinary definition | function parameter | exact |
| 38 | `00fd9d144412a3048ee69f5ee45724fbfb6d944eed8666aa86781f577e04bd78` | `source/zapamcom10200.c:1531` | ordinary definition | local `char` declarator | exact |
| 39 | `020e363afffa73b43c5a9626094cff35e092be454bdebb0571b7fab7af0ea9f2` | `source/zapamcom10020.c:4884` | unresolved | declaration-only type, same-TU definition 없음 | exact |
| 40 | `0216e1b081cb7c5fadf8fa4e3fc7205fb7a35f0e3f76d9d291141978e48b6872` | `source/zapamcom10020.c:304` | unresolved | declaration-only type, same-TU definition 없음 | exact |

직접 판정 결과는 exact 40/40, partial/contradiction/fabrication/omission 0이다. 이 결과는 봉인 symbol
표본 40건에만 유효하다. 전체 4,659 symbol fact의 의미 정확도, configured preprocessing이 있는 C 환경,
다른 언어, Analyzer/Neo4j/API/MCP 정확도로 일반화하지 않는다. raw report는
`_runs/framework/parser-c-type-name-evidence-20260821-r7.json`이고 SHA-256은
`a292453955faf02c4e041fe8c722075e52cfd1569d9367bff2b8f0321b00fa05`다. r7의 상설 accepted-AST 비교는
23/23을 실행했고 `TYPEDEF`를 제외한 `children` delta 0, 직접 확인한 typedef delta +4다.

## 13. Analyzer IR 1.2 strict consumer와 비투영 경계

Parser producer는 `e28be6aa026ff005b5d4adb447459409cdecf34f`로 `origin/main`에 push됐다. Analyzer
consumer의 구현 전 formal red는 IR 1.2 자체를 거부해 5/5 실패했고, actual r6 fixture의 첫 실행에서는
기존 import occurrence 모델이 `evidence_version=1.2.0`을 거부했다. 이는 symbol 계약 추가와 무관한 import
450건을 envelope version 때문에 잃는 downstream 호환 결함이었다.

Analyzer 제품·테스트 commit은 `cd7110d3e7103ff264d6ce041136ab01e96c412f`다.

Analyzer는 다음을 graph mutation 전에 strict validation한다.

- canonical fact ID/range/grammar/presence와 definition/lookup exact payload
- definition scope·visibility와 source-definition lookup의 definition ID/decision 일치
- nonempty unique predicate context, grammar-context ordinary declarator 결정
- configured lookup의 authoritative emitted preprocessing trace ID
- completeness의 unresolved fact ID source-order 전수 집합과 partial reason
- unresolved ordinary 또는 `ambiguous_*` syntax decision의 좌표별 fail-closed

구조가 닫힌 declaration-only unresolved lookup은 AST JSON parse/completeness 진단으로만 검증되고
`NodeBuildResult`, CodeNode extra, Neo4j node/label/relationship/property, API/MCP 객체로 복제되지 않는다.
actual APAS의 unresolved context는 `declaration_only_context=694`, `declaration_only_parse=584`,
`cast_only_parse=1`, `external_declaration_only_parse=1`이고 syntax ambiguity는 0이다.

검증 결과는 다음과 같다.

```text
formal IR 1.2 red: 5 failed
strict 반대 계약 추가 red: 5 failed, 3 passed
focused green: 107 passed, 2 subtests passed
actual opt-in: 1 passed
Analyzer authoritative unittest: 1,141 tests, failures 0, skipped 6
Analyzer full pytest: 1,258 passed, 7 skipped, 367 subtests passed
prompt byte identity: 16/16
```

actual report는
`_runs/framework/analyzer-c-type-name-consumer-20260821-r2.json`, SHA-256은
`e22050b73d773559e50dbc40748b1ae2d1e6e462a08fe10357c61eb46fbee1f2`다. 23 source에서 symbol
4,659 = definition 2,624 + lookup 2,035(resolved 755/unresolved 1,280), duplicate 0, Analyzer ledger
SHA-256은 Parser와 같은 `5d8b1f5ec3b77a01e705cc5b73e4be80cecf116a46027e2b945e0734d002b793`다.
봉인 symbol 40건은 Analyzer가 소비한 source ID/range/grammar/payload/source slice와 40/40 exact하다.

이 슬라이스는 type-name parse/evidence 경계만 승인한다. configured preprocessing trace가 실제로 공급되는
C corpus, 다른 언어, C cross-translation-unit declaration/linkage/type binder(C-067), 전체 의미 LLM/Neo4j
재분석 정확도는 미검증이다. 검증 중 Neo4j write와 Architect/Catalog/Fabric 수정은 0회다.
