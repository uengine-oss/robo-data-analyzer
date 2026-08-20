# Analyzer host-language syntax ownership / intermediate-LLM audit — 2026-08-21

## 1. 범위와 중단 조건

- 대상: Framework와 DBMS Analyzer의 source 재해석, call/import/reference/table/external/branch/column
  생산자와 그 downstream 소비자.
- 의미 owner는 Framework `routine`, DBMS `effective AST CodeNode`로 계속 분리한다.
- Architect는 조회·수정·stage·실행하지 않았다.
- Neo4j `verify_connectivity`와 `SHOW DATABASES`를 다시 확인했고 `neo4j/test/framework/dbms/system`만
  존재하며 모두 online이었다. 이 조사에서 DB write는 0회다.
- 최초 조사는 Analyzer `b448c7be27c54cdca1369ece7c6bd0a670311ec3`, Parser
  `e5fbf6b2e2a20dfc1bf42a665d19960715a6e2dd`에서 시작했다. 이후 C-047 Analyzer slice는
  `19498989434a7a7f033afc478341227d79f5030a`, common evidence IR Parser slice는
  `48de7f82f310c522d35edc697ba53d7857cd4264`까지 각각 독립 push됐다.

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

#### C-047 교정 슬라이스 검증 결과

call-review를 단순 prompt 보정이나 더 큰 drop gate로 고치지 않았다. 이 경계에는 이름과 빈도밖에 없어서
실제 호출인지 판정할 exact syntax/runtime 증거가 없고, finding으로 남겨도 소비 계약이 없다. 따라서
생산자·prompt·schema·validator·설정·배선을 모두 제거하고 Parser AST와 검증된 skill 계약의 결정론 분류만
canonical CALLS를 바꾸도록 했다.

3층 검증 결과는 다음과 같다.

1. **자동 회귀**: 제거 계약 red는 기존 배선/산출물 때문에 `5 failed, 2 passed`였고 구현 뒤 집중
   `16 passed, 43 subtests passed`, 전체 `1178 passed, 6 skipped, 367 subtests passed`가 green이다.
   prompt ledger도 `16/16 byte-identical`이다.
2. **결정론 전수 회계**: 기준 APAS AST 23파일을 현재 reader와 실제
   `proframe-c-pfm-dbio` skill 계약으로 다시 분류했다. `12,112 = edge 958 + stdlib 6,935 + framework
   4,143 + source macro 76`, incomplete 0, wrapper unresolved 0이다. edge 958은 internal 679/external
   279이고, 네 이름의 AST 46 occurrence가 external edge 46개로 exact-once 보존됐다.
3. **봉인 직접 의미 판정**: 표본이 아니라 46개 전부의 source line, Parser `FUNCTION_CALL`, owner routine,
   분류 edge를 대조했다. 6파일/21 routine의 exact 위치는 아래와 같고 source와 AST 불일치는 0이다.

| 파일 | exact source line | 이름/건수 |
|---|---|---|
| `apascrtu0010t02.c` | 290-297, 788, 861, 874, 946, 1005, 2155-2159, 3376, 3473, 3511 | `PFM_DSP` 16, `PFM_MSGN` 3, `PFM_MSG_CLEAR` 2 |
| `zapamcom10090.c` | 2973, 3027, 3665, 4396, 4427 | `PFM_DSP` 3, `PFM_ERRB` 2 |
| `zapamcom10100.c` | 688 | `PFM_DSP` 1 |
| `zapamcom10200.c` | 790, 802, 1006, 1406, 1759 | `PFM_DSP` 5 |
| `zapamcom20010.c` | 271, 355, 573, 584, 591, 1144, 1626, 1639, 1679, 1811, 1817, 1858, 2048 | `PFM_DSP` 13 |
| `zordmfax00001.c` | 2113 | `PFM_DSP` 1 |

이름과 문자열 인자만 보면 logging/message/error 계열로 보이는 항목도 있지만, 그것은 삭제 근거가 아니다.
특히 `PFM_MSG_CLEAR()`처럼 상태 효과 가능성이 있는 호출을 source/Parser 또는 framework 계약 없이 무효화할
수 없다. 따라서 이 슬라이스의 봉인 판정은 네 이름 모두 **호출 occurrence=true, target semantics=unknown,
scope=external, 보존**이다. 이후 Parser/공식 framework 계약이 더 강한 증거를 제공하면 occurrence ledger를
남긴 채 파생 aggregate의 분류만 바꿀 수 있다.

이 검증은 read-only였고 Neo4j replay/write는 하지 않았다. 그러므로 현재 `framework` DB에는 과거 실행의
46개 삭제 상태가 그대로이며, DB 교정은 evidence IR 슬라이스가 실제 replay 3층 검증을 통과할 때만 수행한다.

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

### C-052 — C frontend가 build configuration 없이 조건부 컴파일의 첫 분기를 무조건 활성화

