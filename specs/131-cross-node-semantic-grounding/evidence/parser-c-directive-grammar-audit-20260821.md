# Parser C directive grammar ownership audit — 2026-08-21

## 1. 범위와 현재 배선

결함 C-061은 macro 이외 C preprocessing directive를 grammar가 아니라 문자열로 재해석하는 두 경로다.

```text
decoded source
├─ main CLexer hidden Directive/MultiLineMacro token
│  ├─ CAstListener.extractIncludes
│  │  └─ trim + startsWith("#include"|"# include") + replaceFirst regex -> INCLUDE AST
│  └─ CConditionalCompilationAnalyzer
│     └─ Directive.parse + custom expression Lexer/parser -> presence/conditional_region
└─ pinned CPreprocessorLexer/Parser
   └─ defineLine만 grammar-owned macro evidence, include/conditional은 otherLine으로 폐기
```

Analyzer는 legacy INCLUDE AST를 import scope/reference 후보에 사용하고 conditional presence는 call fact,
Rule/Example active source, generation completeness에 연결된다. 따라서 두 문자열 재파싱은 preview가 아니라
canonical AST/evidence 생산자다.

## 2. actual 모집단과 유입

APAS 23 C source/header 0의 물리 directive 모집단은 다음과 같다.

| directive | 수 |
|---|---:|
| include | 450 |
| define | 183 |
| if | 16 |
| ifdef | 1 |
| else | 2 |
| endif | 17 |
| undef | 1 |

actual 표기는 모두 current happy path에 가까워 기존 include 450와 conditional group 17이 생산됐다. 이것은
다른 표준 표기가 안전하다는 근거가 아니다. main C lexer의 `Directive: '#' ~[\n]*`와 listener 문자열
후처리 원형은 C grammar 최초 계열이고, 별도 preprocessing grammar는 Parser `23b3d43`에서 macro에만
도입됐다.

## 3. 근거와 결정

기존 `c-preprocessor-grammar-research-20260821.md`가 고정한 WG14 N3096 §6.10 preprocessing-file/
control-line 문법, WG14 DR 324 translation phase 2, ANTLR lexer/parser listener 계약을 그대로 사용한다.
새 외부 설계를 무비판적으로 도입하지 않고 이 반례와 actual corpus로 반증한다.

root 방향은 pinned preprocessing grammar를 include/if/ifdef/ifndef/elif/else/endif/undef까지 확장하고,
listener가 logical directive와 physical Unicode code-point range를 생산하는 것이다. main C parser에는 같은
range를 줄 수 보존 공백으로 투영해 digraph와 spliced directive가 code로 오인되지 않게 한다. legacy INCLUDE,
conditional presence, 향후 include closure는 이 단일 grammar extraction에서만 파생한다. source
`startsWith/replaceFirst`, directive word parser, custom expression lexer를 fallback으로 남기지 않는다.

## 4. formal red

`SemanticEvidenceIrContractTest.cIncludeAndConditionalSyntaxComeFromThePreprocessorGrammar`가 다음을 고정한다.

- comment-separated `#/**/include <local/config.h>`
- digraph `%:include "digraph.h"`
- phase-2 line-spliced `#inc\\\nlude "spliced.h"`
- comment-separated `#/**/if 0/#else/#endif`
- digraph `%:if 0/%:else/%:endif`

구현 전 단일 집중 실행은 `expected include 3, actual 0`으로 실패했다. 첫 assertion에서 종료됐으므로
conditional 4 occurrence의 active/inactive 판정은 아직 결과가 없다. 구현 뒤 include projection과 각 call
presence를 별도로 통과시킨 뒤 current simple actual 450/17이 보존되는지도 전수 회계한다.

## 5. rollback과 미검증

- Parser grammar/generated code/listener slice를 독립 commit한다.
- Analyzer import evidence consumer는 Parser producer 검증 뒤 별도 slice다.
- C-062 type environment와 C-064 toolchain trace는 이 commit에 섞지 않는다.
- malformed include는 이 slice에서 explicit unresolved로 고정한다. macro-expanded include target,
  build-dependent `defined`, character/integer constant의 implementation-defined 동작은 구체 build evidence가
  생기기 전까지 해결됐다고 주장하지 않는다.

## 6. 구현 결과

Parser producer는 다음 단일 소유권으로 교체했다.

```text
decoded physical source
-> phase-2 splice-aware CPreprocessorLexer/Parser
-> one listener extraction
   -> import fact candidates + unresolved import population
   -> conditional directive events -> presence/conditional_region
   -> macro fact candidates + unresolved macro population
   -> physical directive ranges
      -> legacy INCLUDE/DEFINE projection
      -> same-code-point-count whitespace view for main C parser
-> EvidenceIrSealer canonical facts/completeness
```

