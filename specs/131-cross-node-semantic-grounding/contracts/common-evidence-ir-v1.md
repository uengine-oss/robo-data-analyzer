# Common Evidence IR 계약 (역사 1.0.0~1.3.0 / 현행 2.0.0)

## 1. 목적과 owner

이 계약은 source syntax를 언어별 Parser frontend가 한 번만 판정하고 Analyzer가 언어 중립 evidence만
소비하게 하는 wire boundary다. 하나의 AST shape로 모든 언어 문법을 평탄화하지 않는다.

- Framework semantic owner: enclosing routine.
- DBMS semantic owner: effective AST `CodeNode`.
- Parser language frontend owner: source decoding, grammar/token boundary, conditional presence, exact occurrence,
  declaration/definition과 lexical scope/declarator/specifier의 typed syntax fact. Parser는 final call binding,
  internal/external, linkage/type compatibility key를 생산하지 않는다.
- Analyzer owner: versioned language semantic adapter와 language-neutral resolution core, framework/DB object
  resolution, owner mapping, occurrence ledger와 aggregate graph 파생. 언어별 판단은 adapter 경계 밖으로
  흩어지지 않으며 이름·확장자·정렬상 첫 후보로 보정하지 않는다.
- Scan/Deep Agent owner: 검증할 fact-field/relationship pattern 제안. source syntax truth 생산 금지.

기존 `FILE.children` AST는 migration 동안 유지하지만 canonical 소비자는 versioned `evidence.facts`다.

## 2. file envelope

모든 accepted AST `FILE` root는 `evidence` object를 가진다.

```json
{
  "evidence": {
    "version": "1.3.0",
    "sourceId": "normalized/relative/path.c",
    "rawSourceSha256": "...",
    "decodedTextSha256": "...",
    "decodedText": "exact decoded source, once per file",
    "sourceEncoding": "UTF-8",
    "decodeStatus": "exact",
    "positionEncoding": "unicode-code-point",
    "rangeConvention": "half-open",
    "rangeEncoding": "char-offset-length",
    "parseStatus": "exact",
    "facts": [],
    "grammarRules": [],
    "presences": [],
    "completeness": []
  }
}
```

- `sourceId`: corpus source root 기준 `/` 상대경로. absolute machine path 금지. 공개 full-file parse에 corpus
  root가 주어지지 않은 경우에만 `unscoped/<rawSourceSha256>/<fileName>`을 사용해 생략 대신 입력 범위를
  명시한다. Analyzer canonical project publish는 unscoped ID를 임의 project path로 승격하지 않는다.
- `rawSourceSha256`: parser가 읽은 원 byte 전체의 SHA-256.
- `decodedTextSha256`: decoded Unicode scalar sequence를 UTF-8로 encode한 SHA-256.
- `decodedText`: 해당 hash의 exact decoded source. fact마다 snippet을 반복하지 않고 range로 이 한 원문을
  참조한다. display용 snippet은 consumer가 range로 파생하며 canonical wire fact가 아니다.
- `sourceEncoding`: strict decoder가 실제 사용한 정규 charset 이름.
- `decodeStatus`: `exact|lossy`. `lossy`는 canonical semantic publish 불가이며 explicit degraded다.
- `positionEncoding`: v1은 `unicode-code-point`만 허용한다. byte/UTF-16 index와 혼용 금지.
- `rangeConvention`: v1은 `half-open`만 허용한다.
- `rangeEncoding`: v1은 SARIF text-region 선례와 같은 character offset/length 형태만 허용한다.
- `parseStatus`: `exact|recovered|partial|unresolved`. diagnostics sidecar와 동일 source hash/status여야 한다.

복구가 source를 바꾸면 materialized final source 전체를 재파싱한 뒤 새 raw/decoded hash로 seal한다. unit AST의
local offset을 줄 번호만 옮겨 합치지 않는다. final source와 모든 fact range/slice 검증 전에는 canonical
publish할 수 없다.

## 3. range

모든 fact와 subexpression은 동일한 `range`를 사용한다.

```json
{"charOffset": 0, "charLength": 4}
```

- `charOffset`은 0-based Unicode code point, `charLength`는 non-negative code point 수다.
- end는 `charOffset + charLength`이며 exclusive다. line/column은 `decodedText`에서 결정론적으로 파생한다.
- CR/LF도 decoded source에 존재하는 code point 그대로 offset에 포함한다.
- `decodedTextSha256`과 canonical range가 slice를 이미 결박하므로 fact마다 source text/hash를 반복하지 않는다.
- 공통 sealer는 decoded source의 code-point 길이에 대해 모든 range를 검증한다. fact마다 source 처음부터
  재탐색하거나 name regex로 범위를 복원하지 않는다.

## 4. canonical fact identity

`factId`는 다음 UTF-8 NUL-delimited tuple의 SHA-256이다.

```text
robo-evidence-v1
sourceId
decodedTextSha256
kind
charOffset:charLength
ordinalWithinSameKindAndRange
```

ordinal은 grammar traversal order로 결정하며 같은 kind+range 안에서만 증가한다. source path/hash/range가 같은
fact는 replay·batch·concurrency와 무관하게 같은 ID를 갖고, 다른 source revision의 fact와 충돌하지 않는다.
aggregate CALLS/REFERENCES/RW는 fact ID 집합에서 결정론적으로 파생하며 occurrence ledger를 대체하지 않는다.

## 5. 공통 fact fields

모든 fact는 다음 필드를 가진다.

