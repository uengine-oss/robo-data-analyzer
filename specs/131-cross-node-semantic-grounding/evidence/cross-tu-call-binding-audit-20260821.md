# C-067 call binding 생산자·소비자 감사

**Status**: Parser IR 1.3 producer implemented and actual-accounted; Analyzer consumer pending
**Date**: 2026-08-21
**Scope**: Parser call/declaration evidence → Analyzer CALL 분류 → Neo4j → REST/MCP/search. Architect 제외.

## 1. 결론

현재 canonical CALL target은 언어 symbol binding이 아니라 **이름 목록**으로 결정된다.
`call_classifier.py`는 모든 non-statement CodeNode를 `name -> ids`로 모은 뒤, 이름이 하나면 그
정의를 무조건 고르고 여러 개면 같은 파일의 정렬상 첫 항목, 그것도 없으면 전체 정렬상 첫 항목을
고른다. 이 결정에는 call-site에서 보이는 선언, C linkage, compatible function type, Java receiver/
overload, Python lexical/dynamic binding이 없다.

이 결과는 preview가 아니다. 내부 `CALLS`가 되어 callee context, semantic CALL evidence,
bottom-up 분석 순서, Neo4j 관계, 외부 stub 생성 여부, 검색 graph distance, REST/MCP occurrence에
전달된다. 따라서 C-067은 실제 의미 target 오귀속 가능성이 있는 canonical 손실 경로다.

## 2. source → 최종 소비자 배선

| 관문 | 현재 생산/변환 | 근거 |
|---|---|---|
| Parser C definition | FUNCTION AST에 returnType/modifiers/parameters를 기록 | Parser `CAstListener.java:208-246` |
| Parser C declaration | file-scope function prototype을 grammar로 감지한 뒤 즉시 버림 | Parser `CAstListener.java:340-363` |
| Parser Java/Python | call range/terminal은 생산하지만 resolved declaration/definition ID는 생산하지 않음 | `CallEvidenceCandidate.java`; Java listener `388-440`; Python listener `591-624` |
| Analyzer AST load | signature/returnType/parameters는 CodeNode로 옮기지만 modifiers와 declaration identity는 버림 | `node_builders.py:103-124`; `graph_nodes.py:210-253` |
| Analyzer binder | `by_name[name]`, `module_of[id]`만 구축 | `call_classifier.py:121-130` |
| target 선택 | 단일 이름은 무조건 선택; 중복은 같은 file/정렬상 첫 ID | `call_classifier.py:430-446` |
| origin 제외 | 확장자별 stdlib name set과 scan-agent glob name으로 occurrence를 제외 | `call_classifier.py:309-320` |
| persist/stream | CALLS와 canonical occurrence ledger를 같은 step에서 저장·발신 | `link_calls_by_name_step.py:45-95` |
| semantic context | CALLS를 caller/callee 집합으로 집계 | `build_context_step.py:188,241-245,311-312` |
| LLM input/order | callee를 deterministic CALL fact와 summary dependency로 사용 | `semantic_frame_planner.py:239-252`; `analysis_order.py:65-93` |
| Neo4j | internal은 기존 node를 MATCH; external은 FUNCTION:EXTERNAL stub 생성 | `graph/edges/calls.py:27-68` |
| generation | occurrence identities/count/hash/outcome을 publish closure에 포함 | `graph/generation.py:299-470` |
| REST/MCP/search | generation summary, owner occurrence, CALLS distance/neighbor를 그대로 소비 | `search/corpus/node_detail.py:120-183`; `search/ranking/graph_access.py`; `search/service.py:38`; `api/mcp_app.py:91-139`; `api/search_router.py:36-79` |

추측으로 메운 고리는 0개다. Parser가 현재 내지 않는 declaration/linkage/type/binding 증거는 실제
부재로 확인했다. 실제 provider/LLM은 이 target 결정 전에 호출되지 않는다.

