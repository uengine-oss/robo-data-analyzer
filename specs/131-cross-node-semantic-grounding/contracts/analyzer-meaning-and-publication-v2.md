# Analyzer Meaning and Publication v2 계약

**Status**: Active — implemented Analyzer wire; current acceptance is owned by `spec.md` and `CURRENT_STATUS.md`
**Requirements**: `SDD-DET-001~011`, `SDD-LLM-001~015`, `SDD-GRA-001~011`, `SDD-PERF-001~005`, `SDD-VER-001~004`
**Implementation tasks**: `T131-N01`, `T131-N03~N04`, `T131-N06~N17`

이 계약은 `parser-structural-facts-v2.md`가 넘긴 언어별 구조 사실을 Analyzer가 의미 후보·연결·모델 입력·제품 graph로 바꾸는 현행 실행 계약이다. 구현·실행의 완료와 미검증 상태는 `CURRENT_STATUS.md`가 소유한다. 과거 v1 계약과 충돌하면 이 계약과 `spec.md`가 우선하며, 작업보고 HTML은 아래 wire를 실제 값과 쉬운 설명으로 보여 주는 사람용 문서다.

## 1. 소유권과 실행 가능한 생산자

### 1.1 Parser가 끝낸 뒤 Analyzer가 소유하는 결정

Analyzer만 다음을 결정한다.

- 언어별 visibility·scope·import·type·linkage 사실을 language-neutral candidate로 바꾸는 일
- call과 symbol use의 최종 binding, physical table/column, 외부 record 연결
- `internal | external | standard_library | framework_provided | configuration_dependent | ambiguous | dynamic | inactive | unsupported` disposition
- 함수별 원문·작은 구조 지도·외부 값·확정 table·callee effect capsule을 중복 없이 조립하는 일
- Summary, FunctionTableUsage, TableDescription, Rule 결과의 구조·근거·완전성 검증
- Example, Module rollup, Component, 검색 segment 및 제품 graph 투영
- exact-once ledger와 generation 공개 여부

Parser 구조 사실에서 누락된 의미를 source 정규식·문자열 분할·이름 추측으로 복원하면 계약 위반이다.

### 1.2 허용되는 generative family의 닫힌 목록

```text
unknown project analysis adapter proposal
unknown external-document adapter proposal
unmatched table-access pattern proposal
Function Summary
FunctionTableUsage
TableDescription
Business Rule atom meaning and row classification
```

다음은 LLM 0회다.

```text
call/symbol/table/external final resolution
Example construction
Module rollup
Component construction
Glossary ingestion
search segment construction and embedding input construction
graph projection, write, verification, publication
all exact-once and performance ledgers
```

같은 concern을 hidden judge, repair, second meaning pass, follow-up agent, tool loop로 다시 묻지 않는다. transport 계층이 서버가 request bytes를 받지 않았음을 증명할 때만 동일 invocation ID로 재전송할 수 있다.

### 1.3 모델·runtime 능력도 입력과 함께 봉인한다

큰 함수 분할, token 계산, KV admission, response cache는 실행 중 live 조회값이나 모델 별칭을 사용하지 않는다.
시작 때 다음 content-addressed 정본을 한 번 만들고 `SealedAnalysisInputs`가 hash와 ref를 소유한다.

`sealed-analysis-inputs.json`은 다음 입력군을 하나의 canonical SHA-256으로 묶는다.

```text
analysis_request                  선택 source ID·전략·locale·datasource·기존 Neo4j database
analysis_scope_files              실제 분석 범위 source와 소비 가능한 외부 프로젝트 파일
ddl_files                         DDL 원문 bytes
source_ast_closure                source↔AST↔diagnostic exact-once 대응표
llm_configuration                선택된 YAML bytes와 검증된 유효값
effective_analyzer_settings       secret을 제외한 실제 실행 설정
prompt_contracts                  실제 prompt·공유 계약 bytes
analyzer_contracts               현재 SDD 계약 bytes
analyzer_implementation          실행 코드·고정 ontology·lockfile bytes
human_glossary                    사람이 제공한 용어집의 content identity
model_runtime_capabilities        model·tokenizer·serving runtime·GPU identity와 hard limits
validation_baseline               비교 기준 파일 또는 전체 디렉터리 tree ref·hash, 없으면 supplied=false
validation_answer_key             직접 의미 정답지 파일 또는 전체 디렉터리 tree ref·hash, 없으면 supplied=false
```

단일 파일 ref의 hash는 그 파일의 원본 bytes SHA-256이다. 디렉터리 ref는 하위의 모든 파일을 정규화된
상대 POSIX 경로로 정렬하고, 각 상대 경로와 원본 bytes의 길이·내용을 순서대로 묶은 canonical tree
SHA-256이다. 봉인에는 전체 파일별 상대 경로·bytes hash·크기와 총 파일 수를 함께 남긴다. 따라서 여러
파일로 구성된 gold 또는 baseline은 manifest 하나만 봉인해서 나머지 파일 변경을 놓칠 수 없다. 빈
디렉터리, root 밖으로 빠지는 파일, 기대 hash 불일치는 모델 호출과 graph 쓰기 전에 실패한다.

비어 있지 않은 `selected_source_ids`는 partial upload의 정확한 source 경계다. 이때 선택하지 않은
Parser-supported source는 봉인·외부 inventory·Scan Agent 도구에서 읽지 않는다. root manifest와 source가
아닌 외부 프로젝트 입력은 문맥 생산에 실제로 소비되므로 별도 입력으로 보존한다. run output이 project root
아래에 있어도 자기 실행 산출물을 입력으로 다시 봉인하지 않는다.

`analysis_scope_id`는 canonical source root, strategy, 그리고 정확한 source population으로 만든다. 전체
population과 partial population은 서로 다른 scope이며, partial끼리도 정규화된 `selected_source_ids` 집합이
다르면 다른 scope다. 같은 집합의 반복 실행만 같은 scope의 새 generation이 된다. 따라서 부분 검증이나
partial upload의 active generation이 전체 프로젝트 또는 다른 선택 집합의 active generation을 대체하지 않는다.

봉인 뒤 prompt·LLM 설정·skill·용어집·외부 문서·table linker가 파일을 다시 열어 다른 revision을 소비하면
계약 위반이다. 모든 text consumer는 봉인 때 읽은 bytes를 run cache에서 재사용한다. CRLF/LF 변환은 기존
text decoding 의미와 같은 universal-newline 표현에만 적용하며 원본 bytes hash는 바꾸지 않는다. 외부 문서
subprocess도 원래 경로를 재개방하지 않고 봉인 bytes로 만든 격리 복사본만 읽는다. 성공 run manifest는 이
전체 내용을 복제하지 않고 단일 봉인 파일의 `ref`와 `sha256`만 가진다.

영속 response cache는 기본 OFF다. 아래 정본 ref의 실제 bytes hash가 봉인 hash와 일치하지 않으면 cache를
사용하지 않는다. 적중 key에는 정본 hash뿐 아니라 필터 적용 뒤 실제 메시지와 provider·endpoint·model·
sampling·reasoning·extra body·출력 한도·structured output schema를 포함한다. 별도 framework 전역
response cache는 금지한다.

```yaml
sealed_model_runtime_capabilities:
  model_id: <model family ID>
  model_revision: <weights/binary immutable revision>
  tokenizer_id: <tokenizer ID>
  tokenizer_revision: <vocabulary/behavior immutable revision>
  quantization: <실제 weight·KV 정밀도>
  serving_runtime:
    runtime_id: <server implementation ID>
    runtime_version: <immutable version>
    runtime_config_sha256: <실제 runtime 설정 지문>
  provider_hard_limits:
    context_tokens: <provider가 실제 보장·강제하는 context 한계>
    max_output_tokens: <provider가 실제 보장·강제하는 출력 한계>
  gpu_runtime_profile:
    gpu_model: <실제 GPU 기종>
    gpu_count: <실제 개수>
    memory_bytes_per_gpu: <실제 bytes>
    driver_runtime_version: <driver/runtime version>
    profile_version: <검증된 admission profile version>
```

