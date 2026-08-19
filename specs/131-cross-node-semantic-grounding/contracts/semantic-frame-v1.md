# `semantic-frame/v1` 계약

**Task**: T131-20
**Status**: Normative
**Applies to**: Framework, DBMS, Analyzer graph/API/MCP, Architect normal generation path

## 1. 목적과 권위

`semantic-frame/v1`은 Analyzer가 만든 자연어 의미의 단일 정본이다. parser·linker·결정론
코드가 구조와 적용 가능한 의미 칸을 먼저 확정하고, LLM은 그 칸의 짧은 의미와 충분성만
채운다.

권위 순서는 다음과 같다.

1. parser AST identity, owner, coordinates, condition, statement와 order
2. 결정론 linker의 CALL, direct/effective R/W, table/column, symbol, external-evidence 관계
3. 위 사실에서 조립한 `structural-profile/v1`과 `SlotSpec`
4. 검증을 통과한 LLM `SlotValue`
5. frame에서 파생한 legacy `summary`, `description`, `logical_name`, `rule.nl`, question

LLM은 1~3을 수정하거나 echo하지 않는다. normalizer도 LLM 문자열에서 ID·좌표·관계·조건을
역추출하지 않는다.

## 2. 공통 봉투

```json
{
  "schema": "semantic-frame/v1",
  "profile_schema": "structural-profile/v1",
  "target": {
    "id": "code:orders.OrderService:save",
    "type": "METHOD",
    "owner_id": "code:orders.OrderService",
    "file": "src/orders/OrderService.java",
    "start_line": 41,
    "end_line": 78
  },
  "profile": {
    "evidence": {},
    "child_refs": [],
    "ordered_flow": [],
    "callee_refs": [],
    "data_object_refs": [],
    "symbol_refs": [],
    "unsupported": []
  },
  "slots": {}
}
```

### 2.1 `target`

| 필드 | 생산자 | 규칙 |
|---|---|---|
| `id` | parser/graph identity | full ID 그대로. 분해·축약·재생성 금지 |
| `type` | parser `label` | 실제 AST type 그대로 |
| `owner_id` | parser/container | 빈 값은 실제 부재일 때만 허용 |
| `file/start_line/end_line` | parser | absolute-file-line 좌표. 셋 중 일부만 있는 상태 금지 |

signature, name, params, return type은 prompt evidence에는 포함할 수 있으나 frame identity를
바꾸지 않는다.

### 2.2 `profile`

`StructuralProfile`은 현재의 새 AST가 아니다. 기존 parser/linker 정본을 중복 없이 한 target
관점으로 조립한 immutable projection이다.

- `evidence`: `evidence_id -> EvidenceFact` exact map
- `child_refs`: 직접 구조 자식 evidence ID. 재귀 본문을 포함하지 않는다.
- `ordered_flow`: 실행/등장 순서가 parser로 입증된 직접 fact ID. 순서를 모르면 빈 배열이다.
- `callee_refs`: exact CALL/callee fact ID. 동일 callee가 여러 좌표에서 호출되면 좌표 fact는
  각각 두고 callee interface claim은 한 번만 둔다.
- `data_object_refs`: direct/effective를 구분한 R/W fact ID
- `symbol_refs`: referenced declaration fact ID
- `unsupported`: parser/coverage residue가 판정한 unsupported kind와 좌표. LLM이 보완하지 않는다.

## 3. stable ID

### 3.1 target digest

```text
target_digest = sha256(UTF8(target.id))[0:12]
```

source body, 현재 summary, 실행 시각, DB 이름은 digest 재료가 아니다. 같은 code identity는 다시
분석해도 같은 digest를 얻는다.

### 3.2 evidence ID

```text
EV:<target_digest>:<KIND>:<coordinate-or-ref-digest>
```

- target: `EV:<d>:TARGET:0`
- control/statement/call: 절대 시작 줄을 쓰며 같은 줄 중복은 결정론 ordinal을 붙인다.
- table/callee/symbol: full referenced ID의 SHA-256 앞 12자를 쓴다.
- 같은 evidence identity가 한 profile 안에 두 번 나오면 조립 실패다.
- evidence ID는 source text hash가 아니다. 본문 변경 감지는 별도 run/input provenance가 소유한다.