## 3. actual APAS 값

정본 fixture:
`_runs/framework/parser-c-type-name-fixture-20260821-r6`

| 항목 | 전수 값 |
|---|---:|
| C source | 23 |
| FUNCTION definitions | 416 |
| `static` definitions | 380 |
| non-static definitions | 36 |
| canonical call occurrences | 12,113 |
| duplicate definition names | 12 names / 54 rows |
| raw name match: same file | 545 occurrences |
| raw name match: unique other-TU non-static definition | 95 occurrences |
| raw name match: corpus definition name 없음 | 11,473 occurrences |

현재 C-064 blanket preprocessing gate를 그대로 통과시키면 `12,113 = unresolved_preprocessing
12,113`, CALLS=0이다. 이 gate만 read-only counterfactual로 해제해 기존 name binder를 관찰하면:

| 기존 분류 | 건수 |
|---|---:|
| stdlib name excluded | 6,918 |
| framework-runtime glob excluded | 4,132 |
| internal edge | 637 |
| external edge | 232 |
| wrapper target evidence 부족 | 85 |
| conditional macro state 부족 | 76 |
| inactive preserved | 33 |

internal 637은 same-file 543, cross-file non-static 94다. cross-file 94는 unique definition 3개에
51/34/9로 집중된다. 일부 caller source는 대응 header include를 명시하지만 업로드 corpus에 header가
없고, Parser AST에는 visible prototype이 없다. 따라서 이 94건은 실제 프로그램에서는 내부일 가능성이
있어도 **현재 입력으로 선언 가시성/type compatibility가 증명된 내부 CALL은 0건**이다. 반대로 무조건
external이라고 단정하는 것도 실제 build truth 주장은 아니다. 부분 업로드 source-view 제품 계약에서는
증명되지 않은 target을 corpus 내부로 결합하지 않고 external 경계와 occurrence reason으로 남겨야 한다.

## 4. 원인 꼬리표와 유입

1. 증상: cross-file internal CALL 94건이 declaration/type evidence 없이 생성된다.
2. 직접 변환: `_resolve_internal()`이 이름 하나면 무조건 definition ID를 반환한다
   (`call_classifier.py:440-446`).
3. 입력 축소: CodeNode index가 이름과 file owner만 보존한다
   (`call_classifier.py:121-130`).
4. upstream 부재: C prototype은 listener에서 감지 후 폐기되고, definition `modifiers=static`은 Analyzer
   CodeNode 변환에서 폐기된다.
5. 최초 입력: raw C source에는 definition, call, include 문장이 있지만 uploaded header/build trace는 없다.

유입 계열:

- name-only binder: Analyzer `6956d02c` (2026-05-26)
- same-file 우선 설명/동작: Analyzer `17922c37`
- statement target filter: Analyzer `bc699ee2`
- C prototype discard: Parser `c68f31ea` (2026-07-22)
- Analyzer CodeNode field boundary: `1aef9a30`

## 5. 표준·공식 구현 근거와 한계

- WG14 N1570 §6.2.2는 external linkage 선언이 translation unit들 사이에서 같은 function/object를
  가리키고, file-scope `static`은 internal linkage라고 규정한다. 함수 선언에 storage-class가 없으면
  `extern`과 같이 linkage를 정한다. §6.7/§6.7.6.3은 같은 entity 선언의 compatible type과 function type
  compatibility를 요구한다. §6.9는 사용된 external identifier의 external definition 유일성을 요구한다.
  <https://www.open-std.org/jtc1/sc22/wg14/www/docs/n1570.pdf>
- Clang compilation database 정본은 각 command가 translation unit의 실제 compile action이며 같은 file도
  configuration별 command가 여러 개일 수 있다고 명시한다. 이는 configuration identity를 file name으로
  합치면 안 된다는 근거다. <https://clang.llvm.org/docs/JSONCompilationDatabase.html>