`provider_hard_limits`는 속도를 위해 임의로 둔 item/token 상한이 아니다. 위 값을 얻지 못하거나 실행 직전 실제
model/tokenizer/runtime/GPU 정체성이 봉인값과 다르면 분석을 시작하지 않는다. 봉인 묶음에는 gold와 별도로
비교 대상 commit·입력 hash·기대 건수·측정 조건을 담은 `baseline_sha256`도 포함한다.

### 1.4 언어 후보·호출 판정·외부 참조의 canonical 내부 wire

이 절이 Analyzer 내부 semantic candidate와 resolution wire의 단일 소유자다. 모든 property 이름은
`lower_snake_case`이고, Parser의 `lowerCamelCase` property는 값·배열 순서·null·동적 data key를 바꾸지
않는 기계 adapter를 통과한다. Parser 계약이나 작업보고 문서에 별도 변형 wire를 만들지 않는다.

언어 adapter의 전체 출력:

```yaml
language_semantic_candidate_set:
  adapter_schema_version: analyzer-language-semantic-candidates/v1
  adapter_implementation_id: analyzer-c-semantic-adapter/<sealed version>
  required_symbol_use_occurrence_ids: [<Parser symbol_use occurrence ID>]
  required_call_occurrence_ids: [<Parser call occurrence ID>]
  symbol_candidates:
    - parser_symbol_use_occurrence_ref: <Parser symbol_use occurrence ID>
      owner_code_node_id: <Analyzer owner stable ID>
      visible_declaration_candidates:
        - declaration_occurrence_ref: <Parser declaration occurrence ID>
          declared_symbol_kind: constant
          type_compatibility: compatible
          linkage_visibility: visible
      configuration_presence: active
  call_candidates:
    - parser_call_occurrence_ref: <Parser call occurrence ID>
      owner_code_node_id: <Analyzer owner stable ID>
      callable_form: named
      declaration_candidates:
        - declaration_occurrence_ref: <Parser callable occurrence ID>
          target_code_node_id: <uploaded definition stable ID or null>
          type_compatibility: compatible
          linkage_visibility: visible
      external_identity_candidates: [<language-qualified callable identity key>]
      configuration_presence: active
```

- `adapter_schema_version`과 `adapter_implementation_id`는 candidate artifact부터 call-resolution artifact까지
  그대로 보존한다. 같은 schema를 다른 구현으로 실행한 결과를 같은 cache/replay 결과로 보지 않는다.
- `type_compatibility`는 `compatible|incompatible|unknown|not_applicable`, `linkage_visibility`는
  `visible|not_visible|conditional|not_applicable`, `configuration_presence`는
  `active|inactive|conditional`의 닫힌 enum이다.
- `symbol_candidates`와 `call_candidates`의 source occurrence 집합은 각각 required 집합과 exact equality다.
  adapter는 후보·언어 사실만 만들고 final binding이나 standard/framework 분류를 만들지 않는다.
- declaration order, lexical scope, class/package 접근처럼 언어마다 다른 가시성 규칙은 adapter가
  candidate별 `linkage_visibility`로 표시한다. language-neutral core는 Parser range를 자기 언어 규칙으로
  다시 비교하지 않고 visible candidate 집합에서 final binding/disposition만 결정한다.
- Java는 receiver가 없는 직접 method invocation이 같은 enclosing type에 있고 인자 수가 문법적 parameter
  arity와 맞을 때 classpath 없이 후보를 `visible|compatible`로 만들 수 있다. 같은 source라는 간접 조건이
  아니라 call의 `caller_id`와 callable의 `code_node_id`가 가리키는 정확한 `owner_id`를 비교한다.
- Java의 명시적·와일드카드 static import는 Parser가 생산한 type path/member/wildcard와 Analyzer의 기존
  `CodeNode.owner_id`, callable modifier tree를 결합한다. 정확한 owner type이 일치하고 callable에 `static`
  modifier가 있을 때만 visible이다. 일반 type import, instance method, 다른 source의 import는 이름만으로
  승격하지 않는다. Parser receiver range의 exact source가 `this`인 호출은 현재 owner type임이 닫혀 있으므로
  같은 type method 후보를 사용할 수 있다. 그 밖의 receiver dispatch, inheritance와 overload type conversion은
  완전한 Java type environment가 없으면 `dynamic|external`로 닫는다.
- 일반 Java 생성자 declaration은 Parser가 callable fact와 기존 함수 분석 단위인 `METHOD` 구조 노드를 함께
  생산한다. Analyzer는 같은 type, 같은 package, 명시적 type import, namespace wildcard 중 하나로 type이
  정확히 보이고 constructor parameter arity가 맞을 때만 업로드된 생성자 정의를 연결한다. 정의가 업로드
  범위에 없으면 external이다. Parser는 Java record를 기존 `CLASS`, compact constructor를 기존 `METHOD`로
  보존해 callable fact와 exact-once 결합한다. compact constructor의 암시적 parameter는 callable 발생 범위
  바깥 record header에 있으므로 0-arity로 꾸미지 않고 type compatibility를 `unknown`으로 넘긴다. 정확한
  owner type의 유일 정의일 때만 neutral core가 연결한다. qualified constructor는 Parser가 생산한 각 type
  identifier component 전체가 candidate owner type과 정확히 같을 때만 import 없이 보인다. 일부 이름이나
  파일명으로 보충하지 않는다.
- 같은 물리 줄에 같은 이름의 overload가 여러 개 있어도 줄을 occurrence identity로 사용하지 않는다. 동일
  Parser traversal에서 나온 callable fact와 함수 AST node를 `(terminal_name, physical_line)` 그룹 안의 순서로
  각각 한 번만 소비하며, 대응 node가 부족하면 즉시 계약 오류다.
- Python은 callable 이름 일치만으로 후보를 visible로 만들지 않는다. parameter·assignment·import·nested
  definition·class definition·`global|nonlocal` 등 Parser binding-site 전체와 봉인된 import environment가
  없으면 bare-name call도 `dynamic`으로 닫는다. Parser의 `parameter` 사실은 이름 token range와
  function/lambda lexical scope만 생산하며, 같은 scope의 parameter가 호출 이름을 가리면 Analyzer가
  `python_parameter_binding`으로 내부 연결을 금지한다. Parser의 `binding_target` 문법 tree는 Analyzer가
  단순 이름·unpacking target과 attribute/subscript mutation을 구분하는 데만 사용한다. 같은 function/lambda
  scope의 assignment·annotated/augmented assignment·for·with·except target이 bare call 이름을 묶으면
  `python_local_binding_target`으로 내부 연결을 금지한다. comprehension target은 target의 정확한
  `comprehension` scope가 call의 scope path에 포함될 때만 적용한다. 따라서 `[x() for x in x()]`의 element
  expression은 target `x`의 지배를 받지만 가장 바깥 iterable의 `x()`는 바깥 scope에서 평가된다. 같은
  function/lambda scope의 명시적
  `scope_directive`는 지역 target 판정보다 우선한다. `global`은 `python_global_binding`, `nonlocal`은
  `python_nonlocal_binding` dynamic으로 닫으며 실제 module/enclosing binding은 완전한 symbol 환경 전까지
  추측하지 않는다. 상충 지시는 unsupported로 fail closed한다. Parser `import` fact의 entry와 lexical
  `scopePath`는 중복 binding fact 없이 그대로 소비한다. `import package.module`은 첫 path component,
  `import ... as alias`는 alias, `from ... import member`는 alias 또는 member가 Python에서 결속하는 이름임을
  adapter가 해석한다. 같은 함수의 명시 import 또는 함수에서 보이는 module import가 호출 이름과 충돌하면
  `python_import_binding`, 이름을 열거할 수 없는 가시적 wildcard import면
  `python_wildcard_import_binding` dynamic으로 닫는다. 실제 imported module/member 연결은 봉인된 import
  environment와 module runtime order가 갖춰질 때까지 추측하지 않는다. 이 내부 사실은 LLM 입력이 아니다.
  대문자 여부나 선언 순서는 binding 근거가 아니다.

  `del`의 단순 이름 target도 Python compiler가 함수 지역 이름으로 결정하므로 Parser
  `delete_target`을 같은 문법 tree 경로로 공급한다. Analyzer는 `del name`은 지역 결속으로 처리하지만
  `del object.attribute`와 `del values[index]`는 object state 변경으로 구분한다.