```json
{
  "factId": "...",
  "kind": "call",
  "range": {"charOffset": 0, "charLength": 8},
  "grammarRuleRef": 0,
  "presenceRef": 0,
  "payload": {}
}

"grammarRules": ["postfixExpression"],
"presences": [{
    "status": "active",
    "condition": null,
    "configurationId": null,
    "provenance": "unconditional"
}]
```

refs는 파일 안 0-based table index이며 grammar traversal 최초 등장 순서로 결정한다. Parser가 아직 소유하지
않는 Analyzer owner ID를 `null` placeholder로 반복하지 않는다. Analyzer는 fact ID를 routine/effective
CodeNode에 resolve한 뒤 별도 owner mapping을 만든다.

`presence.status`는 `active|inactive|conditional|unknown`이다. inactive도 삭제하지 않는다. configuration 없이
결정할 수 없는 C branch는 임의로 active/inactive를 고르지 않으며 exact condition source/range를 보존한다.

## 6. typed facts v1

### call

- fact `range`가 full call expression을 가리킨다.
- `payload.calleeRange`가 exact callee expression을 가리킨다.
- `payload.calleeKind`는 grammar가 확정한 `named|constructor|expression` 중 하나다.
- `payload.terminalName`은 `named|constructor`에서 grammar terminal의 exact text이고,
  `expression`에서는 반드시 `null`이다. Analyzer는 callee source를 정규식·split으로 재파싱해 terminal을
  만들지 않는다.
- `payload.argumentRanges[]`는 배열 순서 자체가 grammar argument order다.
- 모든 expression text는 파일당 한 번인 `decodedText`에서 range로 exact 복원한다.
- Parser의 terminal name은 구문 사실일 뿐 project target resolution이 아니다.

Analyzer Framework consumer는 compatibility AST와 canonical call fact의 population·traversal order·line을
exact-once 대조한 뒤 enclosing routine ID를 별도 owner mapping으로 더한다. compatibility AST가 없거나
개수가 다르면 canonical fact를 source에서 복원하지 않고 publish를 중단한다. call fact ID는 임시
`owner:line:index`를 완전히 대체한다. inactive occurrence도 ledger에 남고, conditional/unknown presence,
expression callee, routine owner 부재, wrapper argument evidence 부재는 각각 명시 unresolved다.
분류 후에도 모든 canonical call ID에 대해 `edge|excluded|preserved|unresolved` 중 정확히 한 outcome을
source order로 남긴다. aggregate CALLS edge나 category count만 맞는 것은 exact-once 승인 근거가 아니다.

Framework call occurrence 원장은 workflow 임시 state에서 끝나지 않는다. 각 canonical call ID는
`CALL_OCCURRENCE` **증거 노드**로 같은 generation에 정확히 한 번 저장하고, enclosing routine이 확정된
경우에만 `HAS_CALL_OCCURRENCE`로 연결한다. 이 노드와 관계는 routine의 의미 소유권을 나누지 않으며,
source/grammar 좌표와 resolution outcome을 보존하는 evidence projection이다. owner가 없는 unresolved도
generation occurrence closure에는 남아야 한다.

저장 행은 최소 `call_id/source_id/caller_id/file/line/range/callee_range/argument_ranges/callee_kind/
terminal_name/presence_status/outcome`과 outcome별 `reason|scope|callee_id`, preprocessing/macro evidence ID를
보존한다. source text를 재파싱하거나 ledger용 summary를 새로 만들지 않는다. generation publish는 canonical
call ID의 중복·누락, owner membership, outcome/reason population, canonical hash를 active pointer 교체 전에
검증한다. unresolved가 하나라도 있으면 run quality는 `completed_with_findings`이며, REST/MCP/search는
generation summary와 owner별 exact occurrence를 `호출 없음`과 구별해 노출한다.

exact decoded source는 envelope에 파일당 한 번만 있으며 Analyzer source cache는 Parser가 hash 검증한
동일 bytes/text 결합을 재사용한다. Analyzer가 별도 UTF-8 decode나 언어별 source lexer를 적용하지 않는다.

DBMS call consumer가 effective AST CodeNode owner를 구현하기 전에는 nonempty DBMS call fact를 받으면
publish를 중단한다. Framework routine owner로 대신 귀속하거나 fact를 보존했다는 이유로 소비를 생략하지
않는다.

### callable declaration/definition과 call binding (`1.3.0`, 역사·소비 금지)

> **2026-08-21 정정:** 이 절은 Parser commit `5dd6410`과 당시 r14 evidence를 재현하기 위한 역사 계약이다.
> 실제 구현은 별도 language adapter가 아니라 `CAstListener.bindCall()`에서 final binding/type/linkage를
> 판정했다. Analyzer product가 이 wire를 소비하기 전에 책임 위반이 확인됐으므로 현행 authority가 아니다.
> Analyzer는 IR 1.3 call binding을 canonical target으로 승격하지 않으며, forward wire는 아래 IR 2.0을 따른다.

IR `1.3.0`은 call occurrence와 target binding을 분리한다. 기존 call fact의 range/callee/argument는
그대로 syntax 정본이고, callable declaration/definition은 별도 `callable` fact다. call fact는 같은
occurrence에 대한 adapter 판정 `payload.binding`을 정확히 하나 가진다. C type-name `symbol` fact는 문법
predicate 진단 전용이므로 이 계약에 재사용하지 않는다.

Callable fact:

