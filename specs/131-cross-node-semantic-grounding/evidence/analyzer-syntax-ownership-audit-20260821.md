# Analyzer host-language syntax ownership / intermediate-LLM audit — 2026-08-21

## 1. 범위와 중단 조건

- 대상: Framework와 DBMS Analyzer의 source 재해석, call/import/reference/table/external/branch/column
  생산자와 그 downstream 소비자.
- 의미 owner는 Framework `routine`, DBMS `effective AST CodeNode`로 계속 분리한다.
- Architect는 조회·수정·stage·실행하지 않았다.
- Neo4j `verify_connectivity`와 `SHOW DATABASES`를 다시 확인했고 `neo4j/test/framework/dbms/system`만
  존재하며 모두 online이었다. 이 조사에서 DB write는 0회다.
- Analyzer main HEAD는 `b448c7be27c54cdca1369ece7c6bd0a670311ec3`, clean에서 조사했다.
- Parser main HEAD는 `e5fbf6b2e2a20dfc1bf42a665d19960715a6e2dd`이며 C/Java/Python call text/argument/
  offset을 추가한 미커밋 WIP 7개 파일이 있다. 아래 §8 때문에 현재 형태를 계약으로 승인하거나 commit하지 않는다.

## 2. actual 기준선

기준 실행은 `specs/131-cross-node-semantic-grounding/_runs/framework/
20260819_001655_0cf07d21afb9`이고 source 23개를 읽었다.

| 모집단/산출 | actual |
|---|---:|
| Parser JSON 전체 AST node | 20,947 |
| `FUNCTION_CALL` AST occurrence | 12,112 |
| routine 정의 | 416 |
| `INCLUDE/IMPORT` | 450 |
| AST `callExpression/callArguments/startOffset/endOffset` | 모두 0 |
| Analyzer `REFERENCES` | 8,113 |
| Analyzer `INIT_BY` | 97 |
| 최초 분류 CALLS | 958 |
| call-review 후 state CALLS | 912 |
| Neo4j CALLS `(caller,callee)` 관계 | 629 |
| table pattern 후보 | 291 |
| 결정론 R/W raw edge | 98 |
| 최종 Neo4j R/W | 84 (`READS` 69, `WRITES` 15) |
| table residual | 193 |
| ExternalChunk / REFER_TO | 0 / 0 |

Neo4j에는 `ANALYZER_OWNED` 16,862개와 위 관계가 있으나 DBMS DB는 현재 비어 있다. 따라서 DBMS
경로는 자동/반례 검증은 가능해도 actual corpus 의미 합격 근거는 아직 없다.

호출 분류 상세는 `12,112 = 958 + stdlib 6,935 + framework 4,143 + macro 76`이고, 958개에서
call-review가 46개를 삭제해 912개가 됐다. 912→629는 동일 routine-callee를 Neo4j `MERGE`한 결과다.

## 3. 확정 결함

### C-046 — Parser call occurrence를 버린 뒤 source 한 줄 정규식으로 wrapper 인자를 복원

배선:

1. Parser `FUNCTION_CALL`은 현 정본에서 `name/startLine/endLine`만 낸다.
2. `framework_reader._collect_internal`은 다시 `{caller_id, call_text, file, line}`만 보존하고
   `call_id=<routine>:<line>:<traversal-index>`를 만든다.
3. `call_classifier._extract_wrapper_callee`는 해당 source의 단 한 줄에서
   `wrapper\s*\(\s*...`를 재검색한다.
4. 추출 실패 시 `wrapper_unresolved += 1` 후 call occurrence를 버린다.

원인: Parser가 이미 구분한 call/argument 구문 경계를 Analyzer가 받지 못하고, Analyzer가 C-like 한 줄
표기에 우연히 의존한다. multiline/nested/escaped/named argument와 다른 언어 표기는 일반적으로 닫히지 않는다.
현 APAS wrapper 86건은 모두 같은 줄이라 actual 발동은 0이지만 최소 multiline 반례는 즉시 실패한다.