- Clang AST의 `FunctionDecl`은 canonical redeclaration, declaration/definition, external linkage를 별도
  구조로 소유한다. 이는 특정 compiler 채택 명령이 아니라 normalized evidence에 필요한 정보축의 독립
  교차검증이다. <https://clang.llvm.org/doxygen/Decl_8h_source.html>
- WG14 N1570 §6.2.5·§6.2.7·§6.7.6.3은 compatible type을 source token equality로 정의하지 않는다.
  function parameter의 array/function type adjustment와 compatibility 판정 시 top-level qualifier 제거도
  명시한다. Clang `ASTContext`도 typedef/typeof 같은 decorated spelling을 제거한 canonical structural type과
  canonical parameter type을 별도 제공한다. 이 근거는 raw token hash를 exact compatibility로 부를 수 없다는
  반증이며, Clang을 유일 provider로 강제하는 근거는 아니다.
  <https://www.open-std.org/jtc1/sc22/wg14/www/docs/n1570.pdf>,
  <https://clang.llvm.org/doxygen/ASTContext_8h_source.html>
- ANTLR 공식 listener 문서는 grammar rule별 enter/exit callback으로 parse tree 구문을 추출한다고 설명한다.
  따라서 prototype/definition/call의 exact range 생산자는 Analyzer 정규식이 아니라 각 언어 listener/
  adapter여야 한다. <https://github.com/antlr/antlr4/blob/dev/doc/listeners.md>
- Java Language Specification §15.12는 method invocation이 receiver에서 검색할 type을 정한 뒤 접근성,
  argument applicability, boxing/varargs, generic inference와 most-specific overload를 거쳐 compile-time
  declaration을 선택하고 runtime dispatch를 별도로 처리한다고 규정한다. 따라서 name/arity binder는
  Java binding의 대체물이 아니다. <https://docs.oracle.com/javase/specs/jls/se24/html/jls-15.html#jls-15.12>
- Python Language Reference §4.2는 name binding이 parameter/function/class/assignment/import 등에서 생기고,
  nearest enclosing scope와 `global|nonlocal`, block 전체 binding set에 따라 해소된다고 규정한다. free name
  resolution은 runtime에 일어날 수도 있으므로 대문자/initializer regex는 binding 증거가 아니다.
  <https://docs.python.org/3/reference/executionmodel.html#naming-and-binding>

공식 자료를 그대로 제품 선택으로 승격하지 않는다. Clang은 available configured frontend provider 후보일
뿐 모든 C toolchain의 무조건적 truth가 아니다. ANTLR은 syntax owner로 유지한다.

## 6. 분류와 새 손실 경로

| ID | 판정 | 현재 영향 |
|---|---|---|
| C-067 | 실제 semantic target loss | 모든 언어 공통 name-only internal binder가 wrong-target/ambiguous target을 canonical CALLS로 만들 수 있음 |
| C-068 | 과도한 degraded gate | C source에 configured preprocessing이 없다는 이유만으로 active/inactive/grammar-known call 12,113건 전부를 같은 unresolved로 덮음 |
| C-069 | 검색 파생 제한이 canonical 분류로 침투 | stdlib JSON 112/384/103 names와 scan-agent glob이 origin proof 없이 canonical CALL edge를 제외 |
| C-070 | Parser name-convention heuristic | Python 대문자 시작을 constructor/NEW_INSTANCE로 보고 초기화식 regex로 variable type을 추측 (`PythonAstListener.java:620`, `ParserUtils.java:420-489`) |
| C-071 | Parser declaration/definition loss | C prototype 폐기와 header function definition 무조건 무시; supplied header의 inline/static definition도 손실 가능 |
| C-072 | Java binding identity loss | method call range는 있으나 receiver/overload/canonical declaration target이 없고 interface declaration과 implementation을 같은 METHOD population에 둠 |
| C-073 | 거짓 type compatibility proof | 서로 다른 translation unit의 같은 typedef/tag 철자를 같은 semantic type으로 봉인하면 이름-only 결합을 type-token-only 결합으로 바꿀 뿐이다 |
| C-074 | inline definition configuration loss | non-static inline definition의 external visibility는 C99/GNU dialect·declaration·attribute에 따라 달라지므로 raw source token만으로 corpus target을 확정할 수 없다 |
| C-075 | 토큰열을 canonical C type으로 과대 선언 | 파라미터 이름만 뺀 grammar token sequence는 `signed int`/`int`, parameter top-level qualifier 제거, array/function parameter adjustment 같은 WG14 compatibility 규칙을 표현하지 못한다. 다른 key를 incompatible로 해석하면 compatible definition을 product external로 오분류한다 |
| C-076 | conditional callable visibility 손실 | inactive/conditional region의 prototype·definition도 unconditional callable과 같이 visible 후보에 들어가 active call을 `declaration_bound`로 과대 확정한다 |