```json
{
  "factId": "...",
  "kind": "callable",
  "range": {"charOffset": 0, "charLength": 24},
  "grammarRuleRef": 0,
  "presenceRef": 0,
  "payload": {
    "role": "declaration|definition",
    "nameRange": {"charOffset": 4, "charLength": 6},
    "adapterSchema": "c-source-binding/v1",
    "bindingKey": "64 lowercase hex",
    "compatibilityKey": "64 lowercase hex or null",
    "compatibilityStatus": "exact|configuration_bound|unavailable",
    "compatibilityScope": "source_file|corpus|configuration|unavailable",
    "definitionStatus": "exact|configuration_dependent|not_applicable",
    "targetScope": "source_file|corpus|runtime",
    "configurationId": null,
    "astNodeRange": null,
    "visibilityRange": {"charOffset": 0, "charLength": 100},
    "visibilityStartOffset": 24
  }
}
```

- `bindingKey`와 `compatibilityKey`는 language adapter가 자기 언어의 namespace/scope/type 규칙으로 만든
  불투명 SHA-256이다. Analyzer는 내용을 split/regex/case-fold하지 않고 equality만 검사한다.
- `role=definition`은 compatibility AST CodeNode와 exact `astNodeRange`로 일대일 결합된다.
  declaration-only fact는 `astNodeRange=null`이다. supplied header의 inline/static definition을 파일
  확장자만 보고 버리지 않는다.
- `visibilityRange`와 `visibilityStartOffset`은 declaration이 call-site에서 보이는 source 범위와 선언
  지점을 exact 좌표로 증명한다. Analyzer는 source text를 재파싱해 visibility를 보충하지 않는다.
- `targetScope=source_file`은 같은 source/configuration 안에서만, `corpus`는 corpus 전체에서,
  `runtime`은 정적 aggregate target 없이 occurrence에서만 사용할 수 있다.
- `compatibilityStatus=unavailable`인 callable은 이름이 같아도 internal target 후보가 아니다. Analyzer가
  signature text나 파라미터 수를 재해석해 보충하지 않는다.
- `compatibilityScope=source_file` key에는 source identity가 포함되고 같은 translation unit 안에서만
  비교한다. 그러나 같은 source의 같은 typedef/tag spelling도 shadow/prototype scope 때문에 semantic identity
  증거가 아닐 수 있으므로 raw token equality만으로 이 scope를 발행하지 않는다. 현재 C source adapter는
  grammar AST로 specifier 동치와 parameter top-level qualifier를 완전히 canonicalize한 제한 builtin scalar
  function type만 `corpus` exact로 낸다. typedef/tag/unspecified-parameter/array·function parameter/복합
  declarator/ABI extension은 `unavailable`이다. configured semantic provider가 실제 canonical type과 scope
  identity를 증명할 때만 `source_file|configuration|corpus`로 확장한다.
- `definitionStatus=exact`인 definition만 aggregate target 후보가 된다. declaration은 `not_applicable`이다.
  C의 non-static inline definition은 C99/GNU dialect·declaration/attribute에 따라 external visibility가
  달라지므로 configured trace 없이는 `configuration_dependent`이며 이름이 같다는 이유로 target이 되지 않는다.
- configuration별 semantic trace가 facts를 내면 `configurationId`가 필수다. source-view fact와 configured
  fact를 덮어쓰거나 서로 join하지 않는다.

Call binding payload:

```json
{
  "binding": {
    "adapterSchema": "c-source-binding/v1",
    "status": "declaration_bound|external|configuration_dependent|dynamic|ambiguous|unsupported",
    "resolutionMode": "direct_definition|compatible_definition|none",
    "declarationFactId": "64 lowercase hex or null",
    "bindingKey": "64 lowercase hex or null",
    "compatibilityKey": "64 lowercase hex or null",
    "compatibilityScope": "source_file|corpus|configuration|unavailable or null",
    "targetScope": "source_file|corpus|runtime",
    "dispatch": "exact|virtual|dynamic",
    "configurationId": null,
    "externalIdentityKey": "64 lowercase hex or null",
    "candidateFactIds": [],
    "provenance": "grammar_scope|configured_semantic_trace|runtime_semantics|unsupported_adapter",
    "reason": null
  }
}
```

- `declaration_bound`는 call-site에서 실제로 보이는 declaration/definition을 adapter가 증명한 상태다.
  `declarationFactId`와 `bindingKey`는 필수이며 참조 callable fact와 같아야 한다.
  `resolutionMode=direct_definition`이면 참조 fact 자체가 `role=definition`, `definitionStatus=exact`여야 하고
  Analyzer는 그 fact의 CodeNode만 사용한다. 이때 별도 type 재매칭이 필요 없으므로 compatibility가 unavailable이면
  `compatibilityKey=null`, `compatibilityScope=unavailable`을 허용한다.
  `resolutionMode=compatible_definition`이면 참조 fact는 declaration-only이고 `compatibilityStatus=exact`,
  non-null `compatibilityKey`/지원 scope가 필수다. 나머지 binding status의 mode는 `none`이다.
- `external`은 grammar-known call이지만 현재 분석 입력에서 corpus definition으로 결합할 선언 증거가
  없다는 **제품 경계**다. 부분 업로드의 정상 상태이며 `Unresolved` graph entity가 아니다.
- `configuration_dependent`는 macro expansion, conditional presence, include/build variant 때문에 exact target이
  compile action별로 달라질 수 있는 상태다. 필요한 preprocessing/build evidence ID를 `candidateFactIds`에
  남기며 file 전체의 unrelated call을 같은 상태로 낮추지 않는다.