- Oracle adapter는 Parser의 `callee_path_ranges`, `database_link_range`, callable `name_path_ranges`, parameter
  grammar component만 소비한다. 각 range에서 한 번 복원한 component를 다시 점·괄호·정규식으로 분해하지
  않는다. unquoted identifier는 Oracle 규칙에 따라 대문자 identity, quoted identifier는 escape를 해제한
  exact identity로 비교한다. 현재 caller callable의 구조 경로로 확정되는 schema/package prefix만 보충하고
  그 밖의 현재 schema·synonym·remote database 상태는 추측하지 않는다. DB link 호출과 업로드 밖 정의는
  external이며 stub/CALLS를 만들지 않는다. parameter 수와 default 존재로 배제 가능한 overload만 거르고,
  인자 타입 환경 없이 남은 여러 overload는 ambiguous다. 패키지 이름 목록은 callable identity가 아니므로
  표준 라이브러리 분류에 쓰지 않는다. 봉인 version의 exact callable registry가 없고 업로드 정의도 없으면
  제품 `CALLS` 없이 external occurrence로 보존한다.

- PostgreSQL adapter는 Parser가 전체 파일 좌표로 rebasing한 PL/pgSQL call range와 callable
  `name_path_ranges`, 선언 종류, parameter/default/variadic grammar component만 소비한다. unquoted identifier는
  소문자 identity, quoted identifier는 escape를 해제한 exact identity로 비교한다. 명시적 schema-qualified
  FUNCTION/PROCEDURE만 같은 kind와 arity의 업로드 정의 후보로 만들며, unqualified call은 봉인된
  `search_path`가 없으므로 `configuration_dependent`로 닫는다. parameter type까지 같아 남는 overload는
  ambiguous이고, 명시적 schema 대상이 업로드 범위에 없으면 partial-upload external이다. Parser `CALL`
  CodeNode의 이름을 다시 분해하거나 source 문자열로 괄호·점·인자를 재파싱하지 않는다.

Framework와 DBMS runtime은 모두 `link_parser_calls_step` 하나를 사용한다. 전략은 검증된 framework 제외
계약만 주입하고, 언어 adapter와 language-neutral resolution core가 모든 call occurrence를 한 번 분류한다.
DBMS `CALL` CodeNode는 구조 표시용일 뿐 binding 권위가 아니다.

호출 판정의 전체 출력:

```yaml
call_resolution_ledger:
  adapter_schema_version: analyzer-language-semantic-candidates/v1
  adapter_implementation_id: analyzer-c-semantic-adapter/<sealed version>
  callable_registry_snapshots:
    standard_library:
      registry_id: <language standard-library registry ID>
      registry_version: <sealed registry version>
    framework:
      registry_id: <framework callable registry ID>
      registry_version: <sealed registry version>
  required_call_occurrence_ids: [<Parser call occurrence ID>]
  occurrences:
    - parser_call_occurrence_ref: <Parser call occurrence ID>
      owner_code_node_id: <Analyzer owner stable ID>
      call_resolution_status: internal
      selected_callable_identity_key: <language-qualified selected callable identity>
      resolved_callee_id: <uploaded user callable CodeNode stable ID>
      selected_declaration_occurrence_ref: <selected Parser callable occurrence ID>
      candidate_declaration_occurrence_refs: [<all considered Parser declaration occurrence IDs>]
      classification_registry_ref: null
      configured_evidence_refs: []
      wrapper_call_occurrence_refs: [<validated outer wrapper call occurrence IDs>]
      call_resolution_reason_code: null
  internal_call_pairs:
    - caller_code_node_id: <owner CodeNode stable ID>
      callee_code_node_id: <resolved internal user callable CodeNode stable ID>
```

각 occurrence는 위 필드를 모두 가지며 해당 없음은 `null` 또는 `[]`다. `call_resolution_status`는 정확히
다음 9개 중 하나다.

```text
internal | external | standard_library | framework_provided |
configuration_dependent | ambiguous | dynamic | inactive | unsupported
```

상태별 필드 불변식:

- `internal`: selected identity, `resolved_callee_id`, selected declaration이 모두 non-null이고 registry ref는 null이다.
- `external`: selected identity만 non-null이며 업로드 밖 정상 경계다. external stub이나 CALLS를 만들지 않는다.
- `standard_library|framework_provided`: selected identity와 `{registry_id, registry_version}`가 non-null이다.
- 나머지 5개 상태는 `call_resolution_reason_code`가 non-null이며 필요한 candidate/configured 근거를 보존한다.
- 표준·framework 분류는 lexical visibility와 shadowing으로 callable 하나를 먼저 선택한 뒤, 그
  `selected_callable_identity_key`를 봉인 version registry의 exact identity key와 비교한다. terminal name이나
  선택 전 후보 이름으로 분류하지 않는다. 같은 identity가 두 registry에 있으면 terminal failure다.
- 해당 언어의 봉인 registry가 없으면 terminal-name 목록이나 type/module 이름 목록으로
  `standard_library`를 추측하지 않는다. 업로드 정의와 검증된 registry가 모두 없는 호출은 external로
  회계하며 external stub과 제품 CALLS를 만들지 않는다.
- output occurrence ref 집합은 required와 exact equality이고 중복 0이다. `internal_call_pairs`는
  `internal` 행의 `(owner_code_node_id, resolved_callee_id)`를 distinct 후 두 ID의 byte order로 정렬한
  유일한 투영이다. 같은 쌍의 여러 occurrence는 장부에 남지만 제품 CALLS는 하나다.

외부 참조 판정의 전체 출력:

```yaml
external_reference_ledger:
  required_external_reference_occurrence_ids: [<Analyzer가 Parser call과 resolver argument로 만든 occurrence ID>]
  occurrences:
    - external_reference_occurrence_id: <required occurrence ID>
      parser_call_occurrence_id: <Parser call occurrence ID>
      owner_code_node_id: <Analyzer owner stable ID>
      source_file: <Parser source file>
      source_line: <Parser source line>
      resolver_name: <Analyzer resolver registry의 exact name>
      argument_index: <검사한 argument index>
      resolution:
        linked:
          external_source_record_id: <validated ExternalSourceRecord ID>
          external_key: <exact reference key>
    - external_reference_occurrence_id: <required occurrence ID>
      parser_call_occurrence_id: <Parser call occurrence ID>
      owner_code_node_id: <Analyzer owner stable ID>
      source_file: <Parser source file>
      source_line: <Parser source line>
      resolver_name: <Analyzer resolver registry의 exact name>
      argument_index: <검사한 argument index>
      resolution:
        unresolved:
          reason_code: <closed reason enum>
          candidate_target_ids: []
    - external_reference_occurrence_id: <required occurrence ID>
      parser_call_occurrence_id: <Parser call occurrence ID>
      owner_code_node_id: <Analyzer owner stable ID>
      source_file: <Parser source file>
      source_line: <Parser source line>
      resolver_name: <Analyzer resolver registry의 exact name>
      argument_index: <검사한 argument index>
      resolution:
        rejected_not_external_reference:
          reason_code: inactive_call
```

각 행은 `linked|unresolved|rejected_not_external_reference` branch 하나만 가진다. 세 occurrence ref 집합의
서로소 합집합이 required와 정확히 같아야 하며, `linked`만 `REFER_TO` 후보가 된다. 나머지는 run artifact에
보존하고 제품 node/label을 만들지 않는다.

## 2. 함수 입력 정본

### 2.1 참조 집합 작성 결과

함수마다 content-addressed `FunctionAnalysisReferenceSet` 하나를 먼저 만들고, 정확히 하나의
`FunctionAnalysisReferenceSetBuildResult`가 그 참조 또는 실패를 반환한다.