`CAstListener.extractIncludes()`의 `trim/startsWith/replaceFirst`, conditional directive word parser와 자체
constant-expression lexer/parser는 삭제했다. 기존 문자열 재파싱을 보조 fallback으로 남기지 않았다. directive
keyword, include target, conditional precedence는 pinned ANTLR grammar가 소유하고 listener는 parse-tree context만
소비한다. line splice는 logical token을 읽되 모든 fact와 legacy projection에는 원래 physical Unicode
code-point range를 되돌린다.

추가로 `#include` target이 없는 경우가 종전에는 import population 0, unresolved 0, status `complete`로
침묵하던 결함을 formal red로 고정했다. `malformedIncludeLine`은 이 occurrence를
`insufficient_preprocessor_directive_syntax` unresolved 1건으로 회계한다. 유효 1건과 malformed 1건 fixture는
population 2 = emitted 1 + explicitlyUnresolved 1, status `partial`을 통과한다.

## 7. 3층 검증과 actual 전수 회계

1. 자동 회귀: `SemanticEvidenceIrContractTest`의 comment-separated/digraph/phase-2-spliced include 3건,
   conditional 네 call의 active/inactive, trailing preprocessing comment, 일반 constant expression, malformed
   include 회계가 green이다. Parser 전체는 184 tests, failures 0, errors 0, skipped 10이다.
2. 결정론 전수 회계: actual test는 각 source를 두 번 parse해 AST/evidence byte identity를 확인한다. APAS 23
   source에서 import fact 450 = legacy INCLUDE 450이고 각 occurrence의 exact target source slice와 시작/끝 line을
   전건 대조했다. target partition은 angle 450, quoted 0, computed 0, unresolved 0이다. conditional region 17,
   call 12,113(active 12,080/inactive 33), macro 183, diagnostics 0, ANTLR recoveries 0이다. call ledger와 macro
   ledger를 포함한 전체 fact ledger SHA는 변경 전과 동일한
   `0829b440ec777845e9a413a7292ad22c31083ba9fdd997a4b8eb3bf65dc9da34`; import 전용 ledger SHA는
   `2e1a6bef42fa6d2954a516126d71473f101fc6da0f29c50cb27e4816dc2c8a0c`다.
3. 봉인 직접 의미 판정: actual report의 `sealedDirectJudgmentPopulation`에 import 450 전건을 exact directive와
   target preview로, conditional region 17 전건을 exact source region과 presence로 포함했다. 각 import는
   grammar fact target과 legacy projection의 동일성을 자동 대조한 뒤 봉인됐다.

정본 실행물은
`_runs/framework/parser-directive-grammar-20260821-r3.json`이며 SHA-256은
`7d4dd20b852f1662694805d8a42ebcee43e50a9588261269ee12402274293b36`이다. grammar source SHA-256은 lexer
`53237d69d134e1658b62aabe3b530ead95c0f870b8f9fb7f342c652767ac3d18`, parser
`1cc9fe390609510e62c1e4b38c5e6e85ba2c90ff8218f0b6c0dc038aefaf3ef3`이며 language catalog checksum은
`9c5413de674322b179801ebe176dffe1b2c5fd1e6d0036baca63b75937a01a9a`다.

## 8. 남은 경계

- 이 slice는 Parser producer만 닫는다. Analyzer는 아직 canonical import payload를 strict validation/binding하지
  않으므로 consumer closure는 별도 red/commit이 필요하다.
- actual 450건은 모두 direct angle target이다. computed include의 build-configured macro expansion은
  compilation database/header/toolchain trace가 없는 상태에서 구체 header로 추측하지 않는다(C-064).
- `defined(identifier)`는 grammar가 인식하지만 build macro environment가 없으면 결과는 unknown이다.
  character/integer constant의 구현 정의 동작까지 compiler인 척하지 않는다.
- C type-name project-global usage regex(C-062)와 universal-character-name identity(C-063)는 이 slice에 섞지
  않았으며 계속 미검증이다.
- Parser commit은 독립 revert 가능하다. Analyzer consumer가 준비되지 않아도 기존 legacy INCLUDE shape는
  grammar fact에서 exact-once로 투영되어 현재 계약을 보존한다.

Parser producer는 `d690e51adfc4cc74e923fddce52425d43431f91b`
(`fix(parser): make C directives grammar-owned`)로 `origin/main`에 push했다.

## 9. Analyzer consumer formal red

Parser producer push 뒤 Analyzer 배선을 조사한 결과 `validate_common_evidence()`의 generic fact loop는 import의
canonical ID와 emitted count만 확인하고 payload를 폐기한다. `ValidatedCommonEvidence`는 call/macro만 반환하며,
`framework_reader._collect_internal()`은 legacy INCLUDE/IMPORT의 name/line을 직접 `import_refs`로 승격한다.
따라서 Parser canonical import target/range/presence와 AST projection이 달라도 downstream scope/reference가 이를
검출하지 못한다.