### C-047 — 중간 LLM의 부정 reason을 무시하고 실제 CALLS 46개 삭제

`agents.call_review`의 prompt는 “조금이라도 실제 호출일 가능성이 있으면 drops에 넣지 않는다”고 요구한다.
그러나 출력 schema는 `drops[{callee,reason}]`뿐이고 reason의 의미를 검증하지 않는다. 기준 실행의
`tool_events.jsonl`에서 모델은 다음 네 항목 모두 reason에 “실제 호출일 수도 있어 확신이 부족하므로
드롭하지 않음”이라고 썼지만 항목을 `drops` 배열에 넣었다.

- `PFM_DSP` 39회
- `PFM_MSGN` 3회
- `PFM_MSG_CLEAR` 2회
- `PFM_ERRB` 2회

validator는 목록 포함/프로젝트 정의 아님/drop 비율 50% 이하만 검사해 네 이름을 승인했고
`link_calls_by_name_step._maybe_review_externals`가 21개 routine의 CALLS 46개를 실제 state와 Neo4j에서
삭제했다. 현재 Neo4j의 네 callee node/CALLS는 0개다. 이 CALLS는 downstream `build_context_step`의
caller/callee, summary dependency, component Leiden graph, health metric, search graph traversal에 쓰인다.
Rule/Example의 직접 call fact는 별도 AST call-line 경로라 남았지만 graph와 context가 서로 모순된다.

판정: **실제 의미 손실**. LLM은 unknown external을 삭제하는 권위자가 될 수 없다. 구조화 parser/skill
증거가 없으면 호출을 explicit unresolved/external로 보존해야 한다.

### C-048 — 193개 table residual의 상태가 routine 의미/API에 도달하지 않음

기준 실행은 known skill pattern 후보 291건 중 98 edge, 193 residual을 만들었다. known skill 경로는
`matched_skill_contract_closed`라는 이유로 residual LLM을 0회 호출하고 193개를 그대로 반환한다.
하지만 `unresolved_cases`의 소비자는 residual step뿐이며 이후 `build_context_step`, semantic sufficiency,
run artifact, Neo4j, API/MCP가 읽지 않는다. step docstring의 “downstream artifact dump” 주장도 실제 writer가 없다.

판정: residual을 가짜 edge로 승격하지 않은 것은 맞지만 **미해결 의미 의무의 은닉**이다. known skill
계약과 맞지 않는 193개 exact source site를 routine별 missing-context/degraded ledger에 귀속하고, 최종
전수 회계에서 `resolved + explicitly-unresolved = obligation population`을 증명해야 한다.

### C-049 — Analyzer 안의 host-language lexer/parser가 canonical 의미를 생산

다음은 데이터-driven이라는 이유와 무관하게 Analyzer가 host-language syntax를 다시 판정하는 경로다.

| 하위 경로 | 재해석 | canonical 소비/actual 영향 |
|---|---|---|
| C-049a `token_matcher.py`, `import_scopes.py` | 언어별 identifier/qualified-name regex, string/comment 제거, raw import/extends/implements 분해, Python C3 분기 | actual `REFERENCES` 8,113 + `INIT_BY` 97의 단일 생산자. import 450개가 scope 후보를 바꿈 |
| C-049b `quality_gates/lexing.py`, `branch_inventory.py` | C/Java/Python/SQL/COBOL comment/string/inactive block, condition call/literal/identifier, C octal/fallthrough | Rule/Example LLM-0 inventory의 active statement, condition value, direct effect atom, grounding accept/reject를 바꿈 |
| C-049c `call_classifier.py` | C-family suffix와 `#define` function macro regex | call을 macro로 drop. actual 76개 |
| C-049d `link_external/resolver_catalog.py`, `key_index.py` | signature parameter, nested call/arg, assignment, cross-language literal/comment/escape/concat 재파싱 | actual dynamic key site 1, wrapper `swgf_fopen` promotion 1. 현 ExternalChunk 0이라 graph edge 영향은 0 |
| C-049e `regex_table_linker.py`, `dml_semantics.py` | whole source scan, C literal token/adjacent concat, embedded SQL regex DML parse | actual pattern 후보 291, raw R/W 98, residual 193 |
| C-049f `enrich_table_description_step._focus_column_source_text` | SQL/C comment, quoted string/identifier, bare identifier 재lex | table-description 실제 prompt에서 sibling column을 가림. exact occurrence span이 없어 prompt projection이 Parser 증거와 독립적으로 변형됨 |