### 3.3 slot ID

```text
SL:<target_digest>:<FAMILY>:<ROLE>:<anchor>
```

`anchor`는 `TARGET`, 절대 줄, referenced identity digest 중 하나다. slot ID는 prompt의 required
property key, graph claim identity, MCP/Architect reference에서 끝까지 보존한다.

## 4. `EvidenceFact`

모든 fact는 다음 공통 필드를 가진다.

```json
{
  "id": "EV:...",
  "kind": "CONTROL",
  "owner_id": "code:...",
  "file": "src/a.c",
  "start_line": 12,
  "end_line": 18,
  "attributes": {}
}
```

허용 `kind`와 attributes는 닫힌 목록이다.

| kind | 필수/허용 attributes |
|---|---|
| `TARGET` | `type`, `signature`, `name`, `params`, `return_type` |
| `CONTROL` | `node_id`, `type`, `role`, `condition`, `condition_timing`, `parent_ref`, `complement_of`, `implicit_false` |
| `STATEMENT` | `node_id`, `type`, `expression`, `target`, `operator`, `effects`, `terminates` |
| `CALL` | `callee_id`, `callee_name`, `scope`, `recursive`, `arguments_source`, `result_target` |
| `DATA_OBJECT` | `table_id`, `access`, `operation`, `direct`, `resolution`, `qualified_columns` |
| `SYMBOL` | `symbol_id`, `name`, `scope`, `data_type`, `declared_value`, `value_semantics`, `initialization_sources` |
| `EXTERNAL` | `chunk_id`, `semantic_kind`, `provenance`, `matched_key` |
| `RULE` | `anchor_line`, `role`, `condition`, `effects`, `when`, `source_refs`, `type`, `condition_timing`, `complement_of`, `implicit_false`, `nested_control`, `fallthrough_to`, `at_label`, `after_control` |
| `TABLE` | `table_id`, `db`, `schema`, `name`, `source_description`, `sample_refs` |
| `COLUMN` | `table_id`, `name`, `source_description`, `usage_lines`, `ownership` |
| `MODULE` | `module_id`, `type`, `member_refs`, `comment_ref` |

`arguments_source`와 `result_target`은 parser 또는 이미 검증된 statement ownership이 제공할 때만
채운다. 현재 parser가 호출명·좌표만 제공하면 CALL 자체는 만들되 두 필드는 빈 값이며,
`ARGUMENT_ROLE`/`RESULT_USE` slot은 source evidence가 해당 범위에 있을 때만 적용한다. 이름을
보고 인자나 결과를 추측하지 않는다.

`EXTERNAL` fact에는 normal MCP packet용 본문을 넣지 않는다. Analyzer 내부 LLM prompt는 이미
연결된 chunk의 bounded text를 별도 evidence payload로 볼 수 있지만, 저장 frame에는 chunk ID,
kind, provenance, 좌표만 남긴다.

## 5. `SlotSpec`과 `SlotValue`

각 slot의 metadata는 결정론 코드가 소유한다.

```json
{
  "slot_id": "SL:...",
  "family": "FUNCTION",
  "role": "RESPONSIBILITY",
  "target_ref": "EV:...:TARGET:0",
  "evidence_refs": ["EV:..."],
  "meaning": "...",
  "status": "sufficient",
  "missing_context": []
}
```

LLM wire에는 required slot key 아래 세 필드만 노출한다.

```json
{
  "meaning": "...",
  "status": "sufficient|partial|insufficient",
  "missing_context": []
}
```

교차 필드 계약:

- `sufficient`: `meaning` non-empty, `missing_context=[]`
- `partial`: grounded `meaning` non-empty, `missing_context` 1~8개
- `insufficient`: `meaning=""`, `missing_context` 1~8개
- `missing_context`는 필요한 evidence kind와 대상을 구체적으로 말한다.
- LLM은 `slot_id/family/role/target_ref/evidence_refs`를 출력하지 않는다.