C-068의 해결은 C-064 evidence를 삭제하는 것이 아니다. configured trace는 configuration-specific active set/
macro expansion의 authoritative 축으로 유지하되, source-view에서 grammar-known occurrence와 target binding을
별도 상태로 분리해야 한다. C-069의 scanner는 pattern discovery로 유지할 수 있지만 canonical edge 삭제
권위로 사용하지 않는다.

## 7. 고정한 목표 책임 경계

1. 각 언어 Parser adapter가 exact declaration/definition/call syntax, lexical scope, language binding key,
   linkage/visibility/type-compatibility capability와 configuration provenance를 versioned evidence로 생산한다.
2. Analyzer는 language/extension/name 규칙을 실행하지 않고 canonical fact ID와 adapter-owned binding key/
   outcome을 strict 검증해 exact join한다.
3. proven unique corpus definition만 internal CALLS가 된다. grammar-known named call에 proven internal target이
   없으면 제품 `external`이며, 이유와 후보 evidence는 canonical call occurrence에 남긴다.
4. expression/dynamic/configuration ambiguity는 occurrence diagnostic이다. `Unresolved` node/label/relation/
   owner property/API entity를 만들지 않는다.
5. external target node도 adapter가 collision-free identity를 증명할 때만 만든다. 그렇지 않으면 occurrence는
   external이지만 aggregate CALLS target을 발명하지 않는다.
6. stdlib/framework origin은 provider/adapter evidence가 있을 때만 derived facet로 쓰며 call occurrence truth를
   삭제하지 않는다.
7. Framework semantic owner는 routine, DBMS owner는 effective AST CodeNode 계약을 그대로 유지한다.

wire 정본은 `contracts/common-evidence-ir-v1.md`의 IR 1.3 절이다. type-name `symbol` fact를 재사용하지
않고 `callable` declaration/definition fact와 각 call payload의 binding 판정을 분리한다. Analyzer는
`(adapter schema, configuration, target scope, opaque binding key, opaque compatibility key)`의 equality만
검사한다. target 0개는 external, 1개만 internal, 2개 이상은 ambiguous occurrence이며 첫 후보 선택은 없다.
Java/Python adapter가 아직 증명하지 못하는 범위는 같은 wire의 `unsupported|dynamic`으로 남고 name-only
fallback하지 않는다.

## 8. 아직 미검증

| 항목 | 이유 | 다음 증거 |
|---|---|---|
| cross-file 94건의 actual build target | header 284개와 compile command/toolchain trace 없음 | configured authoritative trace 공급 후 declaration→definition 전수 대조 |
| Java overload/dispatch 정확도 | actual Java corpus와 binding producer 없음 | grammar fixture + JLS 기반 adapter contract |
| Python lexical/dynamic binding 정확도 | actual Python corpus와 binding producer 없음 | scope/import/descriptor/dynamic boundary fixture |
| DBMS call owner | common IR call support가 현재 unsupported | effective CodeNode owner 별도 slice |
| Neo4j/API/MCP 변경 후 정확도 | 아직 제품 변경 전 | formal red→green 뒤 framework rollback publish와 three-boundary ledger hash |

