# Call binding 책임 경계 정정

**Status**: confirmed; Common Evidence IR 1.3 binding authority rejected before Analyzer product consumption
**Date**: 2026-08-21

## 1. 결론

Parser와 Analyzer 두 서비스만 유지한다. 새 서비스는 만들지 않는다.

- Parser의 ANTLR grammar/listener는 source에 실제로 적힌 **구문 사실**과 exact range만 생산한다.
- Analyzer 내부의 versioned language semantic adapter가 lexical/name/type/import/linkage/dispatch 의미를
  판정하고, language-neutral resolution core가 canonical fact ID를 exact-once 병합한다.
- LLM은 AST, callable, binding, internal/external, stdlib/framework 분류를 생성하거나 수정하지 않는다.
- 부분 업로드에서 분석 대상 내부 target을 증명하지 못한 grammar-known call은 제품 `external`이다.
  동적·configuration-dependent·ambiguous·unsupported는 call occurrence 상태이며 별도 unresolved graph
  entity가 아니다.
- Parser가 발행한 AST CodeNode 하나를 callable 이유로 복제하지 않는다. CodeNode의 primary graph role과
  callable/call/effect syntax fact는 서로 다른 facet이다.

Parser commit `5dd64106540674a4aebfdb37ad897a375950d335`의 IR 1.3은 역사·실험 증거로 보존하지만,
Analyzer의 canonical binding authority로 소비하지 않는다. 이미 push한 역사는 변경하지 않고 forward
wire로 교정한다.

## 2. 현재 코드에서 확인한 원인 사슬

| 고리 | 현재 값 | 근거 |
|---|---|---|
| source syntax | C grammar가 call, prototype, definition, declarator를 인식 | `CAstListener.java:540-560,649-682` |
| listener semantic decision | listener가 모든 call에 `bindCall()`을 실행 | `CAstListener.java:72-79` |
| binding outcome | listener가 `dynamic/configuration_dependent/declaration_bound/unsupported/external`을 결정 | `CAstListener.java:562-646` |
| linkage/type decision | listener가 `static`으로 target scope를 만들고 builtin function type을 canonicalize | `CAstListener.java:737-803,823-900` |
| adapter boundary | `CallableEvidenceExtraction`은 자신을 language-adapter-owned라 부르지만 실제 별도 adapter 객체/registry가 없음 | `CallableEvidenceExtraction.java:5-10` |
| serialization | `CLanguageModule`이 listener 결과를 곧바로 sealer에 전달 | `CLanguageModule.java:89-97` |
| sealer | sealer는 callable/binding population, ID 참조, completeness를 봉인할 뿐 의미를 독립 판정하지 않음 | `EvidenceIrSealer.java:175-206,239-268,332-390` |
| Analyzer product | 현재 classifier는 CodeNode name index, same-module 우선, 정렬상 첫 target, stdlib list, framework glob을 사용 | `pipeline/steps/step5_linking/link_calls/call_classifier.py:95-307` |
| Analyzer IR 1.3 attempt | dirty decoder는 Parser binding outcome을 canonical로 받으려 했고 하단에는 삭제한 `_KINDS` 참조가 남아 미완성 | `pipeline/steps/step2_load_ast/evidence_ir.py:104-188,497-810,1680-1802` |

따라서 이전 문서의 “Parser language adapter가 binding을 생산하고 Analyzer는 opaque key equality만 수행”은
현재 코드의 별도 adapter를 설명한 것이 아니라 listener 안의 의미 판정을 adapter라고 이름 붙인 것이다.
사용자가 지적한 “ANTLR은 구문 노드를 파싱하는 역할”과 맞지 않으며, Analyzer consumer를 이 wire에 맞추면
잘못된 책임을 고착한다.

## 3. actual r14가 증명한 것과 증명하지 않은 것

Parser r14의 현재 모집단은 다음과 같다.