## 6. bounded dynamic family

frame planner는 실제 구조 fact가 요구하는 family만 shallow하게 만든다. 모든 target에 모든 family를
강제하지 않는다.

### 6.1 target family

| family | slot role | 적용 조건 | evidence 최소 집합 |
|---|---|---|---|
| `FUNCTION` | `RESPONSIBILITY` | routine target | TARGET + direct 구조 facts |
| `FUNCTION` | `INPUT_ROLE` | 선언 interface 또는 읽기 dependency 존재 | TARGET + relevant SYMBOL |
| `FUNCTION` | `GUARDS_AND_BRANCHES` | live decision/complement/handler/early-exit 존재 | TARGET; 적용 여부는 결정론 branch inventory |
| `FUNCTION` | `PROCESSING_EFFECTS` | direct CALL/RW/assignment/transaction/output mutation 존재 | TARGET; 적용 여부는 direct 구조 facts |
| `FUNCTION` | `RESULT` | return type/expression/output mutation 존재 | TARGET + RETURN/SET facts |
| `STATEMENT` | `DIRECT_ACTION` | routine이 아닌 분석 target 또는 단일 structural action | target statement fact |

routine target의 위 다섯 역할은 최대치이며, 실제 구조축이 없으면 해당 slot을 만들지 않는다.
`RESPONSIBILITY`는 목적 경계, `INPUT_ROLE`은 실제 interface 사용, `GUARDS_AND_BRANCHES`는 결과를
바꾸는 조건별 흐름, `PROCESSING_EFFECTS`는 호출·조회·변경·transaction과 외부 상태 변화,
`RESULT`는 반환/출력과 그 정확한 조건을 소유한다. 같은 사실을 여러 role에 복제하지 않는다.

선언됐지만 body에서 사용되지 않는 파라미터와 write-only/output-only 파라미터는 현재 source가
입증한 완전한 interface 사실이다. `INPUT_ROLE`은 이를 그대로 밝히고 `sufficient`로 종료하며,
호출자 문맥이나 원래 의도한 업무 역할을 요구하지 않는다. 즉시 상수 반환만 하는 no-op routine도
현재 동작이 완전하므로 이름에서 예상한 검증 로직을 `missing_context`로 만들지 않는다.

`FUNCTION.ORDERED_FLOW` aggregate meaning slot은 만들지 않는다. 실행 순서의 단일 진실은
`profile.ordered_flow`이고, 배열의 모든 ID는 `profile.evidence`의 exact 구조 fact로 해소되어야
한다. 추가 자연어 해석이 적용되는 fact만 세부 semantic slot을 가지며, 단순 대입처럼 구조
속성 자체가 완전한 fact에 LLM slot을 강제하지 않는다. 전체 흐름을 한 문자열로 다시 만들면
좌표별 의미와 중복되고, 대형 함수에서는 문자열 상한 때문에 뒤쪽 흐름이 조용히 누락된다.

### 6.2 Rule structural family — LLM 0

Rule은 semantic slot family가 아니다. 모든 required row는 parser/linker가 소유한 RULE fact와
연결 CONTROL/STATEMENT/CALL/DATA_OBJECT fact에서 `cond/then/when`을 결정론 물질화한다. legacy
`Rule.nl`은 빈 문자열이다. 검색·표시용 사람이 읽는 텍스트가 필요하면 같은 `cond/then`을 해석이나
업무 용어 추가 없이 렌더하며 별도 저장 의미가 되지 않는다.

실제 APAS code-only row를 대상으로 한 고정 replay에서 모델은 `strncmp(...,"N")==RC_NRM`의 극성을
바꾸고, 분기에 없는 `gubun` 조건과 opaque identifier의 업무 뜻을 창작했다. schema와 기존 grader는
문장 의미 진실을 증명하지 못했다. 반면 Architect는 exact RULE evidence ID, condition/effects, Example,
callee/table/column claim을 소비하며 Rule prose를 요구하지 않는다. 따라서 중복 prose 생성과 이를 위한
provider wire/schema/normalizer/batch는 계약에서 제외한다.