## 9. formal red 원본

Parser:

```text
workdir: D:\work\robo\project\antlr-code-parser
.\mvnw.cmd -Dtest=CCallBindingEvidenceContractTest test
Tests run: 6, Failures: 6, Errors: 0, Skipped: 0
```

실패는 IR version `1.2.0`, callable fact 0, call `payload.binding` 부재, header definition callable/AST 부재로
나뉜다. 기존 type-name `symbol` fact가 function identifier를 기록해도 call binding 증거는 아니라는 점도
출력으로 확인했다.

Analyzer 최초 red:

```text
workdir: D:\work\robo\project\robo-data-analyzer
.venv\Scripts\python.exe -m unittest discover -s tests\pipeline\step5_linking \
  -p "test_call_binding_classifier.py" -t .
Ran 6 tests
FAILED (errors=6)
```

6건 모두 `classify_call_sites() got an unexpected keyword argument 'callable_facts'`로 실패했다. 따라서 현
classifier에는 adapter-owned definition 입력 경계가 없고 기존 name index밖에 없다는 원인과 일치한다.

Parser producer 완료 뒤 Analyzer fixture를 final IR 1.3 wire로 다시 고정했다. bound call의
`resolutionMode|compatibilityScope`, callable의 `compatibilityStatus|compatibilityScope|definitionStatus`,
실제로 존재하는 declaration/definition fact reference를 포함하고, compatible/direct, target 0/1/N,
source/configuration 격리, unsupported candidate 보존, missing binding/reference fail-closed를 분리했다.

```text
workdir: D:\work\robo\project\robo-data-analyzer
.venv\Scripts\python.exe -m unittest discover -s tests\pipeline\step5_linking \
  -p "test_call_binding_classifier.py" -t .
Ran 9 tests
FAILED (errors=9)
```

9건 모두 여전히 `classify_call_sites() got an unexpected keyword argument 'callable_facts'`다. red가 제품의
동일 결손에서 실패하면서도 strict consumer 구현 뒤 검증해야 할 최종 Parser wire와 반대 경계를 빠뜨리지
않는 상태다.

## 10. Parser producer 구현·검증

Parser는 C grammar/listener가 prototype·definition·call visibility와 linkage를 생산하고 공통 sealer가
IR `1.3.0` fact ID와 opaque key를 봉인하도록 변경했다. header definition의 확장자 기반 폐기는 제거했다.
member/function-pointer call은 name target이 아니라 `dynamic`, function-like macro occurrence는 해당 macro
fact ID를 가진 `configuration_dependent`, visible declaration이 없는 named call은 product `external`이다.

첫 구현의 토큰-equality compatibility는 C-073/C-075 반례에서 폐기한다. WG14 6.2.5·6.2.7·6.7.6.3은
compatible type이 token equality가 아니며 parameter array/function adjustment와 top-level qualifier 제거를
요구한다. Clang도 decorated spelling이 아니라 canonical structural type과 canonical parameter type을 별도
계산한다. 따라서 현재 source-view capability는 다음처럼 닫는다.

- grammar AST로 canonical form을 완전히 계산한 제한된 builtin scalar function type만 cross-TU
  `compatibilityScope=corpus`로 봉인한다. specifier 동치와 parameter top-level qualifier를 canonicalize한다.
- typedef/tag, unspecified parameter, array/function parameter, 복합 declarator는 raw token spelling을 key로
  쓰지 않고 `unavailable`이다. configured canonical type provider가 생기기 전에는 cross/source-file join을
  발명하지 않는다.
