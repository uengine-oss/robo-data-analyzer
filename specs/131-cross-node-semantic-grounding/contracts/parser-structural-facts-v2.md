# Parser Structural Facts v2 계약

**Status**: Active — implemented structural wire; current acceptance is owned by `spec.md` and `CURRENT_STATUS.md`
**Requirements**: `SDD-PAR-001~009`, `SDD-DET-003~005`
**Implementation task**: `T131-N02`, `T131-N05`

이 계약은 Parser가 언어별 구문·구조를 손실 없이 내보내고 Analyzer가 final 의미 판정을 소유하게 하는
현행 경계다. 구조 wire 구현과 회귀 범위, 실제 제품 검증의 완료·미검증 상태는 `CURRENT_STATUS.md`가
소유한다. 과거 v1 wire가 이 계약과 충돌하면 실행 정본으로 사용하지 않는다.

## 1. 책임

- Parser는 decode, token/grammar range, AST tree/order, 조건부 존재, 문법 대안, 선언·사용·호출의 구조만 소유한다.
- Parser는 binding, type compatibility, linkage 결과, internal/external, standard/framework 분류, graph edge,
  업무 의미를 생산하지 않는다.
- Analyzer language semantic adapter는 이 wire와 봉인된 build/classpath/import/runtime 자료를 입력으로
  language-neutral 후보를 만든다.
- Analyzer resolution core가 final binding/type/linkage와 call disposition을 확정한다.
- Analyzer는 누락 fact를 source regex/lexer/split/괄호 세기로 복원하지 않는다.

## 2. 파일당 최상위 artifact

```yaml
schema: parser-structural-facts/v2
source:
  sourceId: source/apascrtu0010t02.c
  rawSourceSha256: <64 lowercase hex>
  decodedTextSha256: <64 lowercase hex>
  sourceEncoding: UTF-8
  decodeStatus: exact
  positionEncoding: unicode-code-point
  rangeConvention: half-open
  rangeEncoding: char-offset-length
parser:
  language: c
  frontendSchema: antlr-c/<sealed version>
  configurationVersion: <sealed parser configuration version>
  parseStatus: complete
  configuredPreprocessing: null
ast:
  root: <AstNode 전체 객체>
structuralFacts:
  presences: [<Presence 전체 객체>]
  facts: [<StructuralFact 전체 객체>]
  completeness: [<FactCompleteness 전체 객체>]
```

필드 규칙:

- `sourceId`는 분석 source root 기준 `/` 정규화 상대경로다. absolute machine path는 금지한다.
- 원문 text는 `SealedAnalysisInputs`의 source blob이 소유한다. Parser artifact에 같은 text를 복제하지 않는다.
- Analyzer는 `sourceId + rawSourceSha256 + decodedTextSha256`가 봉인 source blob과 모두 같을 때만 range를 사용한다.
- `decodeStatus!=exact` 또는 `parseStatus!=complete`인 required source는 graph 쓰기 전 terminal failure다.
- `configuredPreprocessing`은 실제 build/trace로 봉인했을 때만 non-null이다. 파일명·macro 이름 추측으로 만들지 않는다.
- Parser artifact 재사용 key는 정확히 `(schema, source.sourceId, source.rawSourceSha256,
  source.decodedTextSha256, parser.language, parser.frontendSchema, parser.configurationVersion)`다. 일곱 값이
  모두 byte-for-byte 같을 때만 재사용하며, frontend 또는 configuration version이 하나라도 다르면 그
  source를 다시 파싱한다. artifact 파일 hash나 raw source hash 하나만 같은 것은 재사용 근거가 아니다.

## 3. 공통 range

```yaml
charOffset: 40
charLength: 8
```

- offset은 decoded Unicode code point 기준 0부터 시작한다.
- end는 `charOffset + charLength`이며 포함하지 않는다.
- CR/LF도 decoded source에 실제 존재한 code point로 센다.
- 모든 range는 decoded source 길이 안에 있어야 하고 해당 slice는 봉인 blob에서만 복원한다.
- line/column은 Analyzer의 source line index가 결정론적으로 계산하며 wire에 반복하지 않는다.

## 4. AST node