decision의 condition/극성,
true·false path, loop timing/termination, handler/propagation, entry/continuation/exit의 직접 효과는
이미 같은 anchor의 `RULE` fact attributes와 연결된 `CONTROL`/`STATEMENT` facts가 소유한다.
이를 `DECISION.ROLE`, `TRUE_OUTCOME`, `FALSE_OUTCOME`, `LOOP.*`, `EXCEPTION.*`,
`STATEMENT.DIRECT_ACTION` 같은 여러 prose slot으로 다시 생성하지 않는다.

`Rule.cond/then/when`, EXAMPLE, 행 type/timing/complement/nesting/fallthrough/label/continuation은
모두 결정론 사실이 소유한다. callee/table/column의 업무 의미는 각각의 원래 semantic slot에 한 번만
존재하고 Rule evidence가 ID로 연결한다. Rule이 그 의미를 복사해 두 번째 claim을 만들지 않는다.

EXAMPLE leaf는 accepted Rule의 검증된 `cond/then` truth를 그대로 보존한다. 과거 provider가 Example을
생성하던 시절의 출력 길이 상한을 결정론 산출물에 재사용하지 않는다. 조건을 길이로 자르거나 버리지
않으며, opaque source predicate를 Analyzer 문자열 분해로 짧은 조건처럼 재해석하지 않는다. 조건 또는
직접 outcome이 없는 Rule만 EXAMPLE을 만들지 않는다.

### 6.3 call family

| family | slot role | 적용 조건 |
|---|---|---|
| `CALL` | `PURPOSE` | exact call coordinate/callee가 존재 |
| `CALL` | `ARGUMENT_ROLE` | argument source가 parser/source evidence로 bounded 됨 |
| `CALL` | `RESULT_USE` | assignment/return/condition이 해당 call 결과를 소유함 |
| `CALL` | `SIDE_EFFECT` | linked R/W/API/external contract가 side effect를 입증함 |

callee 내부 책임을 caller CALL slot에 복사하지 않는다. callee frame은 한 번 전달하고 CALL slot은
호출 경계에서 현재 target이 한 일만 말한다.

### 6.4 data operation family

| operation | family | slot roles |
|---|---|---|
| READ/SELECT | `SELECT` | `PURPOSE`, `FILTER`, `RESULT_USE` 중 근거가 있는 것 |
| INSERT | `INSERT` | `TARGET`, `CONTENT`, `CONDITION`, `TRANSACTION_EFFECT` 중 근거가 있는 것 |
| UPDATE | `UPDATE` | `TARGET`, `CONTENT`, `CONDITION`, `TRANSACTION_EFFECT` 중 근거가 있는 것 |
| DELETE/TRUNCATE | `DELETE` | `TARGET`, `CONDITION`, `TRANSACTION_EFFECT` 중 근거가 있는 것 |
| MERGE | `UPSERT` | `TARGET`, `MATCH_ACTION`, `NO_MATCH_ACTION`, `CONDITION`, `TRANSACTION_EFFECT` 중 근거가 있는 것 |

`TARGET`은 물리 table ID를 번역한 문장이 아니라 현재 연산에서 그 table이 맡는 입증된 역할이다.
COMMIT/ROLLBACK은 parser statement 좌표가 있을 때만 transaction slot 근거가 된다.

### 6.5 data model and aggregate family

| family | slot role | 적용 조건 |
|---|---|---|
| `TABLE` | `ROLE` | DDL/comment/code usage/sample 중 하나 이상 존재 |
| `TABLE` | `DISPLAY_NAME` | 번역이 아니라 근거에서 명명할 수 있을 때. 근거가 없으면 slot 자체는 `insufficient` |
| `COLUMN` | `ROLE` | DDL/comment/qualified usage/sample 중 하나 이상 존재 |
| `COLUMN` | `DISPLAY_NAME` | 위와 동일 |
| `MODULE` | `RESPONSIBILITY`, `DISPLAY_NAME`, `ROLE` | member frames가 모두 terminal이고 coverage가 명시됨 |
| `COMPONENT` | `DISPLAY_NAME` | deterministic membership과 terminal member frames 존재 |