- block-local named type, 지원하지 않는 declarator/ABI extension은 `unavailable`이며 name/arity fallback이 없다.
- non-static inline definition은 configured dialect trace가 없으면 `definitionStatus=configuration_dependent`다.
  `definitionStatus=exact`만 aggregate target 후보다.
- call-site에서 이미 보이는 exact definition fact는 type text 재매칭 없이 그 fact 자체를 direct target으로
  참조한다. declaration-only fact는 canonical compatibility가 exact일 때만 definition population과 join한다.
- inactive callable은 visible 후보가 아니며 conditional/unknown callable이 binding에 영향을 주면 해당 fact ID를
  가진 `configuration_dependent`다.

2026-08-21 추가 formal red는 기존 13건에 builtin 동치 spelling, named-type direct definition,
inactive prototype, conditional prototype을 더하고 기존 typedef/unspecified 기대를 엄격화했다. 구현 전 결과는
17 tests 중 6 failures, error 0이다. 실패 6건은 C-075 네 경계와 C-076 두 경계를 정확히 재현했다.

Formal 계약은 최초 6 red에 member function pointer, named function-pointer object, block prototype visibility,
unspecified parameter, cross-TU typedef spelling collision, block-local type, non-static inline을 더해 최신
`CCallBindingEvidenceContractTest` 13/13 green이다. actual checker에서 fact array를 한 번에 인덱싱하기 전에
앞쪽 call이 뒤쪽 callable을 조회하던 검사기 순서 결함도 발견해 `ID 전수 인덱스 → 관계 검증` 2-pass로
교정했다. 잘못 지정한 baseline root 때문에 난 1회 실패는 제품 실패가 아니며 올바른 accepted
`analysis/source` 경로로 다시 실행했다.

최신 actual 정본은
`_runs/framework/parser-c-call-binding-evidence-20260821-r7.json`
(SHA-256 `d382ba59686ec5c7e50dd55dedffbc394a998ae9069fb9125be80bacc264d434`)이다.

| actual APAS 전수 항목 | 값 |
|---|---:|
| source / diagnostics / recoveries | 23 / 0 / 0 |
| legacy FUNCTION / callable definition | 416 / 416 |
| legacy FUNCTION_CALL / call fact | 12,113 / 12,113 |
| call binding | declaration-bound 545 / external 11,492 / configuration-dependent 76 |
| dynamic / ambiguous / unsupported | 0 / 0 / 0 |
| callable | 796 = declaration 380 + definition 416 |
| target scope | source-file 760 / corpus 36 |
| compatibility scope | source-file 780 / corpus 16 / unavailable 0 |
| declaration-bound target join | 0 target 0 / unique 545 / ambiguous 0 |
| unique join source | same source 545 / cross source 0 |
| accepted AST comparison | 23/23, TYPEDEF delta 0 |

fact ledger는 `261f32928ab18efea81876e91cd49f0be0bdcf122d6287501d8ef165a8679928`,
call-binding ledger는 `7608eda90ca3f0de09c8985fccfc57d5a14f6524e49c69f1dea7e2eaa257ef34`,
callable ledger는 `8c3426251901884d7fdd4db0ff23817eaa0c95534f6d296c2467ef72a28ea8a8`다.
결과 열람 전 봉인된 40개는 macro binding 7, declaration-bound 7, external 7, callable 19로 층화됐고,
주 에이전트가 source slice·fact reference·eligible definition을 직접 대조해 40/40 exact로 판정했다.
특히 bound 7건은 모두 exact definition 1개, external 7건은 visible declaration 0개였다. 이 표본은 C
source-view producer에만 유효하며 Java/Python/DBMS/Analyzer consumer 정확도로 일반화하지 않는다.
Parser 전체 `mvnw test`는 216 tests, failures 0, errors 0, skipped 10이다. Surefire가 Spring test의 native
logging을 dumpstream warning으로 기록했지만 XML 52 suites의 합계와 Maven 최종 `BUILD SUCCESS`는 일치한다.