```yaml
astNodeId: <아래 canonical tuple의 SHA-256 lowercase hex>
nodeKind: call_expression
range: {charOffset: 40, charLength: 8}
syntaxProperties: {}
children: [<AstNode 전체 객체>]
```

`astNodeId`의 canonical 입력 순서는 다음과 같다.

```text
parser-ast-node/v2
sourceId
rawSourceSha256
decodedTextSha256
nodeKind
charOffset
charLength
ordinalWithinSameNodeKindAndRangeInGrammarOrder
```

각 원소는 UTF-8 문자열이다. 정수는 앞자리 0 없는 ASCII 10진수로 만들고, 각 원소를
`uint64 big-endian UTF-8 byte length || UTF-8 bytes`로 length-prefix한 뒤 순서대로 연결하여 SHA-256을
계산한다. namespace와 `sourceId`가 tuple에 있으므로 occurrence ID나 같은 bytes의 다른 파일과 충돌하지 않는다.

- `children` 배열은 grammar source order다. 같은 child를 둘 이상의 parent에 넣지 않는다.
- parent ID와 scope path는 root부터 children을 따라 만든 Analyzer index에서 계산하므로 wire에 복제하지 않는다.
- `syntaxProperties`는 `frontendSchema`가 닫힌 schema로 선언한 문법 property만 허용한다. 자유 형식 `extra`는 금지한다.
- 사용자 자료에서 온 동적 map key가 허용된 property 안에 있으면 byte-identical하게 보존한다. key adapter가
  camel/snake case로 바꾸면 안 된다.

## 5. 공통 구조 사실

```yaml
occurrenceId: <아래 canonical tuple의 SHA-256 lowercase hex>
kind: call
range: {charOffset: 40, charLength: 8}
astNodeId: <같은 구문 발생 AST node ID>
presenceRef: 0
payload: <kind별 payload>
```

`occurrenceId`의 canonical 입력은 위와 같은 정수·length-prefix 인코딩을 쓰는 다음 tuple이다.

```text
parser-structural-occurrence/v2
sourceId
rawSourceSha256
decodedTextSha256
kind
charOffset
charLength
ordinalWithinSameKindAndRangeInGrammarOrder
```

같은 content를 가진 서로 다른 두 파일도 `sourceId`가 다르므로 충돌하지 않는다. 같은 파일·revision·grammar
발생은 batch/concurrency와 무관하게 같은 ID다.

## 6. Presence

```yaml
status: active
condition: null
provenance: parser_default
configurationId: null
```

허용 상태는 `active|inactive|conditional`이다. `configurationId` field는 항상 존재하며 실제 configured
build/trace가 발행한 정체성이 없으면 null이다. conditional을 임의 active/inactive로 고르지 않는다.

configured preprocessing 전체 객체:

```yaml
version: <sealed version>
status: complete
trust: build
configurationId: <actual build/trace identity>
occurrenceIds: [<판정 대상 occurrence ID>]
evidenceIds: [<actual build/trace artifact ID>]
emittedOccurrenceIds: [<active occurrence ID>]
explicitlyUnresolvedOccurrenceIds: []
unresolvedReasonCodes: {}
```

## 7. 필수 fact kind와 payload

### 7.1 `call`

```yaml
calleeRange: {charOffset: 40, charLength: 3}
receiverRange: null
calleeKind: named
terminalName: run
argumentRanges:
  - {charOffset: 44, charLength: 3}
scopePath:
  - {kind: translation_unit, range: <전체 파일 Range>}
calleePathRanges:
  - {charOffset: 40, charLength: 3}
databaseLinkRange: null
```

- fact range는 전체 call expression이다.
- `calleeRange`는 호출 대상 표현 전체다.
- `receiverRange`는 member call receiver이며 named call이면 null이다.
- `calleeKind`는 `named|constructor|expression` 중 grammar가 고른 값이다.
- `terminalName`은 named/constructor의 grammar terminal text이며 expression이면 null이다.
- `argumentRanges` 순서는 grammar 인자 순서다.
- `scopePath`는 `translation_unit|package|class|function|lambda|comprehension|block|function_prototype`의
  source-backed lexical 경로다.