```yaml
function_analysis_reference_set:
  target_ref: <사용자 작성 함수 stable ID>
  source_partition_ref: <봉인된 함수 원문 범위 참조>
  structural_occurrence_refs: [<조건·호출·효과 Parser occurrence ID>]
  symbol_refs: [<함수 밖 실제 사용 값의 확정 참조>]
  data_usage_refs: [<직접 data 접근 occurrence 참조>]
  external_chunk_refs: [<exact key로 연결된 외부 조각 참조>]
  internal_call_refs: [<내부 사용자 함수 call occurrence 참조>]
  callee_capsule_refs: [<직접 구조 효과 capsule 참조>]

function_analysis_reference_set_build_result:
  target_ref: <같은 사용자 작성 함수 stable ID>
  output:
    input_ready:
      reference_set_ref: <위 참조 집합의 content-addressed 참조>
```

`reference_set_ref`는 `FunctionAnalysisReferenceSet` canonical bytes의 hash다. 그 hash를 다시
`FunctionAnalysisReferenceSet` 값 안에 넣는 자기참조 field가 아니다. artifact store의 `{ref, value}` 봉투에서만
ref와 값을 함께 보며, 모든 `input_ready.reference_set_ref`는 정확히 한 value를 가리켜야 한다.

또는 무결성 실패 하나만 반환한다.

```yaml
target_ref: <사용자 작성 함수 stable ID>
output:
  failed:
    error_artifact_hash: <source/AST/dangling/duplicate 실패 상세 hash>
```

두 branch는 배타적이다. partial upload, 분석 범위 밖 정의, 미확정 물리 table은 실패나 skip이 아니라 `input_ready` 안의 external/unresolved 참조다. source와 AST가 맞지 않거나 필수 참조가 dangling/duplicate이면 의미 요청과 graph 공개를 시작하지 않는다.

### 2.2 모델에 보내는 함수 입력

참조를 푼 `ResolvedFunctionMeaningInput`은 내부에서 canonical evidence ID와 원문 좌표를 소유한다.
Summary 모델에는 그 장부를 그대로 보내지 않고 아래 최소 packet만 보낸다. 이 packet에는
request-local 결합 key도 없다.

```yaml
response_locale: ko
structural_profile_json:
  target:
    kind: FUNCTION
    signature: c010(context_t *ctx)
  facts:
    -
      kind: CALL
      line: 1138
      callee: c520_mms_psbl_yn_check
      role: effect
linked_callee_outcomes_json:
  - callee: c520_mms_psbl_yn_check(context_t *ctx)
    meaning: <caller가 관측하는 검증된 직접 결과>
linked_external_sources_json: []
source: |
  1135 | if (ctx->gc_usim_yn != "Y") {
  1138 |   c520_mms_psbl_yn_check(ctx);
  1139 | }
```

`facts`는 Parser/Analyzer가 확정한 구조 사실의 source-order 최소 배열이다. canonical evidence ID, fact ID,
occurrence ID, producer identity, request digest key와 fact-reference map을 포함하지 않는다. source와 구조를
맞추는 line/lines만 실제 결합 좌표로 유지한다. 모델 응답은 whole-target `summary`와 `stereotype`만 가지며
구조 사실과 좌표를 복창하지 않는다.

다음 값은 내부 결합·검증에는 필요하지만 모델에는 보내지 않는다.

```text
긴 stable/owner/evidence ID, hash, 절대 경로, char offset, coordinate system,
Parser grammar ref와 wire version, 상세 candidate/resolution 근거,
standard-library/framework call occurrence 장부, exact-once count,
다른 section에 이미 존재하는 source·symbol·table·callee 값
```

원문에 그대로 보이는 값은 별도 section에 반복하지 않는다. 원문만으로 알 수 없는 확정 사실 중 현재
concern의 정확도에 실제 영향을 주는 것만 보낸다. 선택 항목이 없는 map/list는 닫힌 wire shape를
유지해야 할 때만 빈 값으로 존재한다.

## 3. 모든 생성 요청의 공통 전송 계약

실제 request bytes는 다음 순서다.

```text
COMMON_SYSTEM_PREFIX_V1
\n\n
FAMILY_SYSTEM_SUFFIX_<version>
provider strict response schema argument
one dynamic user message
```

공통 prefix, family suffix, strict schema의 전체 문구와 실제 한 요청의 입력·raw response·Analyzer 결과는 작업보고 HTML §7, §10~§14에 각각 한 번만 기록한다. `<COMMON_SYSTEM_PREFIX_V1>`은 문서 중복 방지 표기일 뿐 API에 보내는 문자열이 아니다. API에는 실제 9개 공통 줄이 펼쳐진다.

동적 user message의 section 순서는 아래와 같다.

```text
1. 고정 task/output/decision 계약
2. source를 복제하지 않는 작은 source-order structure array
3. source만으로 알 수 없는 확정 callee/external capsule
4. 원래 line number가 붙은 온전한 대상 source
```

Summary에는 local key가 없다. 다른 keyed family의 local key는 한 요청 안에서만 유일하다. Analyzer 내부 required 정본은 stable obligation ID를 사용하며,
응답은 `(logical_request_id, local_key)`로 scope를 붙여야 여러 요청의 `F1`·`R1`이 충돌하지 않는다.
검증 뒤 제품 graph에서는 local key를 버리지만, local→stable 결합 map은 raw response와 함께
content-addressed run artifact에 보존한다.

```text
S: source structure item
FT: function-table-access tuple
TD: table meaning item
C: column meaning item
R: Rule candidate row
K: verified internal callee
SP: hard-limit Summary source partition
SO: non-overlapping Summary obligation
DU: verified direct data usage
SC/SCN/AP: scanning occurrence/counterexample/proposal
EG/ED: external shape/document
```

각 response root는 해당 관심사가 실제 소비하는 필드만 가지고 `additionalProperties:false`다. Summary는
`summary|stereotype`만 가진다. TableDescription의 TABLE/COLUMN `slots` 값은 내부 객체가 아닌 의미
문자열 하나다. FunctionTableUsage는 FT key→의미 문자열의
strict root이고 Rule은 effect/row의 두 exact map이다. 입력의 condition map은 실행 경로를 이해하는
문맥일 뿐 응답 대상이 아니다. Scanning·external adapter proposal은
작업보고 HTML §7에 정의된 별도
strict root를 사용한다. 어느 family든 모델 출력에는 evidence, confidence, question, 내부 ID/hash,
임의 extra, 자유형식 missing-context가 없다.

Analyzer가 모델 의미값과 내부 참조 장부를 결합하는 의미 상태는 다음처럼 서로 다른 실패를 섞지 않는다.

```text
비어 있지 않은 검증 의미 + missing_context_items 0개      → sufficient
비어 있지 않은 검증 의미 + 확인된 미확정 dependency 존재  → partial
빈 의미 또는 출력 격리 실패                              → insufficient
not_business branch                                   → 제품 Rule 없음, 후보 장부에서 종료
schema/grounding/coverage/source-integrity failure     → terminal failure, publish 없음
```

`partial`은 required key 누락, source 절단, transport 실패를 숨기는 값이 아니다.

## 4. Summary 계약

### 4.1 일반 함수

target 전체를 소유한 한 응답만 `summary`와 `stereotype`을 함께 만든다. 구조 사실별 detail meaning은
whole summary와 중복되고 downstream 제품 소비가 없으므로 생성하지 않는다.

```yaml
summary: <함수 전체 입력→결정→호출·data 접근→상태 변화→결과의 자연스러운 설명 하나>
stereotype: <허용 ontology 값 하나>
```

모델은 충분성 상태나 부족 사유를 출력하지 않는다. Analyzer는 whole-summary 의무 하나, 빈 summary,
실제 `unavailable_cycle` dependency와 출력 검증을 사용해
`sufficient | partial | insufficient`와 고정 missing-context를 만든다. 따라서 모델의 자기평가가 제품
상태로 승격되지 않는다.

허용 stereotype은 다음과 같다.

```text
ApplicationService | DomainService | Repository | Factory | Command | Query |
Validator | Translator | Adapter | Strategy | EventHandler | Scheduler | Helper |
Router | Utility | Controller | Initializer | Unclassified
```