## 11. commit 전 재반증 — C-075~C-077

C-075/C-076 계약으로 제한 builtin scalar type만 grammar-owned canonical compatibility를 발행하고,
visible exact definition은 `direct_definition`, declaration-only exact type join은 `compatible_definition`으로
분리했다. inactive callable은 후보에서 제외하고 conditional callable은 그 fact ID를 가진
`configuration_dependent`로 남긴다. formal 19/19, 관련 type/symbol/evidence IR 49/49가 green이었다.

교체 후 actual r10(`_runs/framework/parser-c-call-binding-evidence-20260821-r10.json`, SHA-256
`0c613168db0f0c05633bf661dbdd371f5586ed286cf088c86959835d2bc399e4`)의 provisional 전수 값은 다음과 같다.

| actual APAS 전수 항목 | 값 |
|---|---:|
| source / diagnostics / recoveries | 23 / 0 / 0 |
| legacy FUNCTION / callable definition | 416 / 416 |
| legacy FUNCTION_CALL / call fact | 12,113 / 12,113 |
| call binding | declaration-bound 57 / external 11,493 / configuration-dependent 76 / unsupported 487 |
| declaration-bound resolution mode | direct-definition 17 / compatible-definition 40 |
| callable | 796 = declaration 380 + definition 416 |
| target scope | source-file 760 / corpus 36 |
| compatibility | unavailable 790 / corpus exact 6 / source-file exact 0 |
| declaration-bound target join | zero 0 / unique 57 / ambiguous 0 |
| unique join source | same source 57 / cross source 0 |
| accepted AST comparison | 23/23, TYPEDEF delta 0 |

이 수치는 token-equality 과대승격 487건을 정직한 `unsupported`로 바꾼 결과이며, 손실을 숨기려고
name/arity fallback하거나 다른 숫자 cap을 쓰지 않았다. 하지만 직접 판정에서 C-077을 발견했다. 487건은
`reason=insufficient_callable_type_compatibility`를 가지면서도, 바로 그 판정을 만든 visible prototype
callable fact ID가 `candidateFactIds=[]`로 사라졌다. 이는 새 graph entity가 필요한 문제가 아니라 기존
call occurrence→callable fact exact evidence closure의 누락이다.

formal test에 visible block prototype의 fact ID가 candidate로 정확히 한 번 남아야 한다는 assertion을
추가했고 다음 red를 확인했다.

```text
workdir: D:\work\robo\project\antlr-code-parser
.\mvnw.cmd -Dtest=CCallBindingEvidenceContractTest test
Tests run: 19, Failures: 1, Errors: 0, Skipped: 0
expected: <1> but was: <0>
```

따라서 r10과 그 앞의 직접 판정은 provisional evidence로만 보존한다. Parser sealer가 unsupported candidate를
허용하고 C adapter가 visible prototype `nameRange`를 전달한 뒤 r11 actual, 전체 회귀, 결과 열람 전 봉인
40건 직접 판정을 새로 수행해야 이 producer slice를 commit할 수 있다.

C-077 제품 교정은 `unsupported/insufficient_callable_type_compatibility` occurrence에 visible declaration
`nameRange`를 candidate로 전달하고, 공통 record invariant를 status별 candidate 계약으로 분리했다. 동일 focused
명령은 교정 후 `Tests run: 19, Failures: 0, Errors: 0, Skipped: 0`으로 green이다. 이 결과는 국소 자동
회귀만 검증하며 actual r11·전체 회귀·직접 판정을 대신하지 않는다.

actual r11과 검사기 강화 후 독립 r12는 count, fact/call/binding/callable ledger와 봉인 selection이 동일했다.
새 actual invariant는 unsupported 487건 전부에서 candidate ID 중복 0, fact 존재, callable kind, 같은 terminal
name, unavailable compatibility, active presence, call-site visibility를 검사하며 green이다. r11 binding ledger는
`1392557b8e3e327efc3d5d54c29d3214b57ef01e3a788216aa45922c940969b3`, report SHA-256은
`b17d598de4ccf384139deab8a184fa540ce65c1192535a6d74423594b009f2d6`이다.