- Oracle처럼 qualified callable 구조가 의미 연결에 필요한 frontend는 `calleePathRanges`를 component
  source order로 생산하고 `databaseLinkRange`를 항상 함께 둔다. DB link가 없으면 null이다. 마지막
  `calleePathRanges` 원문은 `terminalName`과 정확히 같아야 한다.
- callee/receiver/argument text를 wire에 복제하지 않는다.
- `PFM_TRY(inner(ctx))`는 바깥 call과 안쪽 call을 서로 다른 AST node·occurrence·range로 보존한다.

### 7.2 `callable`

```yaml
role: definition
nameRange: {charOffset: 4, charLength: 6}
namePathRanges:
  - {charOffset: 0, charLength: 3}
  - {charOffset: 4, charLength: 6}
declarationPoint: 24
syntax:
  schema: c-callable-syntax/<sealed version>
  declarationSpecifiers: [<SyntaxComponent 전체 객체>]
  declarator: <SyntaxComponent 전체 객체>
  attributes: [<SyntaxComponent 전체 객체>]
```

재귀 `SyntaxComponent`:

```yaml
grammarRule: directDeclarator
range: {charOffset: 4, charLength: 19}
directTokens:
  - tokenKind: Identifier
    range: {charOffset: 4, charLength: 6}
children: [<SyntaxComponent 전체 객체>]
```

- `role`은 `declaration|definition`이다.
- qualified 정의를 구분해야 하는 frontend는 `namePathRanges`에 schema/package/member 구성요소를 source
  order로 생산한다. 마지막 range는 `nameRange`와 정확히 같아야 하며, package member의 앞 구성요소는
  enclosing package header에 있을 수 있다.
- Parser는 visibility, shadowing, linkage, compatible definition을 판정하지 않는다.
- `grammarRule`과 `tokenKind`는 frontend grammar/lexer vocabulary의 실제 이름이다.
- 임의 depth/child cap은 금지한다.

### 7.3 `structural_expression`

```yaml
role: condition
ownerRange: {charOffset: 100, charLength: 42}
syntax:
  root:
    nodeKind: binary_expression
    operator: "!="
    range: {charOffset: 104, charLength: 30}
    children: [<표현식 child 전체 객체>]
```

허용 role은 frontend contract가 선언한 `condition|assignment_target|assignment_value|return_value|argument|
initializer`의 부분집합이다. expression text나 Python 표기를 Parser가 만들지 않는다.

### 7.4 `symbol_declaration`

```yaml
role: definition
symbolKind: function
scopeKind: translation_unit
scopeRange: {charOffset: 0, charLength: 4000}
declarationEndOffset: 24
```

`role`은 `definition|declaration|parameter|field`, `symbolKind`와 `scopeKind`는 frontend별 닫힌 enum이다.
Parser는 이 fact가 어떤 use의 target인지 결정하지 않는다.

### 7.5 `symbol_use`

```yaml
referenceRange: {charOffset: 120, charLength: 13}
terminalNameRange: {charOffset: 124, charLength: 9}
referenceForm: member
receiverRange: {charOffset: 120, charLength: 3}
syntacticContext: value
```

`referenceForm`은 `identifier|member|qualified|type_name`, `syntacticContext`는
`value|assignment_target|call_target|type|label`이다. binding 결과 필드는 금지한다.

### 7.6 `type_reference`

```yaml
typeRange: {charOffset: 10, charLength: 12}
terminalNameRange: {charOffset: 10, charLength: 12}
referenceForm: named
qualifierRanges: []
syntacticContext: declaration
```

`referenceForm`은 `builtin|named|qualified|generic|pointer|array|function`, `syntacticContext`는
`declaration|cast|extends|implements|generic_argument|sizeof`다.

### 7.7 `grammar_disambiguation`

```yaml
ambiguityKind: typedef_name_or_identifier
chosenGrammarAlternative: <frontend enum>
disambiguationStatus: selected
provenance: grammar
supportingTokenRanges: [<Range 전체 객체>]
configuredEvidenceId: null
predicateContexts: []
```