- `dynamic`은 runtime rebinding/dispatch 없이는 exact target을 정할 수 있음을 완전히 판정한 상태다.
  `ambiguous`는 exact 후보를 둘 이상 증명했으나 하나로 고를 수 없는 상태이고 `candidateFactIds`를
  source order로 전부 보존한다. 정렬상 첫 후보를 선택하지 않는다.
- `unsupported`는 해당 language adapter의 binding capability가 아직 없거나, visible callable은 찾았지만
  exact type/dispatch compatibility를 증명할 수 없다는 명시 상태다. 후자의 경우 판정을 막은 callable
  fact ID를 `candidateFactIds`에 source order로 전부 보존한다. Analyzer의 과거 name-only binder로
  fallback하거나 후보를 버리지 않는다.
- `externalIdentityKey`는 adapter/provider가 collision-free 외부 identity를 증명한 경우에만 존재한다.
  값이 없으면 occurrence는 external이지만 external FUNCTION aggregate node/CALLS edge를 발명하지 않는다.
- `dispatch=virtual|dynamic`은 compile-time declaration 하나를 실제 runtime callee 하나로 승격하지 않는다.
  가능한 target 집합용 별도 검증된 관계 계약이 생기기 전에는 occurrence evidence로만 남긴다.

Analyzer의 language-neutral join은 다음 순서 하나뿐이다.

1. 모든 call fact와 call-binding 판정을 source order로 exact-once 검증한다.
2. `declaration_bound + dispatch=exact + direct_definition`은 `declarationFactId`가 가리키는 exact definition
   CodeNode를 직접 사용한다. `compatible_definition`만 `(adapterSchema, configurationId, targetScope,
   bindingKey, compatibilityScope, compatibilityKey)`로 `definitionStatus=exact` definition fact를 조회한다.
3. direct target 또는 eligible definition과 CodeNode가 정확히 하나면 internal CALLS를 만든다. compatible
   lookup이 0개면 product external,
   2개 이상이면 ambiguous occurrence다. 같은 file 우선이나 ID 정렬 첫 후보는 없다.
4. 나머지 status는 occurrence에 그대로 보존한다. 별도 unresolved node/label/relationship, owner 누적
   property, LLM 보정은 만들지 않는다.
5. occurrence resolution 값은 `internal|external|inactive|configuration_dependent|dynamic|ambiguous|unsupported`
   중 하나다.
   aggregate edge는 그 결과에서 파생되며 occurrence 원장을 대체하지 않는다.

언어 adapter의 최소 책임은 다음과 같다.

- C: ordinary identifier namespace, declaration point/scope, `static` internal linkage와 external linkage,
  function type compatibility, configuration identity와 callable conditional presence를 소유한다. visible
  compatible declaration 없이 다른
  translation unit의 동명 definition만 있는 call은 external이다. direct declaration도 유효하므로 header는
  필수 형식이 아니다. missing compile/include trace가 unconditional source-view call 전체를 막지 않으며,
  macro/conditional 영향을 받는 occurrence만 해당 configuration reason을 가진다.
- Java: receiver/type-to-search, accessibility, overload applicability/most-specific declaration, invocation
  mode를 증명해야 한다. name/arity만으로 `declaration_bound`를 발행하지 않는다. virtual dispatch는
  compile-time declaration과 runtime target을 구분한다.
- Python: lexical block/global/nonlocal/import binding과 call 전까지의 rebinding 가능성을 소유한다.
  대문자 시작, initializer 정규식, 함수 이름만으로 constructor/function target을 확정하지 않는다.
  runtime attribute/descriptor/monkey-patch 가능성이 남으면 `dynamic`이다.

`completeness`에는 `call_binding` row를 별도로 둔다. `population`은 call fact 수와 같고 모든 call에 binding
payload가 있으므로 `emitted=population`이다. `unsupported` 또는 증거 누락이 하나라도 있으면 `partial`과
해당 call fact ID 전수 집합을 남긴다. call syntax `complete`와 binding `partial`을 서로 덮어쓰지 않는다.
`callable` row도 type compatibility unavailable 또는 definition configuration-dependent fact ID 전수를
`unresolvedFactIds`로 보존한다.
IR `1.0.0|1.1.0|1.2.0` call은 binding capability가 없는 legacy input이며 Analyzer는 이를 name-only target으로
승격하지 않는다.

### structural callable/call evidence와 Analyzer semantic resolution (`2.0.0`)

IR 2.0은 Parser의 syntax authority와 Analyzer의 semantic authority를 물리적으로 분리하는 breaking wire다.
IR 1.3의 callable syntax population은 보존하지만 final binding fields는 제거한다.

Envelope에는 파일 확장자로 언어를 추측하지 않도록 다음 identity가 필수다.

```json
{
  "version": "2.0.0",
  "language": "c",
  "frontendSchema": "antlr-c/v1"
}
```

`language`와 `frontendSchema`는 Parser registry가 발행한 closed identity다. Analyzer는 명시 등록된 adapter를
고르기 위해서만 사용하고, 파일 suffix나 source spelling으로 다른 adapter에 fallback하지 않는다.

Call fact는 syntax만 가진다.

```json
{
  "kind": "call",
  "range": {"charOffset": 40, "charLength": 8},
  "payload": {
    "calleeKind": "named|constructor|expression",
    "calleeRange": {"charOffset": 40, "charLength": 3},
    "receiverRange": null,
    "terminalName": "foo",
    "argumentRanges": [],
    "scopePath": [
      {
        "kind": "translation_unit|function|block|function_prototype",
        "range": {"charOffset": 0, "charLength": 100}
      }
    ]
  }
}
```