TABLE/COLUMN의 물리명만 있고 설명·usage가 없으면 이름 번역을 생성하지 않는다. `ADBA_VAL`,
`ETC` 같은 사례는 `insufficient`가 정상이다. `COMPUTE`, `TAGSN`처럼 DDL/usage가 있는 항목은
population에서 빠지면 제품 결함이다.

## 7. frame planner의 유한성

- target/profile은 direct facts만 담는다. child frame 본문을 재귀 inline하지 않는다.
- 동일 `(family, role, anchor)` slot은 정확히 한 번만 존재한다.
- family별 role 수는 위 닫힌 목록의 부분집합이다.
- repeated callee/table/symbol identity는 evidence fact 한 개 + 좌표 occurrence refs로 표현한다.
- batch token cap은 source node cap과 별개다. frame 수 때문에 batch가 넘치면 기존 planner가
  node 단위로 분할하며 slot을 자르거나 생략하지 않는다.
- schema가 provider 한계를 넘으면 node/batch를 더 작게 나눈다. 각 호출은 서로 다른
  schema/evidence closure와 request-local key를 가지며, canonical slot ID로 exact merge한다.
  동일 payload를 내용 재시도하지 않는다.

## 8. normalizer와 validator

순서는 고정한다.

1. branch inventory의 모든 required row는 `required_rule_rows` 단일 소유자에서 계산하고,
   `materialize_rule_items`가 exact `cond/then/when` Rule을 LLM 0으로 물질화한다.
   `rule-structures/deterministic-rule-v1` 감사 산출물은 row coverage, 중복, 재물질화 동등성을
   검증한다. 기존 산출물에 이 디렉터리가 없으면 역사 run으로만 읽을 수 있지만, 디렉터리가
   존재하면서 비었거나 손상됐으면 실패한다.
2. Rule 이외의 semantic slot은 dynamic schema가 required slot key와 leaf union을 강제한다.
3. provider key는 canonical ID가 아닌 request-local private key다. key namespace는 현재 slot
   assignment digest를 포함해 source의 일반 코드값과 충돌하지 않게 하며, meaning 또는
   missing_context에 현재 요청의 exact private key가 나타나면 그 leaf만 `insufficient`로 격리하고
   좌표 있는 quality finding으로 표면화한다. 문자열 치환으로 정상 문장처럼 만들지 않는다.
4. normalizer가 `SlotSpec` metadata와 validated `SlotValue`를 결합한다.
5. identity validator: target/profile/slot/evidence ID uniqueness와 exact coverage
6. ownership validator: 모든 evidence owner가 target 또는 명시된 aggregate/member 관계인지 검사
7. grounding validator: slot type에 허용된 evidence kind만 참조하는지 검사
8. completeness validator: planner의 applicable slot이 모두 terminal인지 검사
9. contradiction validator: READ/WRITE, condition operand/operator/literal, return/effect,
   transaction/exception 구조와 meaning의 구조 토큰 변경을 검사
10. projection validator: legacy field가 frame 밖 자연어를 추가하지 않았는지 검사

검증 실패는 해당 slot/target을 `rejected` analysis finding으로 남기며 조용히 빈 projection을
저장하지 않는다. provider transport 실패와 `insufficient`는 다른 상태다.

## 9. legacy projection

| legacy 필드 | frame source |
|---|---|
| `CodeNode.summary` | summary concern의 target-owned FUNCTION 5역할 또는 STATEMENT slot을 고정 순서로 dedupe해 조립. 상세 data-operation claim은 별도 slot로 남고 summary 완료 뒤 재투영하지 않음 |
| `CodeNode.stereotype` | frame target role classification. write contradiction gate 유지 |
| summary status/missing | required target projection slot의 least-sufficient aggregate와 deduped missing |
| `Rule.nl` | 빈 호환 필드. 사람용 표시는 RULE fact의 cond/then 구조 렌더링 |
| `Question` | partial/insufficient slot의 missing_context를 `(slot_id, evidence kind)`에 묶은 projection |
| table `usage_summary` | 해당 target의 data-operation slot projection |
| Table/Column `logical_name/description` | DISPLAY_NAME/ROLE slot projection |
| Module summary/name/role | MODULE slots |
| Component name | COMPONENT:DISPLAY_NAME slot |