`structure_miner.py`의 generic shape regex와 Scan Agent/known skill regex는 후보 발견 자체로는 유지 가능하다.
문제는 이 후보를 raw host source에 적용하고 Analyzer의 재lexer 결과를 canonical truth로 쓰는 현재 경계다.

판정: C-049a/b/c/e/f는 canonical graph/Rule/prompt를 바꾸므로 **실제 또는 잠재 의미 손실**,
C-049d는 기준 actual에서 derived 후보 1개가 발동했지만 external graph가 없어 최종 영향 0이다.

### C-050 — DBMS가 versioned Parser evidence와 legacy source parser를 동시에 보유

`link_dbms_tables_step`은 `data_object_evidence_version`이 있으면 Parser evidence를 fail-closed 소비하지만,
키가 없으면 raw source에서 주석 제거, table regex, DML regex로 계속 진행한다. 이는 새 listener가 versioned
empty를 빠뜨려도 성공처럼 보이게 하는 silent dual producer다. `column_usage_projection`도 unqualified/
derived output을 `source_language == oracle`일 때만 허용해 PostgreSQL v4와 계약이 갈라져 있다.

판정: **실제 DBMS 영향 미검증**(현재 DBMS graph/run 없음), 설계상 의미 손실. 모든 지원 Parser frontend가
명시 versioned empty/nonempty evidence를 내고 Analyzer는 누락을 fail-closed해야 한다.

### C-051 — 12,112 call occurrence의 정체성·인자·위치를 버리고 aggregate 관계만 남김

현 Parser JSON의 모든 12,112 call에 argument/range가 없다. Framework reader가 Parser node 자체를 그래프
노드로 만들지 않고 minimal dict로 바꾸며, caller-line-traversal index를 임시 ID로 쓴다. 분류 후 state 912,
Neo4j 관계 629만 남고 CALLS 정체성은 `(caller_id,callee_id)`다. 같은 routine의 여러 call site, 인자,
branch/source range는 graph/API에서 복구할 수 없다.

aggregate CALLS 자체는 dependency view로 유효하지만 source occurrence의 대체물이 아니다. exact call-site
ledger를 별도 보존하고 aggregate CALLS는 그 ledger에서 결정론적으로 파생해야 한다.

## 4. 안전/preview/search/degraded와의 구분

- `branch_inventory._ast_termination_rows` 160자는 display/prompt preview이며 Rule cond/then 정본은 아니다.
- embedding 2,000자 절단은 actual 2건 발동했지만 canonical graph가 아닌 search recall degraded다.
- regex 검증 sample 25 등은 표시용이고 원 모집단을 자르지 않는다.
- residual sample 30/max rounds 3은 원 residual을 보존한다. 문제는 sample 수가 아니라 최종 residual이
  downstream에 연결되지 않는 C-048이다.
- call-review top 300의 미열거 보존은 안전하다. 열거 항목을 LLM이 삭제하는 C-047은 안전하지 않다.
- DDL은 `sqlglot` parser/tokenizer를 쓰는 dedicated adapter라 naive host-source regex와 같은 범주가 아니다.
  외부 PDF/XLSX/DOCX/HWP/image adapter도 자기 형식의 dedicated parser이며 Analyzer host-language reparse가 아니다.
- Rule expression 내부 DSL을 Python `ast.parse`로 검증하는 경로는 Analyzer가 소유한 문법이므로 제외한다.

## 5. source→consumer 배선 결론