역할이 하나로 확정되지 않으면 priority fallback 없이 `Unclassified`다. `responsibility`, `decisions`, `processing_effects`, `result`를 따로 생성한 뒤 Summary를 다시 만드는 이중 호출·이중 의미 조합은 금지한다.

Analyzer 내부 검증 결과는 모델 의미에 stable identity와 상태만 결합한다.

```yaml
function_id: <실제 함수 stable ID>
summary: <검증된 summary 또는 null>
stereotype: <mapped면 검증된 ontology 값; insufficient면 null>
meaning_status: sufficient | partial | insufficient
missing_context_items:
  - <partial·insufficient일 때 아래 MissingContextItem; sufficient면 빈 배열>
```

```yaml
MissingContextItem:
  missing_context_ref: <실제 부족 대상 또는 occurrence stable ref>
  reason_code: <고정 Analyzer reason>
  description: <감사용 짧은 설명>
```

`missing_context_items`는 모델 출력이 아니며 run artifact에만 온전히 둔다. 제품 CodeNode에는 count와 artifact hash만 저장한다.

### 4.2 provider hard limit를 넘는 함수

임의 길이 상한·truncation·Top-N·sampling을 사용하지 않는다. 실제 provider hard limit를 넘을 때만 source order를 보존한 겹치지 않는 `SP` partition을 만든다. 각 source item은 정확히 한 `SO`에 속하며 모든 SO는 leaf response에서 정확히 한 번 닫힌다.

leaf branch:

```yaml
results:
  SP1:
    item_results:
      SO1:
        included:
          meaning_fragment: <해당 의무의 source-grounded 의미>
      SO2:
        excluded:
          disposition_reason_code: diagnostic_only | mechanical_setup_only | no_business_effect
      SO3:
        insufficient:
          disposition_reason_code: semantic_not_derivable
```

Analyzer는 partition·source item·SO에 대해 required/produced/missing/duplicate/unexpected 장부가 모두 0 잔차인지 검증한다. 그 뒤 검증된 leaf item results와 함수 역할 구조 사실만 synthesis에 한 번 보낸다. 함수 원문은 synthesis에 다시 보내지 않는다. leaf 하나라도 schema·grounding·coverage·transport에 실패하면 synthesis 0회, 해당 함수와 run은 공개 실패다. 모든 key가 닫혔지만 의미를 안전하게 만들 수 없는 경우에만 `insufficient`를 허용한다.

## 5. FunctionTableUsage 계약

확정된 직접 `(function, physical table, access mode)`마다 FT key 하나를 만든다. 하위 함수의 table 사용을 caller로 복사하지 않는다. Summary를 기다리거나 Summary 문장을 입력으로 사용하지 않는다.

Table identity, READ/WRITE, DML operation, column identity, occurrence 좌표는 Analyzer가
이미 소유한다. 모델은 `table_roles`, table ID, access mode, operation, column, 좌표를
다시 고르거나 출력하지 않고 assigned FT key의 의미 문자열만 채운다. 결정론
column usage 근거는 FT 모델 응답에 넣어 운반하지 않으며, FT 호출이 실패해도
최종 Table/Column 의미 단계에 직접 도달해야 한다.

모델 입력은 아래 한 묶음과 번호가 붙은 현재 함수 원문뿐이다. `occurrences`는
Analyzer가 exact direct DATA_OBJECT 사실에서 만든다. 전체 structural profile, 내부 evidence/slot ID,
함수 Summary, 다른 table의 DDL column 목록은 이 호출에 넣지 않는다.

```yaml
assigned_table_usages:
  FT1:
    physical_table_id: <확정 table stable ID>
    access_mode: read | write
    occurrences:
      - source_line: <절대 원문 줄>
        operation: <Parser/Analyzer가 확정한 연산>
        qualified_columns: [<이 occurrence에서 직접 한정된 column>]
linked_table_context:
  <table stable ID>: <이미 검증된 table 설명 또는 빈 문자열>
numbered_source: <줄 번호가 붙은 현재 함수 원문>
```

```yaml
FT1: <이 함수 안에서 이 table을 쓰는 업무 의미 문장 또는 빈 문자열>
FT2: <다음 exact table/access의 업무 의미 문장 또는 빈 문자열>
```

FT map 자체가 response root이며 `results`, `mapped`, `insufficient`,
`function_table_usage_summary`, 반복 reason code 같은 포장 필드는 없다. 빈 문자열이면 Analyzer가
`insufficient`와 고정 missing-context를 만들고, 비어 있지 않은 검증 문자열이면 `sufficient`로 닫는다.

Analyzer 내부 검증 결과:

```yaml
owner_function_id: <실제 함수 stable ID>
physical_table_id: <확정 table stable ID>
access_mode: read | write
used_column_ids: [<직접 사용 column stable ID>]
data_usage_occurrence_refs: [<모든 직접 사용 occurrence ID>]
function_table_usage_summary: <검증 문장 또는 null>
meaning_status: sufficient | insufficient
missing_context_count: <0 또는 양수>
missing_context_artifact_hash: <부족 문맥이 있을 때만 SHA-256>
```

`sufficient`이면 summary가 있고 count/hash가 없다. `insufficient`이면 summary가 없고 Analyzer가 만든
부족 문맥 artifact의 count/hash가 있다. 한 FT 안에서 일부 occurrence만 mapped인 혼합 결과는 제품에
부분 의미로 공개하지 않고 계약 위반으로 실패한다.

제품 정본은 별도 node가 아니라 해당 `READS`/`WRITES` 관계 속성이다. `data_operations`는 의미 응답이
다시 생산하지 않으며 Step 5의 직접 접근 관계가 소유한다.

## 6. TableDescription 계약

제품에서 판정할 직접 사용 table의 table key와 전체 column key를 먼저 만든다. 직접 사용 문맥이 있는 key만 모델 required set에 넣고, 나머지는 Analyzer가 `missing_usage_context`로 결정론 판정한다.

```text
model_required_keys ∩ deterministic_insufficient_keys = ∅
model_required_keys ∪ deterministic_insufficient_keys = all_product_meaning_keys
```

모델 required key마다:

```yaml
results:
  TD1:
    mapped:
      logical_name: <검증 가능한 논리명>
      description: <검증 가능한 table 의미>
  C7:
    insufficient:
      disposition_reason_code: unresolved_domain_abbreviation | configuration_dependent | unsupported_semantics
```

`missing_usage_context`는 모델 enum이 아니다. DDL-only 미사용 table은 모델 호출 대상이 아니다. 입력은 assigned table/column DDL metadata와 LLM prose가 아닌 verified direct usage뿐이며, FunctionTableUsage 문장을 재사용하지 않는다. 모델 결과와 결정론 결과의 disjoint union이 table+전체 column key와 exact equality여야 한다.

Analyzer가 만드는 table과 column 결과는 물리 정체성 이름을 섞지 않는다.

```yaml
table_result:
  physical_table_id: <table stable ID>
  physical_table_name: <물리 table 이름>
  logical_name: <검증된 논리명 또는 null>
  analyzed_description: <검증된 설명 또는 null>
  meaning_status: sufficient | insufficient
  missing_context_items: [<insufficient이면 Analyzer가 만든 MissingContextItem; sufficient면 빈 배열>]
column_result:
  physical_column_id: <column stable ID>
  physical_column_name: <물리 column 이름>
  logical_name: <검증된 논리명 또는 null>
  analyzed_description: <검증된 설명 또는 null>
  meaning_status: sufficient | insufficient
  missing_context_items: [<insufficient이면 Analyzer가 만든 MissingContextItem; sufficient면 빈 배열>]
```

## 7. Strategy B Rule과 Example 계약

### 7.1 후보와 모델 책임

Analyzer는 Parser 구조, containment, binding으로 R row·source closure·condition atom·direct effect atom·allowed symbol을 먼저 닫는다. 후보에는 조건 분기, guard return, 조건 없는 직접 업무 효과, 검증·변환·상태 변경, 구현 전용 분기가 들어간다. LLM은 새 row·symbol·effect를 발견하지 않고 각 R key를 한 번 판정한다.

