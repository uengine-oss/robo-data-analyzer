# Common Evidence IR v1 계약

## 1. 목적과 owner

이 계약은 source syntax를 언어별 Parser frontend가 한 번만 판정하고 Analyzer가 언어 중립 evidence만
소비하게 하는 wire boundary다. 하나의 AST shape로 모든 언어 문법을 평탄화하지 않는다.

- Framework semantic owner: enclosing routine.
- DBMS semantic owner: effective AST `CodeNode`.
- Parser owner: source decoding, grammar/token boundary, conditional presence, exact occurrence.
- Analyzer owner: project symbol resolution, framework/DB object resolution, owner mapping, aggregate graph 파생.
- Scan/Deep Agent owner: 검증할 fact-field/relationship pattern 제안. source syntax truth 생산 금지.

기존 `FILE.children` AST는 migration 동안 유지하지만 canonical 소비자는 versioned `evidence.facts`다.

## 2. file envelope

모든 accepted AST `FILE` root는 `evidence` object를 가진다.

```json
{
  "evidence": {
    "version": "1.0.0",
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

### import/include, symbol, literal, assignment, parameter, macro

- 각 문법 단위 exact range와 file-level source/hash binding
- symbol role은 `definition|reference|import|read|write`; target resolution은 Analyzer 소유
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

파일마다 `call|import|symbol|literal|assignment|parameter|macro|embedded_language|conditional_region` 각 kind에
대해 `complete|not_applicable|partial|unsupported`와 `population/emitted/explicitlyUnresolved`를 기록한다.

```text
population = emitted + explicitlyUnresolved
```

이 등식과 exact ID set hash가 맞지 않으면 publish 실패다. 지원하지 않는 언어/문법을 빈 배열로 성공 처리하지
않는다. Framework와 DBMS owner 차이는 유지하고, consumer는 필요한 kind가 complete가 아니면 해당 의미를
`partial/insufficient`로 표시한다.

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