Callable fact도 syntax만 가진다.

```json
{
  "kind": "callable",
  "range": {"charOffset": 0, "charLength": 24},
  "payload": {
    "role": "declaration|definition",
    "nameRange": {"charOffset": 4, "charLength": 3},
    "astNodeRange": null,
    "scopePath": [
      {
        "kind": "translation_unit|function|block|function_prototype",
        "range": {"charOffset": 0, "charLength": 100}
      }
    ],
    "scopeRange": {"charOffset": 0, "charLength": 100},
    "declarationPoint": 24,
    "syntax": {
      "schema": "c-callable-syntax/v1",
      "declarationSpecifiers": [
        {
          "grammarRule": "declarationSpecifier",
          "range": {"charOffset": 0, "charLength": 3},
          "directTokens": [],
          "children": []
        }
      ],
      "declarator": {
        "grammarRule": "declarator",
        "range": {"charOffset": 4, "charLength": 19},
        "directTokens": [],
        "children": []
      },
      "attributes": []
    }
  }
}
```

- `scopePath`는 별도 scope fact ID를 참조하지 않는다. 같은 file/hash 안의 grammar scope를 바깥에서
  안쪽 순서로 직접 담은 배열이다. 첫 component는 `translation_unit`이며, 뒤 component의 range는 바로
  앞 component range에 포함되어야 한다. call/callable range는 마지막 component range에 포함되어야 한다.
  callable의 `scopeRange`는 이름이 선언되는 binding scope이며 `scopePath`의 마지막 range와 같아야 한다.
- call과 callable의 lexical order는 각각 fact range 시작점과 `declarationPoint`로 결정한다. Parser는
  visible-before-use, shadowing, linkage를 판정하지 않는다.
- `declarationSpecifiers[]`, `declarator`, `attributes[]`의 원소는 같은 재귀 component schema를 쓴다.
  `grammarRule`은 `frontendSchema`가 명명한 ANTLR parser rule, `range`는 해당 context의 exact half-open
  source range, `children[]`는 direct child parser context의 source order다. `directTokens[]`에는 해당
  context의 direct terminal만 다음 형식으로 한 번 기록한다.

```json
{
  "tokenKind": "Identifier|Static|Star|LeftParen|...",
  "range": {"charOffset": 4, "charLength": 3}
}
```

- `tokenKind`는 `frontendSchema`의 lexer vocabulary symbolic name이다. source text, 정규식 분류, canonical
  type/linkage 이름을 Parser payload에 복제하지 않는다. synthetic/recovered token처럼 exact source range가
  없는 component는 complete 사실로 승격하지 않고 envelope parse/completeness 진단에 남긴다.
- C declarator component tree는 storage/type/qualifier/function/alignment specifier, pointer/direct declarator,
  parameter declaration, array/function suffix, variadic marker, attribute context와 terminal을 구조 그대로
  보존한다. parent/child와 source order는 `children[]`로 표현하며 임의 depth/element cap을 두지 않는다.
- `syntax`는 universal type schema가 아니라 frontend별 versioned typed component다. 각 component는 grammar
  kind와 exact half-open range를 보존하며, Analyzer adapter가 source를 regex/split으로 재파싱하지 않게 한다.
- C frontend는 최소 storage/type/qualifier/function/alignment specifier, pointer/direct declarator,
  parameter declaration, array/function suffix, variadic marker, attribute의 grammar kind/range를 보존한다.
- Java frontend는 receiver/type argument/argument/member/constructor syntax와 enclosing scope identity를,
  Python frontend는 Name/Attribute/Call, lexical scope, global/nonlocal/import/assignment syntax를 보존한다.
- grammar가 인식한 새 component를 기존 필드에 억지로 평탄화하지 않는다. frontend schema를 올리고 exact
  range/completeness를 확장한다.
- `payload.binding`, `adapterSchema`, `bindingKey`, `compatibilityKey`, `compatibilityStatus`,
  `compatibilityScope`, `definitionStatus`, `targetScope`, `resolutionMode`, `externalIdentityKey`는 IR 2.0 Parser
  fact에 존재하면 계약 오류다.

Analyzer language semantic adapter의 출력은 Parser wire와 분리된 내부 canonical record다.

```json
{
  "callId": "canonical call fact id",
  "adapterSchema": "analyzer-c-binding/v1",
  "resolution": "internal|external|dynamic|configuration_dependent|ambiguous|unsupported|inactive",
  "declarationFactId": null,
  "targetFactId": null,
  "candidateFactIds": [],
  "configurationId": null,
  "externalIdentityKey": null,
  "reason": "... or null"
}
```

1. Adapter는 Parser structural fact, corpus index, supplied build/classpath/import/runtime trace만 소비한다.
2. actual toolchain/compiler trace가 입력 configuration과 exact하게 결합되면 authoritative다. 다른 toolchain의
   결과로 일반화하지 않는다.
3. source-only adapter는 현재 structural evidence로 증명 가능한 결론만 낸다. 증거가 없으면 이름·arity·대문자·
   stdlib 목록·framework glob으로 메우지 않는다.
4. 분석 입력에서 unique internal target을 증명하지 못하고 동적/모호/configuration/unsupported 원인이 없는
   grammar-known call은 부분 업로드의 정상 `external`이다. collision-free external identity가 없으면 aggregate
   external target node/CALLS edge를 만들지 않는다.