```text
source
  -> language grammar/listener
  -> current coarse AST
  -> framework_reader가 call/import occurrence 축소
  -> Analyzer host lexer/parser가 reference/macro/arg/literal/SQL을 재구성
  -> Scan/call-review/residual LLM 후보
  -> parsed schema + 불충분 validator
  -> CALLS/RW/REFERENCES/INIT_BY
  -> build_context / actual summary prompt / deterministic Rule·Example
  -> Neo4j
  -> search/API/MCP graph consumer
```

근본 경계는 아래여야 한다.

```text
source
  -> language-specific grammar/listener (구문 owner)
  -> language-neutral exact evidence IR + source/diagnostic closure
  -> Analyzer language-neutral ownership/symbol/framework resolution
  -> exact occurrence ledger
  -> derived aggregate graph + prompt evidence + explicit unresolved ledger
  -> Neo4j / API / MCP
```

Parser listener가 언어별인 것은 위반이 아니다. 새 언어 문법과 구문 차이는 그 frontend가 소유한다.
위반은 Analyzer 공통 단계가 C/Python/Java/SQL의 주석·문자열·identifier·call 표기를 다시 아는 것이다.

## 6. 외부 근거와 채택 범위

- ANTLR `ParserRuleContext`는 subtree의 첫/마지막 token interval과 start token을 제공하고, `Token`은
  start/stop character index를 제공한다. 따라서 listener가 exact range를 내는 것은 runtime의 정식 기능이다.
- ANTLR `CodePointCharStream`은 UTF-16 code unit이 아니라 Unicode code point 입력을 다룬다. offset 계약은
  단순 `character`가 아니라 encoding과 half-open 여부를 명시해야 한다.
- SCIP은 document별 occurrence에 half-open typed range, symbol, definition/import role, position encoding을
  둔다. occurrence와 aggregate symbol graph를 분리하는 선례로 채택할 수 있다.
- SCIP만으로 call argument, assignment source, literal group, embedded-language span은 표현되지 않는다.
  그대로 도입하지 않고 occurrence/range/symbol-role 부분만 참고한다.
- CodeQL의 여러 언어 library는 call을 callee/caller/enclosing callable/ordered argument expression으로
  노출한다. 이는 call name+line만으로는 semantic consumer가 부족하다는 교차 검증 근거다. CodeQL 자체를
  runtime dependency로 도입한다는 뜻은 아니다.

1차 출처:

- https://www.antlr.org/api/Java/org/antlr/v4/runtime/ParserRuleContext.html
- https://www.antlr.org/api/Java/org/antlr/v4/runtime/Token.html
- https://www.antlr.org/api/Java/org/antlr/v4/runtime/CodePointCharStream.html
- https://github.com/scip-code/scip/blob/main/scip.proto
- https://sourcegraph.com/docs/code-navigation/writing-an-indexer
- https://codeql.github.com/codeql-standard-libraries/java/semmle/code/java/Expr.qll/type.Expr%24Call.html

## 7. 권고 common evidence IR 최소 계약

큰 단일 AST schema를 새로 만드는 대신 versioned evidence envelope과 작은 typed fact를 둔다.

공통 envelope:

- `evidenceVersion`, `sourceId`, `sourceSha256`, parser diagnostic closure
- `range={startLine,startColumn,endLine,endColumn,startOffset,endOffset,positionEncoding}`; half-open
- `canonicalOccurrenceId = hash(sourceId, sourceSha256, kind, range, ordinal-within-identical-range)`
- `activeStatus = active|inactive|conditional|unknown`와 알려진 경우 condition/provenance
- exact raw slice hash와 Parser가 생산한 normalized fields를 함께 보존

typed fact:

- symbol declaration/occurrence: canonical/local symbol ID, role(definition/reference/import/read/write), owner
- import/include: module/path components, imported members, aliases, wildcard/local/system, exact range
- call site: exact callee expression, resolved/ambiguous/unresolved target set, ordered argument evidence,
  enclosing routine/control/statement occurrence