```yaml
effects:
  E1: <현재 함수의 업무 흐름에서 이 직접 효과 atom이 뜻하는 한 가지 의미 또는 빈 문자열>
rows:
  R1: business_rule | implementation_detail | insufficient
```

두 map은 response schema가 열거한 key를 빠짐없이 정확히 한 번 가져야 하고 extra key는 거부한다.
입력의 `conditions`는 행의 구조와 실행 경로를 이해하는 데만 사용한다. 모델이 opaque framework
predicate를 자연어로 다시 번역하면 실제 효과와 반대 극성을 만들 수 있으므로 condition 의미는 응답
대상이 아니다. 모델은 condition/effect 결합, Python, Rule, Example, reason 문장, confidence,
question, evidence ID를 작성하지 않는다. Analyzer가 정확한 구조 조건과 충분한 effect 의미를 결합한다.

`business_rule`이면서 해당 행의 직접 effect 의미가 모두 비어 있지 않은 행만 다음 제품 Rule이 된다.
소유 함수는 `HAS_RULE` 관계로 알 수 있으므로 Rule property로 복제하지 않는다.

```yaml
anchor_line: <근거 시작 줄>
structural_condition: <Analyzer 구조 Python 또는 null>
structural_effects: [<Analyzer 구조 Python effect>]
business_effects: [<Analyzer가 atom 의미와 구조를 결합한 restricted Python effect>]
```

`implementation_detail`은 제품 Rule을 만들지 않고 `excluded` finding으로 끝낸다. 행 분류가
`insufficient`이거나 필요한 atom 의미가 비어 있으면 제품 Rule을 만들지 않고 `unverified` finding으로 끝낸다. 모델 호출이
실패하면 구조 후보를 Rule로 가장하지 않고 행마다 `unavailable` finding을 남긴다. 따라서 모든 구조 후보는
공개 Rule·excluded·unverified·unavailable 중 정확히 하나에 속한다.
제품 Rule의 존재 자체가 충분한 업무 의미 검증 완료를 뜻하므로 모든 Rule에 같은
`business_meaning_status=sufficient`를 복제하지 않는다.

### 7.2 제한 Python

허용 목적은 자연어 Rule을 동일 의미로 비교할 수 있는 구조화 표기로 바꾸는 것이다. 이 문자는 실행하지 않는다. Analyzer는 synthetic function body에 넣어 Python parser로 구문만 검사한다.

```text
condition:
  null 또는 bool/comparison/call/member/subscript/literal/name과 허용 operator의 expression

effect:
  허용 symbol의 call expression
  허용 target의 assignment
  "return" + 허용 expression
```

import, declaration, loop, comprehension, lambda, reflection, `eval`, 임의 library call, 모델이 새로 만든 symbol은 금지한다. comparison direction, negation, boolean grouping, literal, call argument, effect order를 보존한다. nested row는 supplied ancestor guard를 합성하고, else는 sibling local condition만 부정한다.

검증된 wrapper는 CALLS graph 대상이 아니어도 Rule effect에서는 보존한다.

```python
PFM_TRY(c520_mms_psbl_yn_check(ctx))
```

callee 내부 상태 변경을 caller Rule에 복사하지 않는다. Python parse 성공은 의미 정확도나 제품 합격이 아니다. allowed-symbol·source correspondence·exact-once와 봉인 직접 의미 판정을 별도로 통과해야 한다.

### 7.3 Example

검증된 Rule 중 구조 조건과 업무 효과가 모두 있는 Rule마다 LLM 0회로 만든다. 조건 없는 직접 효과
Rule은 제품 Rule로 유지하지만, 참이어야 할 입력 상태가 없는 Example을 만들지 않는다.

```text
given = [Rule.structural_condition]  byte-identical
then  = Rule.business_effects        byte-identical array order
```

조건 문자열과 효과 배열은 Rule을 다시 parse·정규화·분해하지 않고 byte-identical로 재사용한다.

## 8. 독립 실행과 호출 회계

한 함수의 Summary, FunctionTableUsage, Rule은 같은 `ResolvedFunctionMeaningInput`에서 concern별 projection을 만들되 서로 기다리지 않는다. TableDescription은 DDL+verified direct usage가 준비되면 실행한다. 각 family의 첫 실제 요청이 prefix cache를 데우며 dummy warmup이나 중복 분석은 없다.

```yaml
invocation_ledger:
  logical_invocations:
    ledger_name: <family logical invocation ledger>
    required_keys: [<content-addressed logical request ID>]
    legitimate_disposition_sets: {completed_response: [<ID>], reused_exact_response: []}
    produced_keys: [<legitimate_disposition_sets의 서로소 합집합만>]
    failure_keys: []
    missing_keys: []
    duplicate_keys: []
    unexpected_keys: []
    pass: true
  transport_attempts:
    ledger_name: <family transport attempt ledger>
    required_keys: [<logical request ID, attempt number>]
    legitimate_disposition_sets: {completed: [<attempt key>], retryable_failed: []}
    produced_keys: [<legitimate_disposition_sets의 서로소 합집합만>]
    failure_keys: []
    missing_keys: []
    duplicate_keys: []
    unexpected_keys: []
    pass: true
```

SDK와 Analyzer runner의 자동 retry는 0이다. 현재 구현은 transport·deadline·length·repetition·schema
실패를 다시 보내거나 응답 bytes를 고쳐 성공으로 승격하지 않는다. request 수 제한으로 item을 버리지
않는다. batch는 실제 input token, reserved output, KV bytes, prefill/decode workspace로 admission하며
단일 key가 provider hard limit를 넘으면 조용히 줄이지 않고 실패한다.
`reused_exact_response`는 immutable model revision·tokenizer revision·quantization·serving runtime/config·provider
hard limits·GPU profile·system/user/schema/output-policy 전체 request hash와 검증 완료 raw/parsed artifact hash가 모두
같은 경우만 허용하며, 이때 logical invocation은 하나지만 새 transport attempt는 0이다.

## 9. 결정론 후처리

- Module: 소속 함수 ID 순서로 `{function_ref, summary}`를 byte-identical하게 묶는다. 축약·재작성 LLM은 없다.
- Component: generation의 검증 관계에서 봉인된 Leiden 설정으로 계산한다. 설명 LLM은 없다.
- Glossary: 사람이 제공한 용어 파일이 있을 때만 검증·저장한다. 모델 발명 용어는 없다.
- SearchSegment: 검증된 제품 의미와 source 좌표로 결정론 생성한다. embedding 입력을 줄이거나 sampling하지 않으며 model ID·차원·vector bytes를 기록한다.

## 10. 제품 graph 계약

### 10.1 generation 격리

허용된 기존 database 안에서 `AnalysisScope` 아래 비공개 `AnalysisGeneration(generation_status=building)`을 만든다. 모든 generation 소속 node·relationship은 `analysis_scope_id`와 `generation_id`로 격리한다. `analysis_scope_id`는 `analysis-scope/v2`이며 같은 root·strategy·정확한 source population에서만 재사용한다. 새 database를 만들지 않는다.

공개 제어 node의 닫힌 목록:

```text
AnalysisScope, AnalysisGeneration
```

generation 제품 node의 닫힌 목록:

```text
Package, Module, File, CodeNode, Table, Column, Rule, Example,
ExternalChunk, Component, GlossaryTerm, SearchSegment, AnalysisHealth
```

제품 relationship의 닫힌 목록:

```text
PARENT_OF, NEXT, HAS_COLUMN, FK, CALLS, READS, WRITES, REFER_TO,
HAS_RULE, HAS_EXAMPLE, MEMBER_OF_COMPONENT, HAS_SEARCH_SEGMENT
```

unresolved, call occurrence, candidate, model local key, raw response, finding은 제품 graph node/label이 아니라 content-addressed run artifact다.

### 10.2 CALLS와 data 관계