projection 함수 외의 LLM call이 위 필드를 직접 쓰면 계약 위반이다.

## 10. 저장 계약

- CodeNode, Table, Column, Module에는 `semantic_frame_json`과 `semantic_frame_schema`를 저장한다.
- `semantic_frame_json`은 canonical JSON(`sort_keys=True`, compact separators)이다.
- legacy fields는 frame과 같은 transaction/batch 안에서 projection으로 저장한다.
- graph writer는 이전 frame/legacy 의미를 함께 교체한다. 새 run에서 frame이 실패했는데 이전
  의미만 남는 stale truth를 허용하지 않는다.
- run artifact에는 profile, model raw/parsed, normalized frame, validation findings를 target ID로
  연결한다. raw source는 기존 input/audit artifact가 소유하며 frame에 복제하지 않는다.

## 11. prompt와 structured output

- system prefix: TASK/authority/relation/decision/examples/self-check와 닫힌 family 의미를 안정적으로 유지
- variable suffix: TARGET, profile evidence, applicable slot specs, bounded source/internal evidence
- 모델은 slot leaf만 출력하며 target/evidence/condition/table/callee ID를 다시 생성하지 않는다.
- JSON Schema/function calling은 구조를 강제할 뿐 의미 진실을 보장하지 않으므로 deterministic
  validators와 3층 검증을 생략하지 않는다.
- stable prefix 여부는 실제 serialized prompt bytes로 검증한다. 서버 metrics가 없으면 cache hit을
  추정하지 않는다.

## 12. MCP/Architect projection

정상 packet:

```json
{
  "schema": "semantic-frame/v1",
  "target": {},
  "claims": [
    {
      "slotId": "SL:...",
      "type": "FUNCTION.RESPONSIBILITY",
      "meaning": "...",
      "status": "sufficient",
      "evidenceRefs": ["EV:..."]
    }
  ],
  "evidence": [
    {"evidenceId": "EV:...", "kind": "RULE", "ownerId": "...", "coordinates": {}}
  ],
  "missingContext": []
}
```

- complete source body, source hash, 새 excerpt는 없다.
- 같은 claim/evidence/callee/table identity는 한 번만 있고 GWT scenario가 ID로 참조한다.
- Architect의 Given/When/Then scalar는 cited claim/evidence로 입증돼야 한다.
- `partial|insufficient` claim은 동일 packet에서 source를 다시 읽어 보완하지 않는다.
- 별도 audit/debug 도구는 좌표로 source를 조회할 수 있으나 정상 생성 prompt와 provenance에
  source body를 합류시키지 않는다.

## 13. 호환과 폐기

- `gwt-evidence/v2` complete-source/hash/source-first 계약은 폐기한다.
- 기존 Rule/Example 구조와 stable coordinate는 frame evidence로 흡수해 보존한다.
- A Python 관계식, parser/linker 관계식, 기존 scheduling/concurrency는 변경하지 않는다.
- legacy 문자열 property는 소비자 이행 기간 동안만 projection으로 남고 자연어 생산 권한은 없다.

## 14. 필수 검증

1. 자동: model/schema/planner/ID/validator/projection/storage/API/MCP/Architect tests
2. 결정론 정답지: applicable slot population, exact evidence refs, no-source/no-duplicate,
   condition/effect/RW ownership
3. 직접 의미 판정: 봉인된 Framework/DBMS 혼합 30~40개에서 omission/fabrication/contradiction과
   slot sufficiency를 원문에 대조

실제 Analyzer는 전략별 재사용 DB(`framework|dbms`)와 `_runs/<strategy>`에서만 실행하며 검증마다
새 DB 이름을 만들지 않는다. Analyzer가 세 층 모두 합격한 뒤에만
Architect Simplified/Detailed actual 경로를 실행하며 `accept/apply`는 호출하지 않는다.