이 값은 grammar 대안 선택이지 project symbol binding이 아니다. unresolved/conditional이면 completeness와
configured 근거에 정확히 연결한다.

### 7.8 `import`

```yaml
directiveKind: include
scopePath:
  - {kind: translation_unit, range: <전체 파일 Range>}
entries:
  - importKind: source_file
    targetKind: quoted
    targetRange: {charOffset: 1, charLength: 12}
    pathComponentRanges: []
    memberRange: null
    aliasRange: null
    relativeLevel: 0
    wildcard: false
    locality: local
```

한 statement의 여러 entry는 source order 배열로 한 fact에 둔다. `scopePath`는 import statement가 속한
정확한 lexical 구조이며 Python의 함수 내부 import도 `translation_unit → function → block`을 잃지 않는다.
Parser는 여기서 어떤 이름이 실제로 결속되는지 또는 어느 file/module을 가리키는지 결정하지 않는다. 그
판정은 Analyzer가 언어 규칙과 build/classpath/import 환경으로 수행한다.

### 7.9 `macro`

```yaml
macroKind: function_like
nameRange: {charOffset: 1, charLength: 7}
parameterRanges: [<Range 전체 객체>]
replacementTokenRanges: [<Range 전체 객체>]
invocationRange: null
```

macro replacement 의미와 실행 결과를 Parser가 추측하지 않는다. 실제 configured expansion trace는
`configuredPreprocessing`이 별도 소유한다.

### 7.10 `parameter`

Python function과 lambda의 parameter는 이름 token 자체를 fact range로 쓰고 payload에는 lexical
`scopePath`만 둔다. type 추론, 호출 가능 여부, 최종 binding 대상은 넣지 않는다.

```yaml
kind: parameter
range: {charOffset: 8, charLength: 5}
payload:
  scopePath:
    - {kind: translation_unit, range: <전체 파일 Range>}
    - {kind: function, range: <함수 Range>}
```

### 7.11 `binding_target`

Python 대입문·주석 대입·증감 대입·`for`·`with ... as`·`except ... as`·comprehension의 target은
각 문법 target마다 정확히 한 fact를 만든다. Parser는 target 범위와 문법 tree, 발생 문맥, lexical
scope만 생산한다. 그 target이 지역 이름을 묶는지, 객체 속성이나 인덱스를 변경하는지는 Analyzer가
결정한다.

```yaml
kind: binding_target
range: {charOffset: 39, charLength: 11}
payload:
  bindingContext: assignment
  scopePath:
    - {kind: translation_unit, range: <전체 파일 Range>}
    - {kind: function, range: <함수 Range>}
  syntax:
    schema: python-binding-target-syntax/v1
    root: <range와 일치하는 grammar component tree>
```

`bindingContext`는
`assignment|annotated_assignment|augmented_assignment|for_target|with_target|except_target|comprehension_target|function_definition|class_definition|delete_target`
중 하나다. function/class 정의 이름은 새로 만들어지는 본문 scope가 아니라 그 정의를 결속하는 바깥 lexical
scope에 속한다. comprehension container는 별도 `comprehension` scope로 보존한다. 단, Python에서 가장
바깥 `for ... in <iterable>`의 iterable은 comprehension의 암시적 실행 scope에 들어가기 전에 평가되므로 그
하위 call의 `scopePath`에는 해당 comprehension scope를 넣지 않는다. Parser는 여기서 bound name,
local/global/nonlocal, resolved symbol을 만들지 않는다.

### 7.12 `scope_directive`

Python `global`과 `nonlocal` statement에 나열된 각 이름은 독립 fact다. fact range는 이름 token 자체이며
payload는 원문에 적힌 directive 종류와 lexical scope만 가진다.

```yaml
kind: scope_directive
range: {charOffset: 84, charLength: 7}
payload:
  directiveKind: nonlocal
  scopePath:
    - {kind: translation_unit, range: <전체 파일 Range>}
    - {kind: function, range: <바깥 함수 Range>}
    - {kind: block, range: <바깥 함수 body Range>}
    - {kind: function, range: <현재 함수 Range>}
```

`directiveKind`는 `global|nonlocal` 중 하나다. Parser는 이름이 실제 어느 module 또는 enclosing function
binding을 가리키는지 결정하지 않는다.