| 축 | actual |
|---|---:|
| source / call / callable | 23 / 12,113 / 796 |
| bound | 57 = direct definition 17 + compatible definition 40 |
| non-bound | external 11,493 + configuration-dependent 76 + unsupported 487 |
| bound target uniqueness | 57/57 unique |
| bound source locality | same source 57 / cross source 0 |

이 실행은 source-view listener의 결정론과 같은 파일 안의 제한 builtin type fixture를 증명한다. 실제
cross-translation-unit positive가 0이므로 cross-TU compatibility 정확도, Java overload/dispatch, Python
lexical/runtime binding을 증명하지 않는다. Parser 성공을 Analyzer/Neo4j/API/MCP 성공으로 확대하지 않는다.

## 4. forward wire: structural evidence → semantic resolution

### 4.1 Parser structural evidence

다음 값만 Parser owner다.

- envelope의 explicit `language`와 frontend/grammar schema identity
- call/callee/receiver/argument의 exact half-open range와 grammar kind
- callable declaration/definition role, exact name/range, AST definition range
- lexical scope path/range와 source declaration order
- storage/type/qualifier/function specifier, declarator, parameter, attribute의 **typed syntax component와 range**
- import/include/module/member/alias/wildcard와 conditional presence
- source/hash/range/completeness exact-once closure

Parser는 call payload에 final binding을 싣지 않는다. `bindingKey`, `compatibilityKey`, `targetScope`,
`resolutionMode`, `definitionStatus`, `external/dynamic/bound`는 Parser wire에서 제거한다. Parser는 source
text를 정규식으로 재파싱하지 않고 grammar context가 이미 알고 있는 component를 구조 fact로 보존한다.

### 4.2 Analyzer language semantic adapters

Analyzer에는 새 서비스가 아니라 내부 adapter registry를 둔다.

```text
validated Parser structural IR
  -> adapter registry(explicit language/frontend schema)
     -> C / Java / Python / SQL semantic adapter
        -> optional authoritative compiler/runtime trace provider
        -> source-only proof-safe resolution or explicit limitation
  -> language-neutral resolution core
  -> occurrence ledger -> derived aggregate edges -> generation/Neo4j/API/MCP
```

- C adapter: ordinary identifier scope, declaration point, internal/external linkage, compatible function type,
  compile configuration을 판정한다. compilation database와 actual toolchain trace가 있으면 authoritative로
  사용하고, 없으면 structural facts로 증명 가능한 단위만 확정한다.
- Java adapter: compiler symbol/type trace가 있으면 receiver, accessibility, overload, selected declaration을
  사용한다. name/arity만으로 target을 만들지 않는다.
- Python adapter: lexical binding/global/nonlocal/import/rebinding을 구조 facts 또는 compiler symbol table로
  판정한다. attribute/descriptor/monkey patch 가능성이 남으면 dynamic이다.
- common core: adapter 결과의 canonical occurrence ID, evidence closure, target 0/1/N, source/configuration
  격리, exact-once ledger만 소유한다. language spelling을 해석하지 않는다.

어떤 adapter도 결과를 만들 수 없는 입력은 무한 탐색이나 이름 fallback으로 성공시키지 않는다. 모든 call을
`internal|external|dynamic|configuration_dependent|ambiguous|unsupported|inactive` 중 하나로 회계하고,
근거 fact ID와 missing capability를 남긴다.

## 5. ontology.yaml 판정

`pipeline/ontology_roles.yaml` 전체를 binding engine으로 사용하거나 없애고 Parser에 밀어 넣는 두 선택은
모두 폐기한다.

- 유지: `roles`의 Parser node type → Analyzer primary materialization role, closed stereotype taxonomy,
  scan inventory hint처럼 **분류/발견만 하는 데이터**.
- binding에서 제외: `role_of()`는 `framework_reader._FW_DISPATCH`의 primary handler 하나를 고르는 함수일
  뿐 lexical/type/import/call target 의미를 증명하지 않는다.
- 정리 대상: parser-supported language source를 다시 해석하는 `source_lexicon` 소비는 기존 C-049의
  grammar-fact migration으로 제거한다. name-based call exclusion인 stdlib/framework/external resolver
  heuristic은 기존 C-069과 이 계약에서 evidence/provider 결과로 교체한다.