직접 판정 40건을 열면서 C-078 checker/selection 결함을 발견했다. `declaration_bound`를 한 층으로 묶어
난이도순 선택한 5건이 모두 `direct_definition`이었고, 모집단 40건인 `compatible_definition`은 직접 판정
0건이었다. 자동 계약 통과를 직접 판정으로 잘못 확대하지 않고, selection reason을 resolution mode까지
포함해 두 층으로 다시 봉인한다. 따라서 r11/r12의 40건은 최종 직접 판정 근거가 아니다.

r13은 `compatible_definition` 5, `direct_definition` 4를 포함한 10개 층/40건을 봉인했다. 주 에이전트가
각 call source, visible declaration/definition, candidate fact, eligible definition과 callable signature의
role/static target scope/compatibility 상태를 직접 대조해 40/40 exact로 판정했다. 특히 compatible 표본은
call 앞의 builtin scalar prototype과 같은 source-file의 유일 exact definition을 결합했고, direct 표본은
call 전에 보이는 exact definition fact 자체를 참조했다. external 표본은 same-source visible callable 0이며,
동명 corpus definition이 다른 파일에 있는 표본도 declaration 부재 때문에 external로 유지됐다. r13 report
SHA-256은 `f143f441e00f5d9b4d0c66ae45c650a4d943ecf767d5ea68c9892fa404c353c5`다.

전체 회귀는 `222 tests, failures 0, errors 0, skipped 10`이다. 이후 diff 감사에서 C-079 후보를 발견했다.
C grammar가 허용하는 parenthesized function declarator는 재귀 name token을 callable fact에는 보존하지만,
기존 legacy FUNCTION AST name 생산자는 바깥 direct declarator의 Identifier만 읽는다. callable definition의
`astNodeRange`가 가리키는 CodeNode identity가 달라질 수 있으므로 formal 반례를 먼저 고정했다. 실행은
`20 tests, failures 1, errors 0`이며 `int (wrapped)(int) { ... }`의 callable fact와 direct binding은 exact였지만
같은 range의 legacy FUNCTION `name`만 absent였다. 원인은 function AST와 callable이 서로 다른 declarator
name 추출 경로를 쓴 것이다. 교정은 이미 쓰던 재귀 grammar name token을 두 생산자가 공유하도록 한 줄기로
통합하며, 이름/정규식 특례는 추가하지 않는다.

C-079 교정 후 formal은 20/20 green이다. final actual r14는 r13과 selection, fact ledger, call-binding ledger,
callable ledger가 동일하므로 actual corpus에서 C-079 syntax는 미발동이고 기존 40건 직접 판정도 같은 원문/
fact 집합에 그대로 유효하다. r14 report SHA-256은
`29d7ace44058b9317dfa750baa735625552c0558c6def5c27226ca60233892ad`, two-pass wall은 37,580ms다.
최종 Parser 전체는 `223 tests, failures 0, errors 0, skipped 10`; golden contract는 1/1 green이고
C/Java/Python/Oracle/PostgreSQL generated-resource SHA가 5/5 동일하다. C golden SHA-256은
`a3074fc2297393ca206c0031eadc2dcc5763685089ec0717b1f428f3650f2087`다.

Parser producer 변경은 commit `5dd64106540674a4aebfdb37ad897a375950d335`로 `origin/main`에 push됐고
push 뒤 local HEAD/origin main이 일치하며 working tree는 clean이다. 이 commit은 Parser boundary만 완료한다.
Analyzer IR 1.3 strict consumer, occurrence/generation/Neo4j/REST/MCP는 아직 red/미구현이므로 전체 CALL
정확도나 end-to-end 공개를 의미하지 않는다.