## 8. Completeness

```yaml
kind: call
status: complete
populationOccurrenceIds: [<grammar listener가 방문한 전체 occurrence ID>]
unresolvedOccurrences: []
```

unresolved 행 전체:

```yaml
occurrenceId: <population과 같은 방식의 ID>
range: {charOffset: 0, charLength: 1}
reasonCode: <contract enum>
```

각 kind 합격식:

```text
populationOccurrenceIds
= emitted fact occurrenceIds ⊎ unresolved occurrenceIds
```

- 두 disposition은 서로소여야 한다.
- required kind는 `status=complete`와 unresolved 0을 요구한다.
- 문법상 발생 0개인 complete와 adapter 미지원 empty를 구분한다.
- fact kind별 count/hash는 위 ID 집합에서 계산하며 별도 권위 숫자를 두지 않는다.

## 9. Parser wire에서 금지하는 의미 필드

다음 필드 또는 같은 의미의 별칭이 Parser fact에 있으면 계약 위반이다.

```text
binding, bindingKey, compatibilityKey, compatibilityStatus, linkage,
resolutionStatus, resolutionOutcome, internal, external, standardLibrary,
frameworkProvided, resolvedCalleeId, candidateDefinitionIds,
tableId, businessMeaning, summary, rule, confidence
```

Parser의 legacy AST를 포함한 모든 출력은 식별자 대소문자나 원문 정규식으로 callable 종류·생성자 여부·
변수 타입을 추측하지 않는다. 예를 들어 Python의 `HTTP()`와 `lower()`는 문법만으로 함수 호출인지
class 생성인지 구분할 수 없으므로 둘 다 call 구조와 정확한 범위만 생산한다. type/binding 환경을 가진
Analyzer가 최종 판정한다.

## 10. Analyzer 경계 출력

Parser의 실행 가능한 출력은 §2의 `parser-structural-facts/v2` artifact에서 끝난다. 언어 adapter 후보,
symbol/call resolution, standard/framework/external 분류는 Parser wire의 일부가 아니며 별도 camelCase
복사본을 여기 두지 않는다.

Analyzer 내부 canonical wire의 단일 소유자는
`analyzer-meaning-and-publication-v2.md` §1.4다.

```text
LanguageSemanticCandidateSet  → language_semantic_candidate_set
CallResolutionLedger          → call_resolution_ledger
ExternalReferenceLedger       → external_reference_ledger
```

경계 합격 조건:

1. Parser schema property는 기계 adapter가 `lower_snake_case`로 전수 변환하되 값·배열 순서·null과
   source/occurrence ID 같은 동적 data key는 byte-for-byte 보존한다.
2. Analyzer candidate artifact는 `adapter_schema_version`과 `adapter_implementation_id`를 모두 보존한다.
3. symbol/call candidate source occurrence 집합은 Parser required population과 exact equality다.
4. call 결과는 모든 Parser call occurrence에 9-state 중 하나를 정확히 한 번 배정하며, internal pair는
   internal 행에서만 결정론 투영한다.
5. standard/framework registry 분류는 shadowing 후 선택된 callable identity와 봉인 registry version을
   사용한다. terminal name matching은 금지한다.
6. external reference 결과는 linked/unresolved/rejected_not_external_reference의 exact partition이며 linked만
   REFER_TO 후보가 된다.
7. occurrence 전체와 후보·이유는 run artifact에 남기고 unresolved/call-occurrence graph node는 만들지 않는다.

## 11. 승인 검증

1. multiline, nested, same-line duplicate, Unicode, escape, recovery, conditional, shadowing fixture 자동 회귀
2. source/AST/fact population과 Analyzer resolution population exact-once 전수 회계
3. small actual SKT source에서 range slice·nested call·owner·9-state 직접 대조
4. source regex/lexer/name fallback 정적·동적 호출 0 검증
5. 봉인한 SKT 2파일 1,503줄 run에서 identity·hash·memory/time을 한 번 측정하되 선택 파일의 fact
   population을 자르지 않음. 실행하지 않은 전체 SKT 성능은 미검증으로 남김