Parser `CLanguageModule.preprocessSource`는 `#if/#ifdef/#ifndef` 조건을 평가하거나 build configuration을
받지 않는다. 그런데 directive 뒤 첫 분기는 항상 남기고 첫 `#else/#elif`부터 `#endif`까지 공백으로
바꾼다. 이는 조건을 모르면 `conditional/unknown`으로 보존해야 하는데 임의의 concrete build를 만든다.
최초 유입은 Parser commit `99fdc5f`이고 현재 위치로의 이동 외 의미 변경은 없었다.

기준 APAS에는 7파일, directive 36행, 조건 블록 17개(상수 16, 외부 macro 의존 1)가 있다. 실제 AST 대조 결과:

- `#if 0`인데 AST가 active처럼 보존한 구간은 최소 69 node다. `zapamcom13010.c:499-528` 17개,
  `zapamcom10020.c:4496-4535` 27개, `zapamcom10110.c`의 50/330-335/1283-1352 구간 25개이며,
  여기에는 비활성 함수 `c800_ngmf_spcl_pwd_chk`와 그 호출까지 포함된다.
- `#if 0 ... #else`의 실제 else 분기는 반대로 사라졌다. `zordmutl00010.c:588-591`과
  `zapamcom10110.c:788`의 source statement는 AST node 0개다.
- `#ifdef PFM_DBG`처럼 외부 build macro에 달린 구간도 정의 상태를 모른 채 첫 분기로 확정한다.

판정: **실제 의미 손실과 거짓 의미 생산이 동시에 발생**한다. 단순히 `#if 0/1`만 고치는 것은 또 다른
happy path다. Parser frontend가 실제 compilation database/build macro set을 받으면 concrete active view를,
없으면 모든 branch를 condition provenance와 함께 `conditional/unknown` evidence로 보존해야 한다. Analyzer는
이를 다시 평가하지 않고 owner별 의무/명시 degraded로 소비해야 한다.

### C-053 — offset 인코딩과 recovery rebase가 정의되지 않아 WIP call range가 범용 exact range가 아님

당시 Parser WIP는 C/Java/Python `FUNCTION_CALL`에 token `startIndex/stopIndex+1`을 직접 넣고 테스트에서 Java
`String.substring`으로 검증한다. ANTLR 4.13 `CharStreams.fromString` token index는 Unicode code point 좌표인
반면 Java substring은 UTF-16 code unit 좌표라 supplementary character가 앞에 있으면 동일 source slice가
아니다. `positionEncoding`, line/column half-open range, source hash도 없다.

또한 Python은 `TrailerContext`만 범위로 삼아 `repo.find(...)` 중 `.find(...)` 또는 `(args)`만 담고 callee
expression 전체를 소유하지 못한다. localized recovery는 `AstCoordinates.rebaseChildren`에서 line만 옮기고
offset은 옮기지 않으며, unit substring/context-prefix 경로의 offset base도 계약에 없다.

판정: **잠재 또는 recovery 실제 의미 손실**. 숫자형 offset 네 필드를 추가하는 방식은 승인하지 않는다.
source hash/version, 명시 position encoding, half-open code-point offset/length, full-source rebase,
canonical occurrence ID를 공통 sealer가 검증하고 언어 listener는 grammar context 경계만 제공해야 한다.
구현에서는 decoded source hash와 range가 slice를 이미 결박하므로 fact별 slice hash 반복은 제거했다.

### C-056 — exact callee range만으로 terminal target을 만들면 Analyzer source 재파싱이 재발함

`calleeRange`는 `service.repo().find`, `(*callback)`, `(lambda x: x)` 같은 exact expression을 무손실로
보존하지만, 공통 Analyzer가 이 문자열에서 `find`나 `callback`을 정규식·split으로 다시 뽑으면 C-046과 같은
언어별 구문 소유 위반이 반복된다. 반대로 모든 expression을 이름처럼 취급하면 함수 포인터·람다·연산 결과
호출에 거짓 target을 만든다.

판정: **잠재 의미 손실/거짓 연결**. Parser `48de7f8`에서 각 grammar listener가 구문적으로 이름이 있는
호출, 생성자 호출, 임의 expression 호출을 `calleeKind=named|constructor|expression`으로 분류하고,
앞의 두 종류만 grammar terminal `terminalName`을 발행하도록 red→green 고정했다. Analyzer는 이를 검증해
target 후보에 사용하되 project symbol resolution은 계속 Analyzer가 소유한다. C/Java/Python 계약에는
member call, anonymous class constructor, 함수 포인터, lambda callable을 포함했고, APAS 12,113건은
named 12,113 / constructor 0 / expression 0으로 exact partition됐다.

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

## 7. 확정 common evidence IR v1 경계

