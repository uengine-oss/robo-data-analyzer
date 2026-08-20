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

## 5. 분류와 구현 전 계약

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

## 6. 아직 미검증

- 428개 각각의 declaration/include/usage provenance exact ledger.
- usage-only 416개 중 실제 외부 type과 ordinary identifier의 전수 분할.
- regex 환경 제거 전후 parse tree/diagnostic/recovery/call/owner population 차이.
- grammar-owned type environment의 C scope shadowing, conditional include, duplicate translation-unit 반례.
- Analyzer가 explicit type ambiguity를 어느 owner missing-context와 generation quality에 연결할지.

이 항목을 고정하기 전에는 C-062를 완료로 표시하거나 Parser actual 정확도 향상을 주장하지 않는다.