- `CALLS`는 증명된 내부 사용자 함수 caller→callee 한 쌍당 하나다.
- 같은 함수 안 같은 callee의 모든 발생은 run ledger에 남기되 관계는 deduplicate한다.
- standard-library, framework-provided, external, ambiguous, dynamic, inactive, unsupported 호출은 CALLS를 만들지 않는다.
- `READS`/`WRITES`는 직접 확정 function-table-access만 만들고 `data_operations`, `used_column_ids`, FunctionTableUsage 의미와 count/hash를 소유한다.
- `HAS_COLUMN`은 DDL이 확정한 Table→Column 소유권이다. 모든 제품 Column은 정확히 한 Table에 속한다.
- `FK`는 DDL이 명시한 Table→Table 외래키만 공개한다. 같은 table pair의 여러 column pair는
  `source_column_names`, `target_column_names`, `cardinalities`의 같은 길이·같은 순서 배열로 보존한다.
  추론 후보 FK는 제품 관계로 승격하지 않는다.

### 10.3 단계별 property 소유권

```yaml
code_structure_code_node_insert:
  - stable_id
  - analysis_scope_id
  - generation_id
  - code_kind
  - name
  - qualified_name
  - start_line
  - end_line
function_meanings_code_node_patch:
  - summary
  - stereotype
  - meaning_status
  - missing_context_count
  - missing_context_artifact_hash
data_access_links_relationship_insert:
  - analysis_scope_id
  - generation_id
  - data_operations
function_meanings_data_access_patch:
  - used_column_ids
  - function_table_usage_summary
  - meaning_status
  - missing_context_count
  - missing_context_artifact_hash
```

한 table을 같은 함수가 읽고 쓰면 `READS`와 `WRITES`는 서로 다른 제품 관계다. 구조 단계는 각 관계의
직접 endpoint와 실제로 확인된 `data_operations`만 소유한다. `used_column_ids`와
`function_table_usage_summary`는 access mode가 분리된 FunctionTableUsage 결과가 나온 뒤 의미 단계가
관계 property patch로 소유한다. `(function, table)` 하나로 합쳐진 문장을 두 관계에 복사하거나,
relation 이름에서 누락된 operation을 추측하지 않는다.

Neo4j에 저장하는 property value는 `boolean | integer | finite float | string` 또는 동일 primitive type의
1차원 list뿐이다. `null`, map, list-of-map, nested/heterogeneous list는 저장하지 않는다. optional 값이 없으면
그 property 자체가 없어야 하며 `SET property = null`로 표현하지 않는다. Module rollup은 같은 길이·같은 순서의
`summary_rollup_function_ids: list<string>`와 `summary_rollup_summaries: list<string>` 두 property로 저장한다.

모든 function CodeNode는 최종 공개 때 `meaning_status`와 `missing_context_count`를 갖는다. 검증된 mapped
의미가 있을 때만 `summary`와 `stereotype`이 존재하고, `missing_context_artifact_hash`는 count가 1 이상일
때만 존재한다. 비function CodeNode에는 이 의미 property가 없어야 한다. 같은 presence 규칙을 Rule,
Table/Column, READS/WRITES의 optional 의미 property에도 적용한다. 상세 missing-context 목록은 graph에
복제하지 않는다. Rule은 `anchor_line`, 구조 condition/effects와 업무 effects를, Example은 given/then을
소유한다. owner와 source Rule 정체성은 `HAS_RULE`, `HAS_EXAMPLE` 관계가 소유한다.

### 10.4 외부 조각과 검색·health의 필수 provenance

```yaml
ExternalSourceRecord: # generation 생성 전 Analyzer 내부의 검증된 원문 레코드
  source_record_id: <source file ID + exact source span 또는 derived ordinal>
  source_file_id: <원본 외부 파일 stable ID>
  external_adapter_id: <accepted adapter stable ID>
  external_adapter_version: <실제 adapter contract version>
  addressable_keys: [<코드가 실제 주소로 사용할 수 있는 key>]
  semantic_kind: <닫힌 종류>
  exact_text: <잘리지 않은 원문>
  project_relative_path: <프로젝트 상대경로>
  source_character_start: <원본 문자 시작 위치>
  source_character_end: <원본 문자 끝 위치>
  source_start_line: <원본 시작 줄>
  source_end_line: <원본 끝 줄>
  provenance: <source_span | derived_document>
  source_sha256: <원문 전체 hash; source_span일 때 필수>
  content_sha256: <exact_text hash>

ExternalChunk: # resolved_links graph projection이 아래 두 control identity만 주입
  stable_id: <file identity + exact key + content hash>
  analysis_scope_id: <scope ID>
  generation_id: <generation ID>
  external_key: <검증된 exact key>
  semantic_kind: <닫힌 종류>
  source_file_id: <원본 외부 파일 stable ID>
  source_record_identity: <adapter가 선택한 유일 record 정체성>
  external_adapter_id: <accepted adapter stable ID>
  external_adapter_version: <실제 adapter contract version>
  start_line: <원본 시작 줄>
  end_line: <원본 끝 줄>
  content_sha256: <잘리지 않은 조각 hash>
  exact_text: <잘리지 않은 원문>

SearchSegment:
  stable_id: <parent + order + text hash>
  analysis_scope_id: <scope ID>
  generation_id: <generation ID>
  source_start_line: <원문 좌표가 있을 때만 존재>
  source_end_line: <원문 좌표가 있을 때만 존재>
  text_sha256: <전체 text hash>
  text: <잘리지 않은 검색 text>
  embedding_model_id: <봉인 model ID>
  embedding: <전체 차원 vector>

AnalysisHealth:
  stable_id: <generation ID>::health
  analysis_scope_id: <scope ID>
  generation_id: <generation ID>
  missing_total: 0
  duplicate_total: 0
  unresolved_call_count: <실측>
  unresolved_data_object_count: <실측>
  prepublish_wall_time_ms: <run 시작부터 모든 비-health 제품 stage 입력·검색 embedding closure까지의 실측>
  prepublish_performance_artifact_hash: <publish/finalize를 제외한 단계별 상세 측정 장부 hash>
```

`ExternalSourceRecord`에는 `analysis_scope_id`와 `generation_id`가 없고, generation 생성 뒤
`resolved_external_reference_links` projection만 두 값을 주입해 `ExternalChunk`를 만든다. `ExternalChunk.external_key`가
REFER_TO의 exact-key 정본이며 관계에 복제하지 않는다. 모든 실제 외부 참조 발생은 run ledger에 남긴다.
SearchSegment의 vector 일부를 잘라 저장하지 않는다. publish transaction·commit까지 포함한 전체 wall과
Neo4j timing은 immutable `AnalysisHealth`에 소급 patch하지 않고 post-commit run artifact가 소유한다.

### 10.5 검색·상세조회 소비 계약

- 검색은 active completed generation의 `CodeNode-[:HAS_SEARCH_SEGMENT]->SearchSegment`만 읽는다.
  별도 `EMBEDDED`/`EMBED_META` node, 대문자 구형 label, `id`/`graph_owner` 호환 조회는 없다.
- `search_segment_embedding` vector index는 `SearchSegment.embedding`을 색인하고
  `analysis_scope_id`·`generation_id`를 additional property로 포함한다. Cypher `SEARCH`의 in-index filter로
  요청 범위를 먼저 제한한 뒤 top-k를 구한다. 전체 generation에서 top-k를 뽑고 사후 필터링하지 않는다.
- corpus는 SearchSegment 수, embedding 존재 수, 단일 model ID, 단일 차원, CodeNode 연결 수가 모두
  exact-once일 때만 열린다. 저장 model ID와 현재 query encoder fingerprint가 다르면 검색을 중단하며
  자동 provider 전환·fallback·재임베딩은 하지 않는다.
- 상세조회는 `semantic-detail/v3`의 `frame` 하나만 제공한다. 함수는 검증된 Summary/Stereotype,
  구조화 Rule과 그 Example, 사용자 작성 callee, READS/WRITES로 연결된 Table/Column 문맥을 반환한다.
  Rule의 내부 `AFFECTS_TABLE`, 원문, 좌표/evidence ID, storage bookkeeping과 중복 `rule_examples` 투영은
  반환하지 않는다.