큰 단일 AST schema를 새로 만드는 대신 versioned evidence envelope과 작은 typed fact를 둔다.

공통 envelope:

- `version`, corpus-relative `sourceId`, raw/decoded SHA-256, exact `decodedText`, strict decode와 parse status
- `positionEncoding=unicode-code-point`, `rangeConvention=half-open`,
  `rangeEncoding=char-offset-length`
- `factId = SHA-256(sourceId, decodedTextSha256, kind, range, same-kind/range ordinal)`
- file-local `grammarRules[]`, `presences[]` table과 fact의 reference
- source text는 파일당 한 번만 보존하고 call/callee/ordered argument는 exact range로 참조
- kind별 `population/emitted/explicitlyUnresolved`, reason과 `unresolvedScopeFactIds`

typed fact:

- symbol declaration/occurrence: canonical/local symbol ID, role(definition/reference/import/read/write), owner
- import/include: module/path components, imported members, aliases, wildcard/local/system, exact range
- call site: exact call/callee/ordered argument range. target set과 enclosing semantic owner는 Analyzer가 resolve
- literal group: raw segments, decoded logical value가 완전할 때만 decoded value, concat/escape status
- assignment/parameter/macro declaration: target/source occurrence와 exact expression ranges
- embedded-language span: host literal segment mapping + declared/detected language + parse status; SQL은 dedicated
  SQL parser가 별도 AST evidence로 만든다.

Analyzer는 이 fact를 언어 중립으로 resolve/own/dedup한다. unknown/ambiguous/conditional은 임의 후보를
선택하지 않고 missing-context/degraded ledger에 남긴다. Scan/Deep Agent는 fact의 field/relationship 패턴을
제안하고, verifier는 전 occurrence에 재적용해 exact coverage를 증명한다. raw source regex는 preview나
진단 후보로만 허용하며 canonical edge producer가 될 수 없다.

## 8. Parser WIP 판정과 교체 결과

초기 WIP의 legacy node별 call text/argument/offset은 승인하지 않았다. full-callee Python 경계, position
encoding, source binding, conditional provenance, recovery rebase/completeness가 없고 같은 source text를
노드마다 중복했기 때문이다. 이를 `d8dca38`에서 listener의 grammar range 후보와 공통 sealer로 교체했고,
`48de7f8`에서 grammar-owned callee kind/terminal을 추가했다.

- C/Java/Python call facts와 C conditional-region facts는 canonical v1로 발행한다.
- Oracle/PostgreSQL call kind는 구현 전 `unsupported`이며 빈 complete가 아니다.
- parser recovery/lossy decode/unknown build context는 각각 explicit partial reason으로 남는다.
- workspace 밖 공개 full-file parse도 absolute path를 쓰거나 evidence를 생략하지 않고 content-addressed
  `unscoped/...` source ID를 쓴다.
- C first/alternate branch 추측은 제거했다. nested region은 effective parent presence를 합성한다.

Parser legacy C type-name 수집, include/define 추출, PL/pgSQL initializer call에는 기존 heuristic이 남아 있다.
이들은 v1 지원 kind가 아니며 canonical evidence로 승격하지 않는다. import/symbol/macro/DBMS call slice에서
grammar/build-context red 없이 문자열 규칙을 확대하지 않는다(C-055 pending).

## 9. 되돌릴 수 있는 책임 슬라이스와 비교 계약

1. **완료 — C-047 독립 제거 슬라이스**: 증거 없는 call-review 생산자 전체를 제거했다. baseline
   958 occurrence-derived edge와 46개 모순 사례를 자동 회귀·결정론 전수 회계·봉인 직접 판정으로
   red→green 고정했다. Neo4j actual replay만 후속 evidence IR 통합 검증에 남아 있다.
2. **완료 — Evidence envelope/range + conditional provenance/callee syntax 슬라이스**: Parser `48de7f8`.
   C-052/C-053/C-056과
   구현 중 발견한 recovery complete 세탁, non-workspace evidence skip, nested effective-presence 결함을
   red→green 고정했다. actual은 12,113 call facts, 봉인 526/526이며 상세는 common IR 검증 원장에 있다.
3. **진행 — Analyzer call consumer 슬라이스**: Analyzer의 wrapper/call source reparse를 v1 IR 소비로
   교체한다. APAS 12,113 call fact와 기존 aggregate CALLS를 owner routine별 exact-once 비교한다. import/symbol은
   아직 v1 unsupported이므로 call과 한꺼번에 성공 처리하지 않고 다음 Parser kind slice로 분리한다.
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
- Parser v1 자체의 C/Java/Python call, C conditional actual/성능은 검증했다. Analyzer consumer,
  routine/effective-CodeNode owner mapping, Neo4j/API/MCP replay와 Oracle/PostgreSQL call은 미검증이므로 전체
  파이프라인 정확도 향상을 아직 주장하지 않는다.