5. common resolution core는 adapter output을 바꾸지 않고 call ID 전수, evidence reference, target 0/1/N,
   configuration 격리, exact-once ledger를 검증한다. internal만 exact target CodeNode에 aggregate CALLS를 만든다.
6. adapter 미등록, structural completeness partial, provider 결손은 좌표와 missing capability가 있는
   `unsupported|configuration_dependent|dynamic`으로 분류하거나 graph mutation 전에 fail closed한다. legacy
   name resolver로 돌아가지 않는다.
7. Framework owner는 enclosing routine, DBMS owner는 effective AST CodeNode다. owner 차이를 binding adapter가
   합치지 않는다.

IR 2.0 completeness는 syntax kind만 회계한다. `call_binding` row는 Parser envelope에서 제거한다.
Analyzer run은 별도 resolution ledger에서 다음 합격식을 강제한다.

```text
Parser call population = Analyzer resolution population
                       = internal + external + dynamic + configuration_dependent
                         + ambiguous + unsupported + inactive
```

누락·중복·미분류 잔차는 0이어야 한다. Parser callable fact는 별도 graph entity가 아니며, Analyzer가 target
선택 근거로 참조할 때 occurrence evidence ID로만 보존한다.

### import/include

IR `1.1.0`의 import/include는 statement 하나에 `payload.entries[]`를 source order로 둔다. 한 statement에
여러 binding이 있어도 fact를 복제하지 않으며, Analyzer occurrence identity는
`(factId, entryOrdinal)`이다.

IR `1.2.0`은 symbol payload를 추가할 뿐 위 structured import payload를 바꾸지 않는다. import consumer와
저장/API/MCP occurrence 모델은 `1.1.0|1.2.0`을 같은 structured import 계약으로 처리해야 하며, 상위
envelope version이라는 이유로 legacy/unresolved 계약으로 낮추거나 거부하지 않는다.

```json
{
  "directiveKind": "include|import",
  "entries": [{
    "importKind": "source_file|computed|type|namespace|static_member|module|module_member",
    "targetKind": "quoted|angle|computed|qualified",
    "targetRange": {"charOffset": 0, "charLength": 1},
    "pathComponentRanges": [],
    "memberRange": null,
    "aliasRange": null,
    "relativeLevel": 0,
    "wildcard": false,
    "locality": "local|system|unspecified"
  }]
}
```

- `target`, path component, member, alias text는 `decodedText`와 exact range에서 복원한다. Parser가 별도
  정규화 문자열을 semantic authority로 발행하지 않는다.
- C include search, Java class/module path, Python import system의 target binding은 grammar syntax fact가
  증명하지 않는다. 검증된 환경-aware binder가 없으면 Analyzer는 basename/path/name 후보를 고르지 않고
  `insufficient_import_binding_environment`로 unresolved 처리한다.
- legacy IR `1.0.0` import는 singular target을 명시적으로 legacy binding 하나로 정규화하되 resolved scope를
  승인할 수 없다. `unsupported|not_applicable|partial`을 legacy AST name으로 보충하지 않는다.
- inactive는 `excluded/inactive_import`, conditional·unknown은 unresolved, computed target은 별도 unresolved다.
  모든 entry가 정확히 한 outcome을 가져야 하며 duplicate/missing/out-of-order entry는 publish 실패다.
- complete ledger는 generation-owned `IMPORT_OCCURRENCE` evidence node로 call ledger와 같은 transaction에
  저장한다. 의미 owner를 새로 발명하거나 source file의 모든 routine에 owner edge를 만들지 않는다.
- generation closure와 REST/search/MCP는 population, outcome/reason, canonical hash를 노출한다. code node
  detail의 import context는 `scope=source_file`인 evidence projection이며 resolved semantic edge 주장이 아니다.
- 향후 authoritative binder가 만든 derived `REFERENCES`는 근거 import occurrence ID와 resolved module ID를
  보존해야 한다. 현재 unresolved import에서 aggregate edge를 먼저 만드는 것은 금지한다.

### C type-name symbol decision (`1.2.0`)

C의 `Identifier`가 typedef-name인지 ordinary identifier인지는 lexer text나 이름 모양이 아니라
translation-unit의 declaration point, lexical scope, configured preprocessing environment로 결정한다.
Parser는 grammar semantic predicate가 실제로 사용한 definition과 lookup을 `symbol` fact로 발행한다.

Definition fact:

```json
{
  "factId": "...",
  "kind": "symbol",
  "range": {"charOffset": 12, "charLength": 1},
  "grammarRuleRef": 0,
  "presenceRef": 0,
  "payload": {
    "role": "definition",
    "symbolKind": "typedef_name|ordinary_identifier",
    "scopeKind": "file|block|function_prototype",
    "scopeRange": {"charOffset": 0, "charLength": 40},
    "visibilityStartOffset": 13
  }
}
```

Lookup fact:

```json
{
  "factId": "...",
  "kind": "symbol",
  "range": {"charOffset": 24, "charLength": 1},
  "grammarRuleRef": 1,
  "presenceRef": 0,
  "payload": {
    "role": "lookup",
    "lookupKind": "type_name",
    "parserDecision": "type_name|ordinary_identifier",
    "resolutionStatus": "resolved|unresolved",
    "provenance": "source_declaration|configured_preprocessing|grammar_context|unresolved_environment",
    "definitionFactId": null,
    "configuredEvidenceId": null,
    "predicateContexts": ["typedef_name"]
  }
}
```