## 11. write와 원자적 공개

`AnalysisScope`와 `AnalysisGeneration`은 generation 제품 stage와 분리된 control-node ledger가 소유한다.
generation 생성 시 scope의 `publication_version`과 optional `active_generation_id` presence/value를 읽어
expected initial pre-state로 봉인하고, 새 generation의 `building` control record를 같은 ledger에 기록한 뒤
read-back hash를 비교한다. 이 initial state에서 scope version만 1 증가시키고 pointer·new/previous generation
상태는 그대로 둔 `expected_locked_control_pretransition_hash_by_identity`도 CAS 전 결정론적으로 봉인한다.

각 제품 stage는 DB 비종속 `ProductWriteBatch`를 먼저 만든다.

```yaml
stage_name: <닫힌 제품 stage 이름>
analysis_scope_id: <scope ID>
generation_id: <building generation ID>
node_create_operations:
  - operation_key: [<stage>, node_create, <canonical node identity>]
    present_properties: {<stage-owned key>: <Neo4j primitive 또는 homogeneous primitive list>}
node_property_set_operations:
  - operation_key: [<stage>, node_property_set, <canonical node identity>, <property key>]
    value: <Neo4j primitive 또는 homogeneous primitive list>
relationship_create_operations:
  - operation_key: [<stage>, relationship_create, <canonical relationship identity>]
    present_properties: {<stage-owned key>: <Neo4j primitive 또는 homogeneous primitive list>}
required_operation_key_set: [<canonical operation key>]
required_property_key_set_by_identity:
  <canonical identity>: [<present property key>]
required_product_record_hash_by_identity:
  <canonical identity>: <canonical_product_record_hash_v2>
```

`canonical_product_record_hash_v2`는 label/relationship type, canonical identity, 그리고 그 label/type의
닫힌 schema property key 각각에 대해 UTF-8 key와 `absent|present` tag를 포함한다. present이면
boolean/integer/finite-float/string/homogeneous-list type tag·길이·값을 붙인다. list 순서를 보존하며 map,
nested list, heterogeneous list, null은 거부한다. embedding은 model ID, dimension, 선언 순서의
IEEE-754 finite-float bytes를 포함한다. 따라서 property absence는 값이 있는 상태와 다른 hash이고,
DB의 예상 밖 property도 closure mismatch다.

writer는 existing building generation에만 operation key로 멱등 증분 기록한다. node identity의 create 뒤
서로 다른 stage가 같은 node를 patch할 수 있지만 `(identity, property_key)` owner는 정확히 하나여야 한다.
generation 제품에서 property removal은 금지하며 optional absence는 처음부터 쓰지 않는 것으로 표현한다.
각 write query는 같은 transaction 안에서 실제 property presence와 값을 `RETURN`하고 canonical hash를
계산한다.

```text
required operation-key set = written operation-key set
required property-key set by identity = returned present property-key set by identity
required property-hash map = returned property-hash map
all write batches are disjoint by operation key and their union is the required operation-key set
```

`table_meanings` stage는 FunctionTableUsage와 TableDescription의 두 completion signal과 그 write ledger가
모두 닫혀야 완료다. finalizer는 모든 제품 stage의 operation key와 `(identity, property_key)` owner를
합쳐 create+disjoint patch를 허용하고, operation 중복·property-owner 충돌·미기록 operation만 거부한다.

publisher는 다음 순서를 한 Neo4j transaction에서 수행한다.

1. `AnalysisScope.publication_version`과 expected prior active pointer를 조건으로 CAS update를 먼저 실행해
   scope write lock을 얻는다. 매치된 scope가 하나가 아니면 동시 publisher 충돌로 rollback한다.
2. lock 아래 scope/new building/optional previous active를 다시 읽는다. scope version은 정확히 1 증가했고
   pointer와 new/previous generation 상태는 아직 전환 전인
   `expected_locked_control_pretransition_hash_by_identity`와 read-back hash를 비교한다.
3. building generation 제품 전체의 identity, property presence/value hash, endpoint closure, function 의미,
   Rule/Example, embedding을 다시 읽어 immutable product expected map과 exact equality를 검사한다.
4. previous active를 superseded로, new building을 active로, scope pointer를 new generation으로 바꾼다.
5. scope/new/previous control record를 다시 읽어 expected post-state hash, active count 1, pointer equality,
   incremented publication version을 확인한 뒤 commit한다.

initial/locked-pretransition/post-state expected hash와 실제 read-back hash는 제품 hash와 별도 ledger에 둔다. 어느 검사든 실패하면
CAS update를 포함한 transaction 전체를 rollback하며 실패한 building generation은 공개하지 않는다.

## 12. 완전성·정확도·성능 합격

각 결정 단계는 다음 exact-once 장부를 가진다.

```yaml
required: [<정본 key>]
legitimate_disposition_sets:
  <성공·정상 미확정·명시 제외>: [<key>]
produced: [<legitimate_disposition_sets의 서로소 합집합만>]
failure_keys: []
missing: []
duplicate: []
unexpected: []
pass: true
```

정상 미확정은 skip이 아니다. `external`, `configuration_dependent`, `ambiguous`, `dynamic`, `unsupported`,
의미 `insufficient`처럼 소유 계약에 맞는 disposition으로 남는다. operational/schema/transport failure와
`missing|duplicate|unexpected`는 produced union에 넣지 않고 별도 error set으로 두며 하나라도 있으면
`pass=false`다.

수정 중에는 영향받은 작은 실제 SKT source와 집중 red만 확인한다. 관련 책임 slice가 모두 닫힌 마지막
안정 hash에서 `zapamcom13010.c`와 `zapamcom10150.c`, 합계 1,503줄만 한 번 분석하며 load,
Parser/AST, link, prompt build, queue, prefill, decode, validation, postprocess/embedding, product stage
write/finalize, publish transaction을 분리 측정한다. immutable `AnalysisHealth`는 publish 전 closure와
그때까지의 wall만 저장한다. commit 뒤 전체 wall·publish lock wait·CAS conflict·transaction/read-back/
commit 시간은 content-addressed post-commit run artifact로 확정한다. 속도를 위해 선택 파일 안의
required population을 truncation·Top-N·sampling·skip·임의 token/item 상한·의미 fallback으로 줄이지
않는다. 실행하지 않은 5만 LOC 전체 처리 속도는 미검증으로 남긴다.

최종 합격은 세 층을 모두 요구한다.

1. 자동 회귀와 schema/parser/graph 검증
2. 모든 decision·invocation·write의 exact-once 및 결정론 전수 회계
3. 봉인 gold를 대상으로 한 직접 의미 판정

Python 표기 구문 성공이나 0 누락 장부만으로 의미 정확도·제품 합격을 선언하지 않는다.

## 13. 사람용 완전한 예시의 위치

다음 문서는 이 계약을 쉬운 자연어와 실제 값으로 보여 주는 사람용 정본이다.

```text
D:\work\robo\docs\작업보고내용\2026-08-26-Analyzer-최종-구조-고해성사.html
```

- §2~§9: Parser→Analyzer 입력·연결·Scanning·함수 문맥
- §10: 실제 공통 prefix, 입력 section 순서, local key와 금지 내부값
- §11: Summary system/user/schema/raw/Analyzer 결과 전체
- §12: FunctionTableUsage 및 TableDescription system/user/schema/raw/Analyzer 결과 전체
- §13: Rule system/user/schema/raw/Analyzer 결과와 Example 전체
- §14: exact invocation, KV cache, batching, 단일 transport attempt, large Summary SP/SO 전체
- §16~§17.7: 제품 node/relationship, ProductWriteBatch, writer, finalizer, publisher와 함수별 실행 순서
- §18: 작은 실제 좌표→마지막 SKT 전체 한 번의 검증 절차

HTML 예시가 이 wire에서 정의하지 않은 제품 field·숨은 LLM 호출·의미 fallback을 추가할 수 없다. 반대로 HTML이 쉬운 설명을 위해 내부 ID 값을 `<실제 ID>`로 표시해도 field 자체를 생략해서는 안 된다.