- `sql:EXIT`는 현재 `control_structure`와 `statement` 두 primary role에 중복되어 `role_of()` 결과가 YAML
  순서에 의존한다. 실제 DBMS reader는 variable/constant 외 모든 node를 같은 CodeNode로 보존하고,
  `exit_loop` effect는 `statement_effect_ops`라는 별도 facet에서 읽으므로 현재 node/effect 손실은 없다.
  primary-role 표에서는 중복을 제거하고 독립 facet은 별도 계약으로 유지한다.

## 6. 공식 근거와 채택 범위

- ANTLR listener는 grammar rule에 대응하는 parse-tree event와 token/phrase structure를 제공한다. listener가
  application code를 grammar 밖에 두는 장점은 있지만, 이것이 corpus-wide type/linkage correctness를
  자동 제공한다는 뜻은 아니다: <https://github.com/antlr/antlr4/blob/dev/doc/listeners.md>
- Clang은 Parser와 Sema를 분리하며 Parser는 Sema와 통신하고 canonical type/name lookup은 semantic
  계층에서 수행한다: <https://clang.llvm.org/docs/InternalsManual.html>
- C compile action은 파일 하나만으로 정해지지 않으며 compilation database는 실제 working directory와
  argv를 보존한다: <https://clang.llvm.org/docs/JSONCompilationDatabase.html>
- Python `ast`는 call/import/name 등 syntax를, `symtable`은 compiler symbol table을 제공한다:
  <https://docs.python.org/3/library/ast.html>, <https://docs.python.org/3/library/symtable.html>
- Java compiler `Trees`는 syntax tree에서 element, scope, type mirror를 조회하는 semantic API다:
  <https://docs.oracle.com/en/java/javase/21/docs/api/jdk.compiler/com/sun/source/util/Trees.html>

이 자료는 특정 compiler를 모든 언어·toolchain의 무조건적 truth로 채택하는 근거가 아니다. 구조와 의미의
책임 분리, authoritative provider가 있는 configuration에서만 그 결과를 승격하는 근거로만 사용한다.

## 7. red와 rollback 경계

구현 전에 다음을 고정한다.

1. Parser wire에 final binding/status/key가 하나라도 있으면 실패한다.
2. Parser callable syntax component의 source range 합집합·순서·ID·presence가 exact여야 한다.
3. Analyzer는 explicit language adapter가 없으면 name/extension/stdlib 목록으로 fallback하지 않는다.
4. direct local definition, visible declaration + unique compatible corpus definition, no declaration external,
   internal linkage isolation, duplicate ambiguity, incomplete type unsupported, dynamic expression을 분리한다.
5. 모든 outcome은 occurrence exact-once ledger에 남고 external/unavailable 때문에 별도 unresolved graph
   entity를 만들지 않는다.
6. Parser IR 1.3 commit과 r14 evidence는 삭제하지 않는다. forward wire가 실패하면 새 commit만 되돌릴 수
   있어야 하며 origin history를 rewrite하지 않는다.

## 8. 미검증

| 항목 | 이유 | 다음 증거 |
|---|---|---|
| C cross-TU positive | actual APAS bound 57건이 전부 same source | 별도 sealed cross-TU corpus와 Analyzer adapter 실제 실행 |
| Java overload/dispatch | Parser/Analyzer structural+semantic adapter 미구현 | javac Trees 기반 fixture와 runtime-dispatch 분리 회계 |
| Python lexical/dynamic | Parser/Analyzer structural+semantic adapter 미구현 | nested/global/nonlocal/import/*/rebind fixture 전수 |
| Neo4j/API/MCP forward wire | 계약 정정 전이며 제품 구현 전 | framework/dbms rollback publish와 동일 occurrence ledger hash |
| ontology primary-role 중복 제거 | 현재 영향이 없음을 코드로 확인했지만 제품 변경 전 | role uniqueness test와 전체 regression |