- fact range는 definition/lookup identifier terminal 자체다. 이름 text를 payload에 복제하지 않는다.
- source definition은 identifier를 포함한 declarator가 끝난 직후의 `visibilityStartOffset`과 scope/order를
  grammar action이 소유하고, listener가 declarator terminal과 enclosing file/block/prototype range를 발행한다.
  definition range는 scope 안에 있고 `range.end <= visibilityStartOffset <= scopeRange.end`여야 한다. project
  전체 파일에서 이름을 미리 모으지 않는다.
- `definitionFactId`는 같은 envelope의 definition fact만 참조한다. `source_declaration` resolved lookup은
  반드시 이 ID를 가지며 lookup 위치가 definition의 선언 지점 이후이면서 scope range 안이어야 한다.
- `configured_preprocessing` resolved lookup은 compile-action별 authoritative expanded-token/source-map
  SHA-256 evidence ID를 `configuredEvidenceId`로 참조하고 `definitionFactId`는 null이어야 한다. trace가
  없으면 이 provenance를 사용할 수 없다.
- `grammar_context` resolved lookup은 앞선 builtin/type specifier처럼 현재 parse rule 자체가 ordinary
  declarator 결정을 완전히 닫을 때만 사용하며 definition/configured evidence ID는 모두 null이다.
- environment가 닫히지 않은 상태에서 semantic predicate가 선택을 요구한 unknown identifier는 parser가
  compatibility tree에서 택한 `parserDecision`과 별개로 `resolutionStatus=unresolved`,
  `provenance=unresolved_environment`, `definitionFactId=null`이다. ordinary identifier로 확정하지 않는다.
- 같은 token에 predicate가 여러 번 평가돼도 symbol lookup fact는 token range+decision당 하나이며
  `predicateContexts`를 중복 없이 grammar evaluation order로 합친다. speculation/retry 수를 occurrence로
  부풀리지 않는다.
- declaration/statement 또는 cast/expression 대안 비교는 같은 source token의 grammar parse 결과로만
  수행한다. unknown 이름 전체를 type으로 seed하지 않고 현재 candidate identifier 좌표 하나만 대안 입력으로
  바꾸며, 양쪽이 동일하게 완주하면 어느 쪽도 exact environment truth로 승격하지 않는다.
- 미해소 첫 identifier 때문에 ANTLR recovery가 뒤 identifier에도 type-name predicate를 실제 평가했다면
  서로 다른 source token decision이므로 각각 fact로 남긴다. 이 후속 fact를 사람이 예상한 타입명 목록에
  맞춰 숨기지 않으며, 반대로 canonical symbol meaning으로 승격하지도 않는다. envelope `parseStatus=partial`과
  symbol unresolved ledger가 recovery 영향을 함께 드러내야 한다.
- header/POSIX/Windows/library typedef, macro(`NULL` 포함), 고객 naming suffix를 language builtin type으로
  하드코딩하지 않는다. C keyword/builtin terminal은 grammar token/rule이 직접 소유하므로 symbol lookup
  seed가 아니다.
- symbol completeness는 관측된 definition/lookup fact 전부를 `population=emitted`로 회계한다. unresolved
  lookup이 있으면 `status=partial`, reason=`insufficient_type_name_environment`이고 같은 envelope의 unresolved
  lookup만 `unresolvedFactIds`로 전수 참조한다. 알 수 없는 occurrence 수를 `explicitlyUnresolved`로 발명하지
  않는다.
- 분석 대상에 definition이 없는 것은 정상적인 부분 업로드 경계다. call/tag처럼 grammar role이 이미
  확정된 사용은 결정론 linker가 corpus definition 유무에 따라 내부 target 또는 제품 external로 분류한다.
  typedef 여부가 문법 자체를 바꾸는 경우의 `unresolved_environment`는 그 target 분류가 아니라 Parser AST
  JSON의 parse/evidence 진단이다. 이를 `Unresolved`나 `external_unbound` 도메인 entity로 승격하지 않는다.
- C의 `external linkage`와 Analyzer의 `external target`은 다른 개념이다. 현재 corpus에 다른 translation
  unit의 unique compatible function/object definition이 있고 call-site에 보이는 declaration이 그 definition과
  external linkage로 결합되면 Analyzer에서는 내부 사용자 정의 target이다. header include는 declaration 전달의
  일반적 수단일 뿐 필수 형식이 아니며 직접 declaration도 가능하다. 반대로 `static` internal linkage,
  incompatible/duplicate definitions, declaration 부재를 이름만으로 project-global 결합하지 않는다.
- typedef name에는 linkage가 없다. 다른 `.c`에 같은 typedef definition이 있다는 이유만으로 현재 translation
  unit의 type environment나 내부 binding이 되지 않는다. configured include/preprocessing으로 현재 unit에
  실제 유입된 declaration만 사용할 수 있다.
- Parser source envelope의 exact definition/lookup fact와 parse/completeness 진단이 이 경계의 정본이다.
  Analyzer는 IR population·ID·range·status를 strict validation하고, syntax-dependent AST/owner가 닫히지 않은
  분석 단위는 좌표가 있는 run finding으로 격리하거나 fail-closed한다. compatibility AST를 canonical exact로
  승격하지 않는다.