- literal group: raw segments, decoded logical value가 완전할 때만 decoded value, concat/escape status
- assignment/parameter/macro declaration: target/source occurrence와 exact expression ranges
- embedded-language span: host literal segment mapping + declared/detected language + parse status; SQL은 dedicated
  SQL parser가 별도 AST evidence로 만든다.

Analyzer는 이 fact를 언어 중립으로 resolve/own/dedup한다. unknown/ambiguous/conditional은 임의 후보를
선택하지 않고 missing-context/degraded ledger에 남긴다. Scan/Deep Agent는 fact의 field/relationship 패턴을
제안하고, verifier는 전 occurrence에 재적용해 exact coverage를 증명한다. raw source regex는 preview나
진단 후보로만 허용하며 canonical edge producer가 될 수 없다.

## 8. 현재 Parser WIP를 그대로 commit하지 않는 이유

- C/Java/Python 세 listener만 고쳤고 PL/pgSQL `emitInitializerCall`은 여전히 regex로 첫 call만 뽑는다.
- `ParserUtils.emitInitializerCall`은 ordered args/range가 없고 whitespace가 소실된 `getText()` 입력을 받는다.
- Python listener의 `TrailerContext` range는 `f(...)` 전체가 아니라 `(…)` 또는 `.method(…)` trailer일 수 있어
  callee expression의 exact range가 아니다. 현 red test는 source substring 일치만 검사해 이 결함을 못 잡는다.
- `startOffset/endOffset`의 position encoding/source hash/version/active status가 없다.
- import, identifier, literal group, parameter, assignment, macro, embedded span 소비자를 닫지 못한다.

따라서 WIP는 증거 보존 대상으로 두되, 위 IR 계약과 per-language completeness matrix를 먼저 red로 고정한
뒤 교체한다. 숫자 상한 증설이나 multiline regex 확장으로 C-046만 막지 않는다.

## 9. 되돌릴 수 있는 책임 슬라이스와 비교 계약

1. **C-047 독립 제거 슬라이스**: call-review는 삭제 권한을 잃고 finding/proposal만 생산한다. baseline
   958 occurrence-derived edge가 보존되고 46개 모순 사례가 red→green이어야 한다.
2. **Evidence envelope/range 슬라이스**: source hash/position encoding/exact range/canonical occurrence ID를
   Parser에 추가하고 C/Java/Python/PLSQL/PLpgSQL/PostgreSQL 지원 matrix를 고정한다.
3. **Call/import/symbol 슬라이스**: Analyzer의 wrapper/import/token reparse를 IR 소비로 교체한다. 기존
   APAS 12,112 call/450 import/8,210 ref-init 모집단과 새 multiline/nested/unicode/ambiguous corpus를 비교한다.
4. **literal/embedded SQL 슬라이스**: Scanning Agent는 유지하되 normalized fact pattern만 제안한다.
   291 후보의 exact accounting과 193 explicit residual ownership을 고정한다.
5. **branch/DBMS/column 슬라이스**: active/literal/call/SQL evidence를 Parser/dedicated parser로 넘기고
   Framework routine와 DBMS effective AST owner를 각각 봉인한다.
6. 각 슬라이스는 red test, 자동 회귀, deterministic full accounting, sealed direct meaning 판정,
   actual replay의 정확도/시간/메모리 비교 후 독립 commit/push한다. 성능이 악화되면 자료구조/streaming을
   개선하며 의미를 절단하지 않는다. 합격하지 못한 구조 변경은 해당 commit만 revert할 수 있어야 한다.

## 10. 미검증

- DBMS actual run/graph가 없어 C-050과 새 DBMS evidence IR의 실코퍼스 영향은 미검증이다.
- 193 residual 각각이 “DDL 미제공”인지 “pattern/transform 결함”인지는 현 run이 cases를 artifact로 남기지 않아
  아직 분해 불가다. 다음 replay 전부터 exact residual artifact를 생산해야 한다.
- 새 IR의 모든 지원 언어 coverage와 performance는 red/구현/replay 전이므로 아직 정확도 향상을 주장하지 않는다.