formal red는 angle, quoted, phase-2 line-spliced, Unicode target 4건을 한 fixture에 넣고 다음을 요구한다.

- language-neutral `ImportEvidenceFact`가 canonical ID, directive/target range, target kind, grammar rule, presence를
  보존한다.
- payload field/value와 빈/out-of-range target은 fail closed한다. Analyzer는 target text를 regex로 재파싱하지
  않고 Parser 소유 `targetKind` enum과 exact source slice만 검증한다.
- canonical fact와 legacy projection의 population/order/target/start/end physical line을 exact-once로 결합한다.
- mismatch나 duplicate는 downstream import scope 전에 실패한다.

최초 집중 실행은 `AttributeError: ValidatedCommonEvidence has no attribute import_facts`로 실패했다. 이 red를
green으로 만든 뒤 actual Parser report 450건을 Analyzer reader에 직접 통과시켜 producer/consumer ledger를
재대조한다.

## 10. Analyzer strict binding 구현과 검증

Analyzer는 language-neutral `ImportEvidenceFact`를 추가해 canonical ID, source ID, directive/target exact range,
directive/target kind, grammar rule, presence를 immutable contract로 검증한다. payload는
`directiveKind/targetKind/targetRange` exact fields만 허용하고 빈/out-of-range target이나 알 수 없는 enum은 fail
closed한다. 단, target 문자열이 angle/quoted인지 Analyzer가 regex나 prefix로 다시 판정하지 않는다. 그 syntax
소유자는 Parser grammar이며 Analyzer는 exact range의 source slice와 legacy projection 동일성만 검증한다.

Framework binding은 다음을 보장한다.

- supported import completeness에서는 canonical fact 수 = legacy INCLUDE/IMPORT 수여야 한다.
- traversal order, exact target text, physical start/end line가 각 occurrence에서 같아야 한다.
- canonical import ID는 전부 unique여야 하고 downstream `import_refs`는 range/targetRange/kind/rule/presence를
  보존한다.
- Parser가 다른 언어에서 import를 `unsupported` 또는 `not_applicable`로 명시하면 legacy projection을 버리거나
  canonical인 척하지 않고 `import_id=""`, 해당 evidence status,
  `parser_import_evidence_not_available` reason으로 명시적 degraded 처리한다.
- DBMS envelope가 import fact를 생산했는데 DBMS consumer가 없으면 조용히 버리지 않고 fail closed한다.

formal fixture는 angle, quoted, phase-2 line-spliced, Unicode target과 corrupt kind/range/extra field,
population/target/end-line mismatch를 고정했다. 최초 red는 `ValidatedCommonEvidence.import_facts` 부재였고 이후
34 tests가 green이다. step2 전체는 97 passed, 1 skipped다.

actual은 Parser `d690e51` 산출물 23개를
`_runs/framework/parser-directive-analyzer-fixture-20260821-r1`에 봉인 export해 production
`verify_source_ast_closure -> read_antlr_json -> validate_common_evidence -> framework_reader`를 두 번 통과시켰다.
결과는 import 450/unique 450, duplicate 0, Parser/Analyzer import ledger SHA 동일
`2e1a6bef42fa6d2954a516126d71473f101fc6da0f29c50cb27e4816dc2c8a0c`, target kind angle 450,
presence active 449/inactive 1, evidence status complete 450, deterministic replay 동일이다. call 12,113와 macro
183 ledger도 그대로 통과했다. 봉인 import 450건 전부의 range/targetRange/kind/presence/source target을 Analyzer
결과와 대조했다.

직접 판정에서는 단순 `<pfmcom.h>`, 긴 trailing comment가 붙은
`<pdb_dual_zord_stl_dmb_if_log_s_s0001.h>`, 그리고 inactive fact
`4cb06d86a857bc112715b599876cb23dc52b831190eaf294ad26f57f3ea8ff7d`의
`<zngmmcap20020.h>`를 원문 directive/target/presence와 대조했다. trailing comment는 directive range에는
보존되지만 target range에는 섞이지 않았고, inactive include는 `#if 0` 아래라는 사실이 active로 변조되지
않았다.

Analyzer actual 보고서는
`_runs/framework/analyzer-directive-import-consumer-20260821-r1.json`, SHA-256
`b44da9a357854b65c59ed83d8c6df0aaa1792ddb734791180eba072ab1728337`이다. 전체 회귀는 1,228 passed,
7 skipped, 367 subtests다. 이번 검증은 `ROBO_RUN_NEO4J_INTEGRATION=0`으로 수행해 DB write 0회다.

이 책임 slice는 strict ingestion/binding까지만 닫는다. `import_refs`가 다음 `import_scopes -> REFERENCES`에서
canonical occurrence ID/presence를 보존하는지, Neo4j/API/MCP가 import 자체를 공개해야 하는지는 아직 조사
중이며 E2E 공개 완료로 주장하지 않는다.