- 이 symbol 진단을 Neo4j node/label/relationship 또는 기존 owner의 누적 property ledger로 저장하지 않으며
  API/MCP 도메인 객체로도 투영하지 않는다. 확정된 함수·object의 cross-translation-unit 연결은 별도
  결정론 linker가 declaration/linkage/type/definition 근거로 처리한다.
- LLM은 승인된 owner의 exact AST/source/evidence를 받아 별도 natural-language semantic frame만 채운다.
  symbol definition/lookup, AST node/type, linkage, CALL/REFERENCE를 만들거나 변경할 수 없다.

- 각 문법 단위 exact range와 file-level source/hash binding
- 일반 symbol role의 향후 definition/reference/import/read/write 확장은 exact grammar terminal과 별도
  versioned payload를 요구한다. IR 1.2의 현재 승인 symbol payload는 위 C `definition|lookup` type-name
  decision뿐이며 project target resolution은 Analyzer 소유다.
- literal은 raw segments와 concat/escape decode status를 분리한다. decode가 불완전하면 raw만 canonical이다.
- assignment는 target/source expression range를 분리한다.
- macro는 object/function-like, parameter, replacement token range, presence를 보존한다.

### embedded-language

- host segment range와 decoded-to-host range mapping
- declared/detected language와 parse status
- SQL 등 embedded parser의 자체 versioned facts

## 7. conditional C contract

- 검증된 compile action이 있으면 action hash가 `configurationId`이며 그 action별 concrete facts를 분리한다.
- 동일 file의 여러 compile action은 merge하지 않고 configuration별 occurrence set을 보존한다.
- configuration이 없으면 variability-aware provider가 presence-conditioned facts를 생산한다.
- provider가 없으면 conditional region 전체를 exact fact로 남기고 영향 owner completeness를
  `insufficient_missing_build_configuration`으로 표시한다. 성공처럼 publish하지 않는다.
- constant expression도 동일 evaluator를 거친다. `#if 0/1` 특례 문자열 patch는 금지다.

## 8. completeness matrix

IR 1.x 역사 envelope는 파일마다 `call|call_binding|callable|import|symbol|literal|assignment|parameter|macro|
embedded_language|conditional_region` 각 kind/capability를 기록했다. IR 2.0 Parser envelope는 final semantic
capability인 `call_binding`을 제거하고 syntax kind만 `complete|not_applicable|partial|unsupported`와
`population/emitted/explicitlyUnresolved`로 기록한다. binding population은 Analyzer run resolution ledger가
별도로 소유한다.

```text
population = emitted + explicitlyUnresolved
```

이 등식과 exact ID set hash가 맞지 않으면 publish 실패다. 지원하지 않는 언어/문법을 빈 배열로 성공 처리하지
않는다. Framework와 DBMS owner 차이는 유지하고, consumer는 필요한 kind가 complete가 아니면 해당 의미를
`partial/insufficient`로 표시한다.

`reasons[]`가 있으면 호환 `reason`은 반드시 첫 항목과 같아야 한다. `unresolvedScopeFactIds`는 같은
envelope에 존재하는 `conditional_region` fact만 참조할 수 있다. 다른 kind의 fact ID나 외부 ID를 scope로
가장하면 consumer는 publish를 거부한다.

IR `1.2.0`의 `unresolvedFactIds`는 해당 completeness row와 같은 kind의 fact만 참조한다. symbol row에서는
`resolutionStatus=unresolved` lookup fact의 전수 집합과 정확히 같아야 하고, resolved/definition/다른 kind ID가
섞이거나 누락·중복되면 publish를 거부한다.

조건부 region은 call occurrence와 단위가 다르므로 `explicitlyUnresolved` call로 더하지 않는다. 관측된
source call occurrence는 그대로 `population=emitted`로 회계하고, configuration 때문에 active set이
미확정이면 status를 `partial`로 두며 `unresolvedScopeFactIds`에 해당 conditional-region fact ID 전부를
기록한다. 알 수 없는 call 수를 숫자로 발명하지 않는다.

strict decode가 실패하거나 parser diagnostic/recovery가 있으면 call completeness도 각각
`insufficient_lossy_decode`, `insufficient_parser_recovery` reason을 가진 `partial`이다. 여러 원인이 동시에
있으면 `reasons[]`에 모두 보존하고 `reason`은 호환용 첫 원인이다. recovery fragment의 local offset을
canonical full-source fact로 가장하지 않는다.

## 9. 금지

- Analyzer source regex/lexer로 missing fact 복원
- 원문 요약을 다음 LLM의 권위 입력으로 사용
- 이름/라인만으로 occurrence ID 생성
- offset encoding 생략 또는 Java UTF-16/Python code point 혼용
- first branch 선택, sample/Top-N, 숫자 cap으로 conditional/configuration 공간 은닉
- unknown/unsupported/partial을 empty success로 변환

## 10. 승인 검증

1. 자동 회귀: multiline/nested/unicode/escaped/named argument, same-line duplicate, recovery rebase,
   C conditional 반례와 모든 지원 언어 matrix.
2. 결정론 전수 회계: actual corpus source↔facts population/count/hash, exact-once merge.
3. 봉인 직접 의미 판정: source range를 숨긴 상태에서 fact만으로 판정한 뒤 source를 열어 전수/표본 대조.
4. actual replay: Framework routine, DBMS effective AST owner별 Neo4j/API/MCP consumer까지 일치.
5. 성능: baseline 대비 parser/analyzer wall time과 peak memory. 악화 시 streaming/index 자료구조를 개선하되
   fact population을 자르지 않는다.
