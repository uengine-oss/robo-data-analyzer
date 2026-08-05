# spec 110 단일 인계 정본 — 고정 20 의미 정확도·폭주·구조 증거

> 최초 실행 상태 기준 시각: 2026-07-30 19:26 KST
> 최종 요구사항 갱신: 2026-07-31 KST
> 저장소: `D:\work\robo\project\robo-data-analyzer`
> branch / HEAD: `main` / `a3779491be6fc962834a95503332a39f73afa4dd`
> 현재 단계: 최종 실제 재분석과 직접 판정은 끝났다. 다음 작업은 전체 SOURCE를 유지한
> 구조적 입력 분할, 고정 Rule/Example 상한 제거, evidence 기반 검증의 SDD·구현·테스트다.
> 추가 전체 재실행은 사용자 지시로 금지되어 있다.
> 이 문서의 지위: spec 110 작업을 이어가는 **단일 인계 정본**이다.

## 1. 문서 목적과 고립 환경 사용 계약

이 문서는 이전 대화, 작업자 기억, 외부 검색, 추가 질문 없이 spec 110 작업을 이어가기 위한
실행 정본이다. 다음 작업자는 이 문서와 이 문서가 가리키는 저장소·로컬 실행 증거만 사용한다.
`verification.md` 등 다른 문서는 세부 증거와 과거 판정 장부이지 별도 인계 정본이 아니다.

재개 시 반드시 다음 순서로 원문을 다시 읽는다.

1. `C:\Users\roede\.codex\memories\principles.md`
2. `C:\Users\roede\.codex\memories\manual.md`
3. `C:\Users\roede\.codex\memories\speckit.md`
4. `D:\work\robo\AGENTS.md`
5. `D:\work\robo\project\robo-data-analyzer\.specify\memory\constitution.md`
6. 이 `HANDOFF.md`
7. `spec.md`, `plan.md`, `tasks.md`, `verification.md`
8. 현재 코드, `git status --short`, 관련 diff, 필요한 실제 실행 증거

적용한 원칙은 다음과 같다.

- `principles.md §1 — “모든 판단은 검증된 사실을 근거로 한다.” — 현재 코드·Git·실행
  로그·manifest를 우선하고 대화 기억을 사실로 쓰지 않았다.`
- `principles.md §3 — “실제 문제를 해결하고, 임시방편으로 넘어가지 않는다.” — 누락을
  재시도·별도 LLM·부분 JSON·특정 코퍼스 hard-code로 맞추지 않는다.`
- `principles.md §6 — “코드·데이터 흐름을 추적할 때는 원문→AST→LLM 로그→그래프
  관계를 순서대로 검증한다.” — 필드별 최초 실패 단계를 이 순서로 고정한다.`
- `principles.md §8 — “응답은 가능하면 구조화한다.” — 현재 Pydantic, transport,
  Neo4j 구조와 미구현 제안 구조를 명시했다.`
- `principles.md §9 — “장기 작업은 자동 컨텍스트 압축이나 대화 기억에 의존하지
  않는다.” — 확정 요구·사실·정정·실패·결정·다음 작업을 이 문서에 누적한다.`
- `principles.md §10 — “검증과 개선은 반복 가능한 루프로 운영한다.” — 같은 고정
  20함수·219 claim·canonical 66 노드로 비교하되, 이번 최종 사이클 뒤에는 사용자 승인
  없이 재실행하지 않는다.`
- `speckit.md §5 — “실행 결과와 현재 코드 → 현행 contracts/constitution → TODO →
  과거 spec.” — 최종 실행 D와 현재 코드를 가장 높은 사실로 사용했다.`
- `.specify\memory\constitution.md I — Determinism-First — 구조 추출·ID·검증·병합은
  결정론 코드, 자연어 의미 해석만 LLM이 담당한다.`
- `.specify\memory\constitution.md VI — Schema Restraint — 폭주를 피하려고 자유키나
  깊은 반복 구조를 무작정 늘리지 않는다.`

문서 내 상태 표기는 네 가지로 엄격히 구분한다.

- **구현됨**: 현재 파일에서 확인한 코드.
- **테스트됨**: 명시한 명령과 결과가 존재함.
- **실제 실행 검증됨**: 최종 파이프라인 run과 read-back 증거가 존재함.
- **제안, 미구현**: 다음 설계 후보일 뿐 현재 동작이라고 가정하면 안 됨.

## 2. 한 문장 목표

2026-07-27 고정 gold를 기준으로 고정 20함수와 canonical 스키마 노드의 모든 LLM 생성
필드를 직접 비교하고, `원문/컨텍스트 → 프롬프트 → 모델 응답 → 파서/후처리 → Neo4j`의
최초 실패 지점을 증명해 범용 로직·프롬프트만 개선하며 폭주 피해를 제한한다.

### 2.1 다음 작업자가 실제로 해야 할 핵심

1. 최종 실패의 raw prompt/response와 현재 parser/branch/def-use 데이터를 다시 대조한다.
2. 함수 SOURCE를 줄·토큰 구간으로 자르지 않고 모든 의미 호출에 전체 함수 SOURCE를 유지한다.
3. SOURCE에서 `decision_groups`, `condition_tree`, `symbol_flow`, `effect_inventory`,
   `exit_inventory`, `fallback_inventory`를 결정론적으로 추출한다.
4. 입력 정보는 위 구조 블록으로 나누고 Summary, Rule/Example, Table Insight 호출에
   필요한 블록만 공통 SOURCE 뒤에 붙인다.
5. Rule 출력에 `evidence_refs`와 exact `mappings`를 추가한다.
6. validator가 evidence의 missing/unknown/duplicate, 조건값, AND/OR/부정,
   complement/default/fallback, effect/exit를 결정론적으로 검사한다.
7. 현재 Rule 48/Example 8 고정 상한을 제거하고 유한 evidence obligation 집합으로
   출력 유한성을 보장한다.
8. token budget, whitespace/repetition guard, delimiter·중복 검사는 폭주 피해 제한 장치로 유지한다.
9. stereotype과 glossary에도 구조 근거를 제공하되 결정론 코드가 새로운 업무 의미를 만들지 않는다.
10. SDD로 contract/spec/plan/tasks를 먼저 고정하고 구현·국소·반대 사례·전체 회귀를 수행한다.
    사용자 새 승인 전에는 전체 Analyzer와 Neo4j를 재실행하지 않는다.

## 3. 사용자 목적·배경·현재 증상·기대 결과

### 3.1 배경

- 2026-07-27 문서와 gold가 canonical 판정 기준이다.
- 2026-07-28의 별도 LLM judge 검증 방식은 폐기됐다.
- 매 재분석에서 미리 고정한 20함수를 추출하고, gold·원문·실제 prompt/response·최종
  graph를 Codex가 직접 눈으로 대조하는 것이 원래 작업이다.
- 219 active gold claim은 Rule/Example의 원자 의무 분모이며 전체 검증의 전부가 아니다.
- 함수의 `summary`, `search_text`, `logical_name`, `stereotype`, `domain_terms`,
  `table_insights`, `RULE`, `EXAMPLE`, `QUESTION`과 별도 FILE/TABLE/COLUMN,
  glossary/component 구조까지 각 소유권에 맞게 검증한다.

### 3.2 현재 증상

1. 입력 근거가 모두 들어와도 모델이 긴 조건·값 매핑을 누락·과압축하거나 잘못 조립한다.
2. 큰 dispatch 함수는 은행별 분기를 Rule로 과분할해 48개 비상 상한에 닿고 뒤쪽
   fallback을 잃었다.
3. 한 큰 MQ 함수는 모델이 미닫힌 괄호를 포함한 Rule 문자열을 두 번 생성해 Rule concern
   전체가 구조 검증에서 거절됐다.
4. summary와 rules 양쪽에서 whitespace flood가 발생한다. 용어 프롬프트만의 문제가 아니다.
5. 일부 stereotype은 완전한 입력에도 Router/Adapter/Query와 Helper 사이에서 흔들린다.
6. table insight는 컬럼 소유권 문제를 고쳤지만, 의미가 완전한 입력에서도 발송 지연 조회를
   메시지 복사로 바꾸는 모델 해석 오류가 남았다.
7. glossary synthesis 후보에는 물리 식별자가 일부 남는다.

### 3.3 기대 결과

- 누락을 “랜덤” 한 단어로 덮지 않고 결정론적으로 잡을 문제와 확률적 모델 출력 문제를 구분한다.
- 프롬프트 문제와 로직 문제를 분리해 최초 실패 지점을 보고한다.
- 변수명만 출력됐는지가 아니라 선언값·초기값·사용처·결과 연결이 prompt에 들어왔고 실제
  해석에 사용됐는지 검증한다.
- 의미가 비슷해도 조건 집합·예외·보류·반환 결과가 다르면 다른 주장으로 판정한다.
- 폭주 때 GPU가 계속 생성하지 않도록 빠르게 중단하되, 부분 JSON 채택이나 무작정 재시도로
  결과를 가장하지 않는다.
- 큰 함수를 임의 줄/토큰 구간으로 쪼개 문맥을 끊지 않는다. 전체 SOURCE를 유지하면서
  결정론 구조 증거를 더 명확하게 주는 방향을 우선한다.
- “입력을 쪼갠다”는 것은 함수 원문을 여러 독립 slice로 자른다는 뜻이 아니다. 동일한 전체
  SOURCE를 유지하고 `decision`, `condition`, `symbol`, `effect`, `exit`, `fallback`을
  독립 구조 블록으로 분리해 관심사별 호출에 필요한 블록만 추가한다는 뜻이다.
- 현재 `Rule max 48`과 `Rule별 Example max 8` 고정 상한은 제거한다. 단순 무상한 배열로
  바꾸지 않고, SOURCE에서 결정론적으로 추출한 유한 evidence 집합과 참조 완전성 검증으로
  생성의 유한성을 보장한다.

## 4. 포함 범위와 명시적 비범위

### 포함 범위

- 고정 20함수, active gold claim 219개의 직접 의미 판정
- 고정 20의 모든 함수 생성 필드
- canonical FILE 23, TABLE 7, COLUMN 36
- final glossary/component의 구조·근거·write와 gold gap
- prompt input, context compaction, 심볼 값, runtime contract, branch lineage
- structured output, parser, normalize/filter, fan-out/handoff, graph write/read-back
- Rule/Example 고정 상한 제거와 evidence 기반 유한성, domain term/Question 상한 재검토,
  whitespace/repetition guard
- 프롬프트 상호충돌, 로직 오류, 모델 해석 한계의 분리
- 현재 구현의 데이터 구조와 향후 구조 개선안

### 비범위

- 2026-07-28 LLM judge 또는 문자열 유사도 기반 의미 판정
- 별도 LLM 재작성·심판·repair pass
- 부분 JSON을 파싱해 “반값 결과”로 채택
- gold 문구 복사, 특정 함수·파일·테이블·코드값 hard-code
- 결과가 나올 때까지 happy-path 재시도
- 사용자 승인 없는 추가 전체 재분석
- GPU 서버 설정 변경. 현재 작업자는 GPU 서버 접근 권한이 없다.
- 임의 line/token chunking. 문맥 폐쇄를 증명하지 못한 source slicing은 금지한다.
- 독립 gold가 없는 module/component/glossary 문구를 닫힌 정확도 점수에 합산

## 5. 전체 배선과 데이터 구조

### 5.1 실행 배선

```text
23개 C source + 1개 DDL + gold/선정표
  → step1 DDL parse: TABLE 7 / COLUMN 73
  → step2 AST parse: FILE/FUNCTION/구문/변수/상수/타입
  → step4 scanner: CALLS, READS/WRITES, table op, codebase notes
  → step5 linking: call/table/symbol/external evidence 연결
  → step6 AnalysisContext: 원문 범위, R/W, callee, 심볼 값, 제어 구조
  → step7 함수별 3분할 LLM
       1) code_summary
       2) code_rules_examples
       3) code_table_insights (직접 R/W 있을 때만)
  → 결정론 검증/제한: schema, delimiter, R/W, DDL column allowlist, quality observation
  → CodeNode fan-in
  → Example Mapping builder: RULE/EXAMPLE/QUESTION/관계
  → step8 module/table/glossary/component 종합
  → Neo4j test + run artifact + immutable prompt/response log
  → 결정론 review packet 추출
  → 사람이 gold/source/output을 필드별 직접 판정
```

API나 UI는 이번 작업의 수정 대상이 아니다. 최종 graph는 Catalog/Fabric/Gateway/UI가
소비할 수 있으나 spec 110은 Analyzer 내부 의미 생성·저장 정확도까지를 다룬다.

### 5.2 현재 `AnalysisContext` 구조 — 구현됨

근거: `pipeline\models\analysis_context.py:32-157`.

```json
{
  "id": "code:<file>:<routine>",
  "signature": "<signature>",
  "file_path": "<absolute source path>",
  "start_line": 1,
  "end_line": 100,
  "reads": ["db:<table>"],
  "writes": ["db:<table>"],
  "callers": ["code:<caller>"],
  "callees": ["code:<callee>"],
  "referenced_symbols": [
    {
      "id": "<symbol id>",
      "name": "<symbol name>",
      "kind": "<constant|variable|...>",
      "data_type": "<type>",
      "declared_value": "<exact declared value>",
      "value_semantics": "<deterministically known semantics or empty>",
      "initialization_sources": ["<definition/assignment provenance>"]
    }
  ],
  "external_evidence": [
    {
      "id": "<chunk id>",
      "file_path": "<path>",
      "matched_key": "<link key>",
      "semantic_kind": "<kind>",
      "provenance": "<provenance>",
      "numbered_text": "<absolute-line-numbered text>",
      "start_line": 1,
      "end_line": 2,
      "coordinate_system": "absolute-file-line",
      "evidence_scope": "linked-external-context",
      "content_hash": "<sha256>"
    }
  ],
  "one_liner": null,
  "token_count": 0,
  "summary": null,
  "search_text": null,
  "is_in_cycle": false,
  "extra": {
    "control_subtree": [],
    "table_op": {},
    "ddl_columns_by_table": {},
    "prompt_columns_by_table": {},
    "rules": [],
    "questions": []
  }
}
```

`ReferencedSymbolContext`는 이름 목록이 아니다. `declared_value`와
`initialization_sources`를 실제 비교 길이·접두사·연결식·초기값 사용 위치에 역대입해야 한다.
심볼 이름만 복사하고 값과 결과 연결을 설명하지 않은 출력은
`unresolved-symbol-semantic` 의심 대상으로 본다.

### 5.3 현재 공통 prompt 입력 — 구현됨

근거: `three_split_llm_fanout.py:453-508`,
`llm\prompts\framework\_common.yaml:93-143`.

```json
{
  "id": "<target code id>",
  "signature": "<signature>",
  "code_body": "<budget 정책이 선택한 전체 대상 본문>",
  "raw_code_body": "<결정론 검증용 정확 원문>",
  "numbered_raw_code_body": "<절대 파일 줄번호가 붙은 원문>",
  "source_file": "<absolute path>",
  "source_range": "<start>-<end>",
  "source_status": "<complete/truncated 등>",
  "coordinate_system": "absolute-file-line",
  "codebase_notes": "<runtime/framework contract>",
  "module_intent": "<module pre-analysis 결과 또는 없음>",
  "referenced_symbols": "<위 심볼 배열의 compact JSON>",
  "external_evidence": "<외부 증거 compact JSON>",
  "callee_search_texts": [
    {
      "callee_id": "<callee>",
      "scope": "callee-interface-only",
      "search_text": "<callee search_text>"
    }
  ],
  "ancestor_context": "<DBMS range 조상 골격 또는 없음>",
  "reads": [
    {
      "table_id": "db:<table>",
      "description": "<DDL/source description>",
      "columns": ["<직접 소유가 입증된 DDL 컬럼>"],
      "sample_rows": []
    }
  ],
  "writes": [],
  "table_ops": {"db:<table>": "SELECT|INSERT|UPDATE|DELETE|MERGE|TRUNCATE"},
  "branch_index": "<absolute line, role, condition, nesting/complement lineage>"
}
```

`callee_search_texts`는 callee 인터페이스 보조 문맥이다. callee 내부 Rule이나 테이블을
caller의 직접 동작으로 복사하면 안 된다. 검색 임베딩 재료도 `search_text` 하나다.

### 5.4 현재 3분할 출력 — 구현됨

#### A. `code_summary`

근거: `code_analysis_output.py:55-113`, `code_summary.yaml:9-76`.

```json
{
  "search_text": "의미 검색용 상세 조건·대상·효과·실재 코드 표기",
  "domain_terms": [
    {
      "term": "원문에 실제 존재하는 고유 업무 표기",
      "meaning": "같은 대상의 활성 근거가 입증하는 뜻",
      "evidence": "표기와 뜻을 지지하는 원문의 연속 구절"
    }
  ],
  "logical_name": "형제 함수와 구별되는 짧은 업무 표시명",
  "summary": "목적·주요 처리·핵심 정상/예외 효과를 설명하는 1~3문장",
  "stereotype": "ontology_roles.yaml의 닫힌 값 하나"
}
```

- `domain_terms`는 현재 summary concern 안에 있다. 별도 네 번째 LLM 호출로 분리되어 있지 않다.
- `summary`와 `search_text`는 같은 근거에서 각각 생성하며 중복 자체가 오류는 아니다.
- `summary`는 짧은 사용자 설명, `search_text`는 정확한 검색 구별 정보다.
- 의미 검색은 `search_text`만 사용한다. Rule/Example/summary를 임베딩 재료에 합치지 않는다.
- `domain_terms` hard max는 10이며 목표 개수가 아니다. 0개가 정상일 수 있다.

#### B. `code_rules_examples`

근거: `code_analysis_output.py:159-312`, `code_rules_examples.yaml:7-83`.

```json
{
  "rules": [
    {
      "statement": "<조건 또는 적용범위>이면 <관측 결과>",
      "anchor_line": 123,
      "examples": [
        {
          "given": "사전 상태·입력",
          "when": "사용자·호출자의 행동",
          "then": "반환·상태·데이터·외부 I/O 결과",
          "affected_tables": [
            {
              "table": "db:<주입된 table id>",
              "op": "READ|INSERT|UPDATE|DELETE|MERGE|TRUNCATE 또는 생략"
            }
          ]
        }
      ]
    }
  ],
  "questions": [
    {
      "text": "제공 증거로 답할 수 없지만 결과 의미에 필요한 질문",
      "reason": "어떤 근거가 없는지"
    }
  ]
}
```

- **현재 구현 사실**: Pydantic Rule hard max는 48이고 Rule별 Example hard max는 8이다.
- **확정된 다음 변경**: 위 48/8 고정 상한을 제거한다. 64나 100으로 올리는 변경이 아니다.
- **대체 안전 계약**: 전체 SOURCE에서 결정론적으로 추출한 유한 evidence ID 집합, concern별
  token budget, whitespace/repetition guard, 정확 중복 검사, 미닫힌 구조 검증으로 유한성과
  폭주 안전을 보장한다.
- 전체 함수 Example max를 2로 두지 않는다. 고정 개수를 먼저 목표로 삼지 않는다.
- 보통 1~2개를 권장하지만 필요한 정상/대상 없음/오류/fallback/경곗값을 버리지 않는다.
- Rule 하나가 같은 판단축의 완전한 조건→결과 매핑을 담는 것은 정상이다.
  “있으면 갱신, 없으면 생성”은 모든 조건과 결과가 statement에 복원 가능하면 한 Rule이다.
- 독립 단계·다른 시점 효과·별도 종료 계약은 다른 Rule로 나눈다.
- Example은 Rule을 검증할 뿐 Rule에 없는 정책을 보충하지 않는다.
- 문자열의 괄호·대괄호·중괄호·백틱이 닫히지 않으면 전체 concern 결과를 거절한다.

#### C. `code_table_insights`

근거: `code_analysis_output.py:119-153`, `code_table_insights.yaml`.

```json
{
  "table_insights": [
    {
      "table": "db:<exact injected table id>",
      "role": "READ-only|WRITE-only|READ_WRITE|append-only|lookup|upsert",
      "usage_summary": "현재 함수가 이 테이블에 직접 한 일",
      "columns": [
        {
          "name": "<exact DDL column>",
          "logical_name": "컬럼 표시명",
          "description": "저장 값과 현재 사용 역할"
        }
      ]
    }
  ]
}
```

테이블 아래에 컬럼을 중첩하는 구조가 현재 정본이다. 테이블과 컬럼을 하나의 평탄 배열에
섞지 않는다. 컬럼 수가 많아도 소유 테이블 경계가 명확하며, deep nesting은 한 단계뿐이다.

### 5.5 `op` 확정 책임 — 구현됨

LLM이 `AffectedTable.op` 후보를 낼 수 있지만 최종값은 전적으로 LLM이 확정하지 않는다.
근거: `rw_table_filter.py:211-317`.

1. scanner의 `table_op`가 정확한 폐쇄 연산이면 scanner 연산이 최종값이며
   `op_source="SCANNER"`.
2. read-only 관계이면 최종 `READ`, `SCANNER`.
3. write-only인데 scanner가 연산 종류를 모르면 LLM 후보가 유효한 쓰기 op일 때
   `LLM_INFERRED`; 아니면 `UNKNOWN/UNRESOLVED`.
4. read+write인데 정확 op를 확정하지 못하면 `READ_WRITE/UNKNOWN/UNRESOLVED`.
5. 직접 R/W 밖 테이블은 escape 오류로 거절한다.

최종 transport는 다음 형태다.

```json
{
  "table": "db:<table>",
  "access": "READ|WRITE|READ_WRITE",
  "op": "READ|INSERT|UPDATE|DELETE|MERGE|TRUNCATE|UNKNOWN",
  "op_source": "SCANNER|LLM_INFERRED|UNRESOLVED"
}
```

### 5.6 Neo4j 저장 구조 — 구현됨

근거: `graph\schema\graph_nodes.py:267-299`,
`graph\schema\graph_edges.py:198-237`,
`example_mapping_builder.py:31-180`,
`graph\nodes\code_node.py:60-78`.

```text
(FUNCTION)-[:HAS_RULE {
  occurrence_id: "<function id>::R0001",
  ordinal: 1,
  anchor_line: 123
}]->(RULE {
  id: "<normalized statement md5[:12]>",
  statement: "..."
})

(RULE)-[:HAS_EXAMPLE {
  occurrence_id: "<function id>::R0001",
  ordinal: 1
}]->(EXAMPLE {
  id: "<occurrence>::<rule hash>::1",
  given: "...",
  when: "...",
  then: "..."
})

(EXAMPLE)-[:AFFECTS_TABLE {
  access: "READ|WRITE|READ_WRITE",
  op: "...|UNKNOWN",
  op_source: "SCANNER|LLM_INFERRED|UNRESOLVED"
}]->(TABLE)
```

FUNCTION에는 `summary`, `search_text`, `logical_name`, `stereotype`,
`domain_terms_json`, `rules_quality_flag`, `analysis_partial`이 저장된다.
`table_insights`는 FUNCTION에 저장하지 않고 TABLE/COLUMN 종합 근거로 소비한다.
component는 합성 노드가 없으며 멤버 FUNCTION의 `component_id`, `component_name` 속성이
단일 진실이다.

### 5.7 review packet과 판정 구조 — 실제 실행 검증됨

`all-field\manifest.json`이 다음 stage records와 표본을 해시로 고정한다.

- `fixed20-functions.jsonl`: 20 rows
- `canonical66-schema.jsonl`: FILE 23 + TABLE 7 + COLUMN 36
- stage records: module pre-analysis 23, code summary 20, table insight 8,
  rules 22 records/20 functions, module summary 23, table description 7,
  glossary synthesis 6, component naming 20
- `context-presence-audit.json`: 의미 판정이 아닌 입력 존재·줄 무결성 검사

### 5.8 확정된 다음 구조 개선 요구 — **미구현**

다음 작업은 실제 남은 이슈를 고치기 위한 구현 작업이다. 큰 함수를 줄/토큰으로 임의
분할하지 않는다. 동일한 전체 SOURCE를 각 의미 호출에 유지하면서 기존 AST, branch,
def-use 자료에서 다음 결정론 evidence inventory를 생성해 입력 구조를 역할별로 분할한다.

```text
동일한 전체 함수 SOURCE
├─ 공통 직접 증거: R/W, runtime contract, referenced symbols
├─ decision_groups / condition_tree
├─ symbol_flow
├─ effect_inventory
├─ exit_inventory / fallback_inventory
├─ Summary 호출에 필요한 구조 블록
├─ Rule/Example 호출에 필요한 구조 블록
└─ Table Insight 호출에 필요한 구조 블록
```

여기서 “분할” 대상은 정보의 책임과 구조다. SOURCE의 앞·뒤 구간을 별도 호출로 잘라 서로
독립 분석하지 않는다.

#### decision group

```json
{
  "decision_group_id": "DG_BANK",
  "selector": "gc_Bank_Cd",
  "cases": [
    {
      "condition_id": "C1",
      "operator": "EQ",
      "values": ["02"],
      "direct_calls": ["Bank_002_CSB_Check"],
      "lines": [120, 123]
    },
    {
      "condition_id": "C2",
      "operator": "IN",
      "values": ["10", "11"],
      "direct_calls": ["Bank_011_CSB_Check"],
      "lines": [124, 129]
    }
  ],
  "default": {
    "condition_id": "C3",
    "direct_calls": ["Bank_Other_CSB_Check"],
    "lines": [130, 133]
  }
}
```

#### Boolean condition

```json
{
  "condition_id": "C17",
  "operator": "AND",
  "items": [
    {"left": "co", "op": "EQ", "right": "T"},
    {"left": "pay", "op": "EQ", "right": "01"},
    {"left": "edi", "op": "EQ", "right": "E"},
    {"left": "skvan", "op": "NE", "right": "Y"}
  ]
}
```

#### symbol use

```json
{
  "use_id": "U31",
  "expression": "strncmp(result_cd, \"A00\", LEN_CHECK)",
  "resolved_symbols": {"LEN_CHECK": "3"},
  "resolved_meaning": {"comparison_range": "first_3_characters"}
}
```

`resolved_meaning`은 API/언어/runtime contract로 결정론 증명할 수 있을 때만 만든다.
증명할 수 없으면 빈 값으로 두고 LLM이 이름만 보고 의미를 만들게 하지 않는다.

#### exit / fallback

```json
{
  "exit_id": "X4",
  "guard": "rc == RC_NRM",
  "effects_before_exit": [],
  "return_value": "RC_NRM",
  "line": 417
}
```

```json
{
  "fallback_id": "F2",
  "parent_decision": "DG_BANK",
  "direct_call": "Bank_Other_CSB_Check",
  "return_value": "RC_ERR"
}
```

#### 제안 Rule 출력

```json
{
  "rules": [
    {
      "statement": "금융기관 코드에 따라 전문 검사 경로를 선택한다.",
      "evidence_refs": ["DG_BANK"],
      "mappings": [
        {"when_values": ["02"], "then_call": "Bank_002_CSB_Check"},
        {"when_values": ["10", "11"], "then_call": "Bank_011_CSB_Check"}
      ]
    }
  ]
}
```

결정론 validator가 수행할 일:

- 모든 `evidence_refs`가 실제 inventory ID인지 검사
- condition item, 값 집합, complement/default, effect, exit가 정확히 보존됐는지 검사
- 하나의 effect가 중복 또는 누락되지 않았는지 검사
- 알려지지 않은 ID·값·효과를 거절
- stable group/anchor 순서로만 병합하고 LLM 재작성·판정을 하지 않음
- 전체 evidence obligation 집합에서 참조된 집합을 뺀 `missing_evidence_refs`를 계산
- 존재하지 않는 참조를 `unknown_evidence_refs`로 계산
- 같은 effect/exit를 여러 Rule이 소유하면 `duplicate_evidence_refs`로 실패
- 필수 evidence가 하나라도 빠지면 성공 결과로 저장하지 않고 명시적 validation failure 또는
  `analysis_partial`로 남김

#### 고정 상한 제거와 유한성 대체 — 확정 요구

현재 구현의 `Rule max 48`과 `Rule별 Example max 8`을 제거한다. 다음 변경은 단순한 무상한
출력이 아니다.

```json
{
  "evidence_inventory": {
    "decision_groups": ["DG_1", "DG_2"],
    "conditions": ["C_1", "C_2", "C_3"],
    "effects": ["E_1", "E_2"],
    "exits": ["X_1"],
    "fallbacks": ["F_1"]
  },
  "output_coverage": {
    "referenced": ["DG_1", "DG_2", "C_1", "C_2", "C_3", "E_1", "E_2", "X_1", "F_1"],
    "missing_evidence_refs": [],
    "unknown_evidence_refs": [],
    "duplicate_evidence_refs": []
  }
}
```

- Rule 개수는 evidence 항목 수와 같을 필요가 없다. 한 complete mapping Rule이 여러 evidence를
  소유할 수 있다.
- 정확도 판정은 Rule 개수가 아니라 전체 evidence obligation의 누락·추가·중복으로 한다.
- Rule/Example 배열의 유한성은 입력 evidence 집합과 참조 검증이 보장한다.
- 모델이 evidence와 무관한 Rule/Example을 반복하면 unknown/duplicate 검사와 stream guard가
  차단한다.
- `domain_terms max 10`은 검색 가치와 기존 폭주 이력을 가진 별도 계약이므로 이번 Rule/Example
  상한 제거와 함께 없애지 않는다.
- `Question max 15`는 이번 직접 실패 원인이 아니다. 고정 수치의 필요성을 SDD에서 검토하되
  Rule/Example 상한 제거와 자동으로 묶어 제거했다고 가정하지 않는다.

LLM은 구조를 다시 계산하지 않고 자연어 의미를 설명한다. 이 구조는 dispatch 분기를
은행별 Rule로 과분할하는 문제를 줄이면서 정확 값 매핑은 별도 배열에 보존한다.

#### 제안 stereotype 근거

```json
{
  "direct_reads": ["db:<table>"],
  "direct_writes": [],
  "external_state_mutation": false,
  "maps_caller_input_to_callee_input": false,
  "returns_lookup_result": true,
  "delegates_to_callee": false,
  "validation_decisions": false
}
```

```json
{
  "stereotype": "Query",
  "basis": [
    "direct_reads",
    "returns_lookup_result",
    "external_state_mutation=false"
  ]
}
```

단, ontology가 역할 우선순위를 정의하기 전에는 validator가 semantic taxonomy를 확정할 수
없다. 현재는 구현 제안일 뿐이다.

#### 제안 glossary 자격

AST에서 함수/테이블/컬럼/변수/상수/매크로/타입/call target/SQL key의 known identifier
집합을 만든다. 후보가 **구조 식별자로만** 등장하고 활성 자연어 주석·문자열·도메인 note에
사람용 표기로 등장하지 않으면 거절한다. 용어와 식별자가 같을 수 있으므로 “식별자와 같다”는
이유만으로 일괄 제거하지 않는다.

#### 구조 실패 완화 제안

Rule prose에 모든 식별자를 괄호·백틱으로 길게 넣기보다 출력 필드를
`condition`, `effect`, `identifiers`, `anchor_line`으로 단순 분리하는 후보가 있다.
이는 미닫힌 delimiter 위험을 줄일 수 있으나 아직 구현·측정되지 않았다.

문맥 폐쇄가 증명된 slice는 최후 수단이다. 필요한 경우 각 slice에 전체 함수 skeleton,
상속 ancestor condition, live-in 정의/값, live-out consumer, exit, call, stable source
coordinate가 모두 있어야 한다. 하나라도 계산할 수 없으면 함수를 쪼개지 않는다.

## 6. 확인된 사실

확인 시각은 별도 표기가 없으면 2026-07-30 19:26 KST다.

| 사실 | 근거 |
|---|---|
| 최종 run ID는 `20260730_094632_f8837581c3a4` | `...\final-rerun6\manifest.json` |
| strategy `framework`, locale `ko`, Neo4j DB `test` | 같은 manifest |
| prompt tree hash `366122f5...ef0fe` | 같은 manifest |
| 819.7초, 977 calls, input 7,162,691 / output 600,192 tokens | `usage_summary.json`, console tail |
| pipeline은 정상 종료했다 | console err 마지막 `🎉 종료`, 19:00:12 |
| 고정 20 prompt 20/20, claim 실행 근거 219/219, 실행 줄 4,139/4,139 | `context-presence-audit.json` |
| full evidence 216/219이며 빠진 세 조각은 실행문이 아닌 주석 | 같은 audit와 `verification.md` |
| 고정 함수 20, canonical schema 66 = FILE23/TABLE7/COLUMN36 | `all-field\manifest.json` |
| Rule 직접 엄격 충족 171/219 (78.1%) | `verification.md` 실행 D |
| stereotype exact 17/20 | `verification.md` 실행 D |
| logical_name 19/20 존재 | `verification.md` 실행 D |
| repetition guard 중단 13회: period-1 12, period-504 1 | console error log와 `verification.md` |
| 서버 관측값은 `grammar_backend=xgrammar`, `constrained_json_disable_any_whitespace=false` | `verification.md`; GPU 서버 직접 접근은 불가 |
| 현재 analyzer Python 프로세스 없음 | 19:26 KST `Win32_Process` 조회 결과 0건 |
| git status는 129개 dirty entry | 19:26 KST `git status --porcelain=v1` |

### 6.1 최종 고정 20 Rule 직접 판정

| 함수 | 충족/active | 누락·오류 |
|---|---:|---|
| `zordmfax:a000_input_validation` | 8/15 | c4,c7,c8,c9,c10,c11,c16 |
| `zapam10090:b000_main_proc` | 11/13 | c1,c7 |
| `zapam10060:b000_main_proc` | 14/18 | c9,c11,c13,c17 |
| `zapam10020:b000_main_proc` | 6/8 | c3,c8 |
| `zapam10080:c210_insert_zpay_spc` | 6/8 | c6,c7 |
| `zapam10100:b200_SMS_transfer` | 15/16 | c16 |
| `zapam10200:d400_mms_transfer` | 13/16 | c2,c7,c8 |
| `zordmutl:ordf_chk_adlt_vald` | 11/15 | c6,c10,c13,c14 |
| `zapam10011:b100_bill_isue_typ_chk` | 4/6 | c3,c5 |
| `apascrtu:c000_main_proc` | 12/14 | c2,c6 |
| `zapam10140:c300_call_zapamcom10050_proc` | 13/15 | c2,c3 |
| `apascrtu:c010_rltm_auth_proc` | 9/12 | c1,c4,c5 |
| `zapam10140:d100_call_zapamcom10060_proc` | 4/5 | c3 |
| `zapam20010:b000_main_proc` | 11/12 | c6 |
| `zngmmcap:a000_input_validation` | 8/8 | 없음 |
| `zapam10070:c000_main_proc` | 8/8 | 없음 |
| `zngmmcap:get_card_ser_num` | 4/5 | c4 |
| `zapam10140:d510_*` | 11/11 | 없음 |
| `zngmmcap:get_ctz_num` | 3/3 | 없음 |
| `zordmfax:b450_mq_trns` | 0/11 | c1~c11 |

### 6.2 최종 주요 성공

- `c210_insert_zpay_spc` table insight에 exact DDL `com_cd_val`이 복구됐고 다른 I/O 구조체
  컬럼은 섞이지 않았다.
- `skvrq_yn`, `drw_bank_ctz_num_pinf`, `drw_bank_ctz_ser_num`의 source 의미 오류가
  해소됐다.
- `drw_bank_ctz_num_pinf`는 `출금은행 주민 부분정보 (암호화)`를 보존했다.
- component partition 입력에서 `swgf_strncmp` 같은 Util bridge가 제거됐고 FAX component가
  FAX 처리·이력 함수군으로만 구성됐다.
- MMS 함수는 Rule 16개로 뒤쪽 T/S/P/default, 수신거부, 전송 실패 정상 반환까지 보존해
  과거 32 cap 절단을 해소했다.

### 6.3 최종 주요 실패

- `zapam10020:b000_main_proc`
  - summary concern이 period-1 공백 폭주로 중단됨.
  - summary/search_text/logical_name/stereotype이 비었음.
  - Rule은 은행별 분기를 과분할해 48개 cap에 닿고 초기 결과/default 오류가 잘림.
- `zordmfax:b450_mq_trns`
  - 두 rules 응답 모두 미닫힌 구분자를 포함.
  - 구조 검증이 전체 concern을 거절.
  - partial JSON 미채택, 최종 Rule/Example 0.
- `d400_mms_transfer`
  - 컬럼 소유권은 정확.
  - 발송 지연 파라미터 조회를 “조회된 메시지를 본문에 복사”로 잘못 설명.
  - 입력은 완전했으므로 최초 실패는 model response.
- stereotype
  - `zapam10020`은 summary 실패로 미기록.
  - `d100_*` gold Adapter, actual Helper.
  - `get_card_ser_num` gold Query, actual Helper.
- glossary synthesis 후보에 `mpfmdbio`, `zpay_ap_auth_req_hst`, `RC_NRM` 같은 기술
  식별자가 남은 사례가 있다. fixed20 함수 domain terms 자체는 모두 0개였다.

## 7. 합리적 추론

| 추론 | 근거 | 반증 조건 |
|---|---|---|
| whitespace flood 발생은 확률적 모델/server decode 현상이며 용어 전용 문제가 아니다 | 같은 run에서 summary와 rules 모두 period-1 공백 반복; 일반 반복 period-504도 1건 | 동일 raw stream이 client에서 중복 조립됐다는 로그, 또는 서버가 공백을 보내지 않았다는 token trace |
| 현재 client guard는 원인을 제거하지 않고 피해를 제한한다 | 32자/JSON root 후 8자에서 stream abort; 서버 옵션은 false | 서버 옵션을 켠 대조 실험에서 flood가 그대로이고 client 로직이 원인임이 증명됨 |
| `zapam10020`의 48 Rule은 실제 계약 수라기보다 같은 selector dispatch의 표현 과분할이다 | 은행별 sibling branch가 각각 Rule, 뒤쪽 default 잘림 | source-grounded 직접 판정에서 48개가 모두 독립 종료/효과 계약임이 증명됨 |
| dispatch group + exact mappings 구조는 cap 의존 없이 누락을 줄일 가능성이 높다 | 같은 판단축을 prose Rule 여러 개로 만들면서 값 매핑과 default가 분산됨 | 구조 도입 후 gold 경계·fallback이 더 많이 누락되거나 validator가 의미를 추론해야만 동작함 |
| b450 구조 실패는 긴 prose 안의 delimiter-heavy 식별자 표현과 관련될 가능성이 있다 | 최초 모델 문자열이 `mpfmdbio(`에서 닫히지 않음, parser 후 손상이 아님 | 간단 출력 구조에서도 같은 응답이 동일 비율로 발생하거나 raw stream 자체가 전송 중 절단됨 |
| stereotype은 prompt 문구만 계속 늘리는 것보다 결정론 basis를 제공하는 편이 안정적일 수 있다 | 완전 입력에도 Query/Adapter가 Helper로 흔들림 | ontology precedence가 없어서 basis가 같은 여러 stereotype을 계속 허용함 |

이 추론은 구현 완료 사실이 아니다. 다음 작업자는 반증 조건을 먼저 검사하고 설계를 확정한다.

## 8. 확인 불가·미검증

1. GPU 서버 자체 로그와 시작 인자에 접근할 수 없다.
   - `--constrained-json-disable-any-whitespace`가 실제 서버 프로세스에 적용됐는지 직접 확인하지 못했다.
   - `false` 값은 기존 관측/verification 근거다.
   - 이 옵션은 “JSON의 모든 공백을 제거해 반환”하는 단순 pretty-print 옵션이 아니라 grammar가
     임의 whitespace 토큰을 허용하지 않게 하는 서버 decode 설정으로 이해하고 있으나, 현재
     저장소만으로 서버 빌드별 정확 동작을 재검증할 수 없다.
2. 최종 run 뒤 `test` DB가 다른 작업으로 바뀌지 않았는지 19:26 KST에 live query하지 않았다.
   final review packet과 manifest는 immutable 증거지만 live DB는 mutable하다.
3. 최종 shell invocation 전문은 별도 command transcript로 고정되지 않았다.
   console preflight로 profile, data dir, output override는 복원 가능하다.
4. final glossary stage 후보가 최종 GLOSSARY node에 모두 write됐는지는 독립 live read-back으로
   재확인하지 않았다. 후보 stage와 graph final을 구분해야 한다.
5. 확정된 다음 요구인 `decision_groups`, `effect_inventory`, `exit_inventory`, `symbol_flow`,
   structured stereotype basis는 구현·테스트되지 않았다.
6. Rule 48/Example 8 고정 상한 제거와 evidence 기반 유한성은 요구사항으로 확정됐지만
   아직 코드·Pydantic schema·prompt·validator에 구현되지 않았다.
7. function slicing은 구현·검증되지 않았고 기본 방향도 아니다.

## 9. 결정 원장

| 결정 | 이유 | 폐기한 대안 | 되돌릴 조건 |
|---|---|---|---|
| 27일 gold와 직접 비교를 canonical 판정으로 사용 | source-grounded 원자 의무와 실제 output을 직접 확인 가능 | 28일 별도 LLM judge, 문자열 유사도 | 사용자가 새 정답 계약을 명시적으로 승인 |
| 고정 20/219와 canonical 66을 함께 검증 | 219는 Rule/Example 분모일 뿐 전체 필드가 아님 | Rule 수치만 전체 정확도로 보고 | field ownership 자체가 변경 |
| 의미 검색은 `search_text`만 사용 | 검색 전용 상세 필드의 단일 책임 | summary/rule/example를 embedding에 합침 | 검색 계약 spec이 명시적으로 개정되고 회귀 검증 |
| summary는 1~3문장, search_text는 더 구체적 | 표시용 설명과 의미 검색 목적이 다름 | 둘을 완전히 동일하거나 억지로 다르게 만듦 | 소비자 계약 변경 |
| domain_terms는 현재 summary concern에 유지 | 현재 3분할 구조와 response schema의 사실 | 독립 LLM 호출 추가 | 폭주·정확도 실측을 갖춘 별도 spec 승인 |
| 테이블별 컬럼 중첩 구조 유지 | 소유권이 명확하고 깊이가 1단계 | 모든 컬럼 평탄화 | 소비·성능 실측에서 명확한 이득 |
| Rule은 complete mapping이면 한 문장 가능 | “있으면 갱신/없으면 생성” 같은 한 판단축은 한 계약 | 무조건 한 조건당 한 Rule 또는 무조건 합침 | statement에서 매핑 복원 불가하면 분리 |
| Example max 2로 낮추지 않음 | 필수 경계·fallback 누락 위험, 실측 3개 이상 존재 | Rule당 hard max 2 | 실제 분포와 gold에서 2개 초과가 불필요함을 증명 |
| 현재 Rule max 48, Example max 8 고정 상한을 제거 | 48에 실제 도달해 필수 fallback이 잘렸고 고정 개수는 source 계약 수를 표현하지 못함 | 48을 64/100으로 상향, 단순 무상한 배열 | 새 evidence 구조가 완전성을 보장하지 못한다는 반증이 나오면 수치 상한 복귀가 아니라 유한성 계약을 재설계 |
| 고정 상한 대신 evidence 기반 유한성을 사용 | SOURCE에서 추출한 유한 obligation 집합의 missing/unknown/duplicate를 결정론 검사 가능 | LLM이 Rule 개수를 자율 결정하고 cap만 적용 | parser가 필요한 evidence를 결정론적으로 추출하지 못하면 미검증으로 남기고 추출 계약부터 보완 |
| domain_terms max 10은 당장 유지 | Rule/Example과 다른 검색 사전 후보 계약이며 폭주 이력이 있음 | 모든 상한 일괄 제거 | 용어 자격·유한성이 별도 구조로 증명되고 정확도/폭주 대조 검증 |
| whitespace 반복은 즉시 중단, 재시도하지 않음 | GPU 피해 제한, 같은 failure trajectory 반복 방지 | 무조건 재실행, partial JSON 채택 | 서버 측 원인 제거와 대조 검증 후 retry contract 별도 설계 |
| full function source 유지 + 구조 evidence 우선 | 임의 분할은 ancestor/def-use/exit 문맥을 끊음 | line/token chunking | slice context closure가 결정론적으로 증명 |
| 결정론 로직은 사실 추출·검증, LLM은 의미 해석 | constitution I과 black-box 금지 | corpus-specific semantic overwrite, 후처리 정답 주입 | ontology가 폐쇄형 결정론 계약으로 승격 |
| 최종 run D가 마지막 사이클 | 사용자 명시 지시, 재실행 비용·파괴성 | 남은 오류를 맞힐 때까지 반복 | 사용자 새 승인 |

## 10. 사용자 지적·정정 원문과 바뀐 판단

아래는 의미를 보존한 사용자 원문이다. 오타는 원문 그대로 두고 해석을 덧붙였다.

- “27일 문서도 중요하지않나?”
  - 변경: 27일 handoff/gold를 canonical 입력에 포함하고 28일 judge 방식을 폐기했다.
- “그 28일 짜리 그 검증하는 것은 폐기했고요 그러니까 너가 골드를 보고 ... 직접 비교”
  - 변경: 의미 판정 자동화를 금지하고 사람이 gold/source/output을 직접 대조한다.
- “로직문제인가 프롬포트문제인가 아닌가요?”
  - 변경: 모든 실패를 prompt input/contract/model/parser/merge/write 최초 단계로 분류한다.
- “LLM으로 한번더 처리한다던지 ... 금지원칙”
  - 변경: 별도 LLM judge/repair, happy-path 재시도, partial 복구를 금지했다.
- “그리고 컨텍스트 요약이라는것도 존재하는거알지”
  - 변경: callee `search_text`, module intent, compact body/ancestor context의 손실 여부도 감사한다.
- “의미가 비슷하다고 해서 ... 이런건 다른거야”
  - 변경: 조건 집합·미정/보류·오류 반환 경계가 다르면 비동치로 판정한다.
- “모듈 SUMMARY나 기타 등등 TABLE DESCTION 등등도 ... 다 보고 압축”
  - 변경: 함수 Rule 외 module/table/column/glossary/component까지 field ownership matrix에 포함했다.
- “필드값이랑 ... 실제 쓰이는 함수나 테이블은 다 전달 ... 자연으로 해석”
  - 변경: 직접 구조 사실은 그대로 전달하고 자연어 해석은 summary/search/table 설명에 생성한다.
- “일단 의미검색은 search_text만 하는걸로 합시다”
  - 변경: embedding/semantic retrieval material을 `search_text` 하나로 고정했다.
- “role: WRITE-then-READ, 이건 왜 뽑음?”
  - 변경: 현재 table role은 폐쇄 enum이며 최종 op는 scanner/RW provenance와 결합한다.
    `WRITE-then-READ` 같은 임의 role은 현재 구조에 없다.
- “도메인 용어 ... 상태 코드 ... 그들만의 용어 ... 이상한 거 다 집으면 안”
  - 변경: 일반어·구조 식별자·뜻 없는 상태코드를 제외하고 evidence가 뜻까지 입증해야 한다.
- “op를 llm없이 알수있음?” / “fw는 ... 직접 판단”
  - 변경: scanner exact op가 있으면 결정론 확정, 없으면 LLM 후보를 provenance와 함께 제한적으로
    사용하거나 `UNKNOWN`으로 남긴다.
- “모든속성별로 다 체크하는거맞음?”
  - 변경: fixed20 전체 함수 필드와 canonical schema, 신규 산출물의 gold gap을 모두 포함했다.
- “로직적인 문제와, 프롬포트 문제 2개지?”
  - 변경: 두 축을 유지하되 model response/parser/write까지 최초 실패 단계를 더 세분화했다.
- “실제 변수나 값 상수 ... 실제값이 참조되어서 ... 뱉어졌나”
  - 변경: source definition/assignment → use → result 연결을 별도 핵심 축으로 고정했다.
- “context들이 문제없이 다 들어왔는가 ... 그리고 ... 지침과 함께 결과가 잘 만들어졌나”
  - 변경: context presence와 interpretation quality를 독립 판정한다.
- “구간을 함부로 나누면 문맥이 끊어질텐데요”
  - 변경: 임의 slicing 제안을 철회하고 full SOURCE + deterministic structure evidence를 기본으로 고정했다.
- “전혀 이해가안됨.. 그러니가 함수를 쪼개ㅓ 분석하자는거임?”
  - 변경: 함수 분할이 기본 해법이 아님을 명시하고, closure 증명 없는 분할을 금지했다.
- “반복되는 구조만 코드가 정리해서 함께 제공합니다... 너무 추장적인데 뭐 어떻게 하겠다는거임”
  - 변경: `decision_group`, boolean items, symbol use, exit, fallback의 구체 JSON 구조로 설계를 명시했다.
- “입력을 쪼개는거아니였음?”
  - 변경: 함수 SOURCE를 줄/토큰으로 자르는 것이 아니라, 동일한 전체 SOURCE를 유지하면서
    decision/condition/symbol/effect/exit/fallback 정보를 독립 입력 블록으로 구조 분할하는
    요구임을 명시했다.
- “상한제거처리는 할건가요?”
  - 변경: 현재 Rule 48/Example 8 고정 상한을 제거하고, 64/100 상향이나 단순 무상한이 아닌
    source evidence obligation의 missing/unknown/duplicate 검증으로 유한성을 대체하도록 확정했다.
- “해야할거 딱딱 제대로 문서에담겨씀?”
  - 변경: 후속 작업을 상황 파악으로 끝내지 않고 구조 입력 분할, 고정 상한 제거,
    evidence 참조 출력, validator, stereotype/glossary 구조 근거까지 실제 SDD·구현 과제로 고정했다.
- “이번사이클을 마지막으로 하도록”
  - 변경: final rerun6 이후 추가 실제 재분석을 중지했다.

## 11. 현재 branch·HEAD·dirty WIP·프로세스·데이터 상태

### Git

- repository: `D:\work\robo\project\robo-data-analyzer`
- branch: `main`
- HEAD: `a3779491be6fc962834a95503332a39f73afa4dd`
- dirty entries: 129
- `specs\110-fixed-20-semantic-accuracy\` 전체는 현재 untracked다.
- 이 handoff 작성 턴이 새로 만든 파일은 `specs\110-fixed-20-semantic-accuracy\HANDOFF.md`다.
- 나머지 massive dirty worktree는 사용자와 다른 작업자의 WIP가 섞여 있다. reset, checkout,
  clean, broad format을 실행하지 않는다.

관련 WIP에는 다음이 포함된다.

- prompt contracts/YAML/hash
- repetition guard
- analysis context와 branch inventory
- rule/table/domain post-processing
- component/table description post-processing
- 관련 테스트
- spec 110 문서 전체

정확한 현행 목록은 다음 read-only 명령으로 다시 확인한다.

```powershell
# cwd: D:\work\robo\project\robo-data-analyzer
git status --short
git diff --stat
git diff -- specs/110-fixed-20-semantic-accuracy
```

### 프로세스

2026-07-30 19:26 KST에 analyzer 관련 Python process는 0개였다. 다음 명령으로 확인했다.

```powershell
Get-CimInstance Win32_Process |
  Where-Object {
    $_.Name -match '^python' -and
    ($_.CommandLine -like '*analyze_data_dir.py*' -or
     $_.CommandLine -like '*robo-data-analyzer*')
  } |
  Select-Object ProcessId, Name, CommandLine
```

### 데이터

- final immutable run output:
  `D:\work\robo\_runs\analyzer-semantic20-20260730-final-rerun6`
- final review:
  `D:\work\robo\_runs\analyzer-semantic20-20260730-final-rerun6-review`
- run 당시 Neo4j DB: `test`
- Analyzer 전체 실행은 시작 시 graph를 `DETACH DELETE`하고 logs를 비우는 파괴적 full rebuild다.
- live `test` DB는 공유 mutable state이므로 final review packet보다 낮은 권위다.
- 추가 전체 실행은 사용자 새 승인 전 금지한다.

## 12. 핵심 파일 지도와 현재 SHA256

아래 핵심 파일은 모두 절대경로로 기록한다.

| 절대경로/역할 | 주요 줄 | SHA256 |
|---|---:|---|
| `D:\work\robo\project\robo-data-analyzer\.specify\memory\constitution.md` — Analyzer 헌법 | 전체 | `81416f628082bf10c4c8f467dff8934d2c681f546a6c8bf28940e35294897fe8` |
| `D:\work\robo\project\robo-data-analyzer\specs\110-fixed-20-semantic-accuracy\spec.md` — 범위·판정 계약 | 전체 | `a720aea95a406a6abd815f4c4c765862c5295bc3e3d3de72d9e82f804428da22` |
| `D:\work\robo\project\robo-data-analyzer\specs\110-fixed-20-semantic-accuracy\verification.md` — 실행 A~D 직접 판정 | 전체 | `1849d7d85b0612701fb3b47fbfa4eeddf30e40468e91e729d91797e8010976af` |
| `D:\work\robo\project\robo-data-analyzer\pipeline\models\analysis_context.py` — 심볼/외부 증거/context | 32-157 | `d72641acf9a68aef8cdd6e3c364da978069c8109e42bfa5ae4222389ee4a3b42` |
| `D:\work\robo\project\robo-data-analyzer\pipeline\models\code_analysis_output.py` — LLM Pydantic schema/cap | 55-424 | `efd3784d14baab0a42ed1c08febf8acf7d462bbf0a0115d0e21b2df2888cbc49` |
| `D:\work\robo\project\robo-data-analyzer\pipeline\quality_gates\branch_inventory.py` — branch/effect/index | 23-339 | `bd2408e17cfd38cebfd52ea81bcbf5bb83d32edbce481dfedd41e89d3a99baf3` |
| `D:\work\robo\project\robo-data-analyzer\pipeline\steps\step7_analyze_code\three_split_llm_fanout.py` — 3분할 조립/merge | 138-580 | `ee3feaf7cde9e9b2f8eba14b139616e6b68928073f285fd373276205357528b7` |
| `D:\work\robo\project\robo-data-analyzer\pipeline\steps\step7_analyze_code\table_prompt_context.py` — table/column 소유권 | 11-135 | `5a99ce3ff9575613c4a78a173ac5ea8b0b25f163a73da52fdae2751f739b1bd2` |
| `D:\work\robo\project\robo-data-analyzer\pipeline\steps\step7_analyze_code\rw_table_filter.py` — final op provenance | 181-345 | `cbfb9db9331900bb8450450ab9d8d9e73dfe6a6ec76dc6bfcfb7fb83b279f16e` |
| `D:\work\robo\project\robo-data-analyzer\pipeline\steps\step7_analyze_code\rule_quality.py` — cap/quality 관측 | 14-103 | `360baa449ece63c410ad0d7c6011c5f99053b27b8f763e85dab08af05ea80045` |
| `D:\work\robo\project\robo-data-analyzer\pipeline\steps\step7_analyze_code\domain_terms.py` — 용어 normalize | 65 이후 | `106ab35434f22ebc7ea340f87e28964fd16818d3f159ad7ee93f22e4e20d1fc3` |
| `D:\work\robo\project\robo-data-analyzer\pipeline\steps\step8_post_processing\build_components_step.py` — component partition/name | 92-230 | `8e9dc304b784db4a042416e9920a7a1f29e7eb18d4820e4d4ddc6d6caca0e828` |
| `D:\work\robo\project\robo-data-analyzer\pipeline\steps\step8_post_processing\enrich_table_description_step.py` — TABLE/COLUMN 종합 | 190-286 | `b54946e1bc8646bd710ba5a8cb4c09df0a697036d52c60d595897fc5ffe56b77` |
| `D:\work\robo\project\robo-data-analyzer\llm\repetition_guard.py` — stream 폭주 중단 | 39-165 | `a383e7f723a538927610d458ad41df125a1da1278f2adb06d2960ec9dabe8d7c` |
| `D:\work\robo\project\robo-data-analyzer\llm\prompts\framework\_common.yaml` — 3분할 byte-identical 공통 | 전체 | `3b4bf96bf8f5c12e78b441dc9ab24d4ae348464613bd028b716530e4176e049b` |
| `D:\work\robo\project\robo-data-analyzer\llm\prompts\framework\code_summary.yaml` — summary/search/term/name/stereotype | 전체 | `0a46c6d537bab42ba91b78e61570e6da822970a49f70f085a897012dc66a5974` |
| `D:\work\robo\project\robo-data-analyzer\llm\prompts\framework\code_rules_examples.yaml` — Rule/Example/Question | 전체 | `a814ceead022a19abb73edf9a05e425f5ab590b7f14b8c8991e973628335a785` |
| `D:\work\robo\project\robo-data-analyzer\llm\prompts\common\rule_evidence_contract.md` — Rule 경계/값/상한 | 전체 | `151b0dc157d1cb30852fa12b35c8f99dabff4e75d90e17ee87f5fc8c59ef769c` |
| `D:\work\robo\project\robo-data-analyzer\llm\prompts\common\domain_terms_contract.md` — 용어 자격 | 전체 | `dcbab81205037469e2ce225a150f60842539f9d40f8ab6996b98521abcb8197a` |
| `D:\work\robo\project\robo-data-analyzer\llm\prompts\prompt_hashes.json` — prompt hash single truth | 전체 | `6229e1a95985f2fa860e780e8ea1b86036e674057961a34d445dad418c1619b7` |

Run evidence hashes:

| 경로 | SHA256 |
|---|---|
| `D:\work\robo\_runs\analyzer-semantic20-20260730-final-rerun6\manifest.json` | `2bc32220a1a834d62003c760602aa9762275904ac855bd1b18b0aeb9c7ed8f03` |
| `...\fixed20\context-presence-audit.json` | `c4d68a193dd88399fc85b7a6effe6dd0c69705b353ce32219480bd3dd9d7c1f1` |
| `...\all-field\manifest.json` | `2b227d104e937078f2a19cd3e375a6824604712b33432c3f4c0758160ebb566c` |
| `D:\work\robo\_runs\analyzer-semantic20-20260730-final-rerun6.console.log` | `8d3bd2097d23262997bd061353e5066285ba7ee0c714d7df7f19c246b67b96a2` |
| `D:\work\robo\_runs\analyzer-semantic20-20260730-final-rerun6.console.err.log` | `067d024a070ec74663d2faf004cc5081ef542b9fd1ee4153ca7cc0f75c2e26fd` |

## 13. 변경 파일과 변경 이유

### spec 110 사이클에서 확인된 관련 변경

- `analysis_context.py`, context builders
  - referenced symbol 선언값·초기화 provenance를 prompt에 전달.
- framework runtime note handoff
  - `PFM_TRY` 같은 wrapper 계약을 `codebase_notes`로 전달.
- `branch_inventory.py`
  - complement의 직접 decision과 외곽 상속 decision 좌표를 명시.
- `table_prompt_context.py`
  - 테이블명과 DB/ORM owner를 기준으로 직접 컬럼만 전달.
  - 구조체 `comm_cd_val`과 DDL `com_cd_val`처럼 인접 동일문자 하나가 줄어든 구조적 별칭만
    허용하고 출력은 exact DDL 이름 유지.
- `code_analysis_output.py`
  - 현재 구현에는 Rule max 48, Rule별 Example max 8이 남아 있다.
  - 이 48/8은 2026-07-31 확정 요구에 따라 제거해야 할 구현 대상이다.
  - 미닫힌 delimiter 검증.
  - ComponentNamingOutput을 실제 소비하는 `name` 한 필드로 축소.
- `rule_quality.py`
  - Rule 20부터 검토 신호, cap hit 기록. 결과를 수정하지 않음.
- prompt contracts
  - summary/search_text/Rule/domain term/table/stereotype 책임과 충돌 제거.
  - Rule token budget 10,000.
- `domain_terms.py`
  - 구조체 멤버와 known identifier 오염 억제.
- `rw_table_filter.py`
  - R/W/table op/DDL allowlist와 LLM 후보 결합.
- `enrich_table_description_step.py`
  - source semantic head가 모델 설명에서 사라지면 source 표현을 결정론 보존.
  - 새 의미를 생성하지 않음.
- `build_components_step.py`
  - Util/Utility 노드와 incident edge를 Leiden partition 입력에서 제외.
  - 원본 CALLS 관계는 유지.
- `repetition_guard.py`
  - 일반 exact repetition 3,072 chars/period≤512.
  - JSON 문자열 밖 연속 whitespace 32, root 종료 뒤 8에서 중단.
  - 부분 JSON 미복구.
- 관련 테스트 및 `prompt_hashes.json`
  - 위 계약을 회귀 고정.

### 이번 handoff 작성 턴의 변경

- 새 파일: `specs\110-fixed-20-semantic-accuracy\HANDOFF.md`
- 코드·prompt·test·Neo4j에는 이번 handoff 턴에서 변경하지 않았다.

## 14. 실행한 검증 명령·결과·증거 경로

### 실제 final pipeline

console preflight로 복원되는 실행 형태:

```powershell
# cwd: D:\work\robo\project\robo-data-analyzer
.\.venv\Scripts\python.exe scripts\analyze_data_dir.py `
  --profile D:\work\robo\_runs\analyzer-semantic20-20260730-context-closed-profile.yaml `
  --run-output D:\work\robo\_runs\analyzer-semantic20-20260730-final-rerun6
```

- 목적: 23 source + DDL을 framework 전략으로 Neo4j `test`에 full rebuild.
- 예상 성공: preflight가 정확한 data/source/ddl/DB/output을 표시하고 마지막에 `🎉 종료`.
- 실제 결과: 성공, run ID `20260730_094632_f8837581c3a4`, 819.7초.
- 증거: final console logs, manifest, usage summary.
- **재실행 금지**: 이 명령은 재현 기록이지 다음 작업 지시가 아니다.

### 최종 context/all-field 추출

- 결과:
  - target prompt 20/20
  - execution claim 219/219
  - execution lines 4,139/4,139
  - fixed20 20, canonical schema 66
- 증거:
  - `...\final-rerun6-review\fixed20\context-presence-audit.json`
  - `...\final-rerun6-review\all-field\manifest.json`

### 이미 통과한 회귀

- 실행 C 이후 집중: 34 tests / 24 subtests 통과.
- 관련 범위: 210 tests / 156 subtests 통과.
- 최종 문서 직전 focused set: 15 tests 통과.
- prompt byte identity: 32 prompts 통과.
- 근거: `verification.md`.

핸드오프 자체 완료 전 다시 실행할 read-only/focused 명령:

```powershell
# cwd: D:\work\robo\project\robo-data-analyzer
.\.venv\Scripts\python.exe -m unittest `
  tests.pipeline.step7_analyze_code.test_table_prompt_context `
  tests.pipeline.step7_analyze_code.test_rule_quality `
  tests.pipeline.step8_post_processing.test_component_utility_bridge `
  tests.pipeline.step8_post_processing.test_table_semantic_grounding

.\.venv\Scripts\python.exe scripts\check_byte_identical.py

git diff --no-index --check -- NUL `
  specs/110-fixed-20-semantic-accuracy/HANDOFF.md
```

마지막 명령은 untracked 문서를 빈 파일과 비교하므로 내용 차이 때문에 exit code 1이 정상이다.
exit code 2 이상 또는 `trailing whitespace` 출력이 실패다. 일반 `git diff --check -- <path>`는
untracked 파일을 검사하지 않으므로 이 문서 검증에 사용하면 안 된다.

예상 성공은 각각 `OK`, `32 prompts byte-identical`, whitespace error 없음이다.

2026-07-30 19:31 KST 실제 결과:

- focused unit tests: `Ran 15 tests in 0.044s`, `OK`
- prompt byte identity: `OK - 32 prompts byte-identical`
- 최초 `git diff --check -- <path>`는 untracked 파일을 검사하지 않아 유효한 검증이 아니었다.
- `git diff --no-index --check -- NUL <path>`로 다시 검사했다. 최초 검사에서 문서 첫 네 줄의
  Markdown hard-break trailing spaces를 발견해 제거했다. 재검사 결과 whitespace error 없음,
  내용 차이만 뜻하는 exit code 1.
- 첫 검증 직전 문서 크기: 1,184 lines / 57,991 bytes. 이후 이 검증 결과와
  `rw_table_filter.py` hash를 추가했으므로 현재 크기는 명령으로 다시 계산한다.
- 문서 자체 SHA256을 본문에 고정하면 hash 문자열을 쓰는 순간 다시 바뀌므로 self-hash는
  본문 밖 최종 명령 결과로만 관리한다. 다음 작업자는 `Get-FileHash`로 현재값을 계산한다.

## 15. 실패했던 시도와 다시 하지 말아야 할 것

1. **28일 LLM judge**: 의미 판정 정본으로 사용하지 않는다.
2. **Rule cap 32**: MMS 뒤쪽 T/S/P/default와 실패 경로를 잘랐다. 되돌리지 않는다.
3. **Rule/Example 수를 먼저 목표로 정함**: 실제 계약을 자르거나 과분할한다.
4. **Example hard max 2**: 필수 경계·fallback 누락 위험 때문에 채택하지 않았다.
5. **프롬프트만 계속 길게 보강**: 상호충돌과 우선순위 희석이 생긴다.
6. **용어가 폭주의 단일 범인이라는 가정**: final run에서 summary/rules 모두 폭주해 반증됐다.
7. **무작정 재실행**: 확률적 결과를 맞힐 때까지 돌리는 것은 원인 추적이 아니다.
8. **부분 JSON 채택**: b450처럼 잘린 결과를 정상으로 가장하지 않는다.
9. **별도 LLM repair/judge**: 최초 오류를 가리고 비용·비결정성을 늘린다.
10. **summary/search_text 상호 복사**: 빈 필드를 다른 생성 필드로 채우지 않는다.
11. **table 전체 DDL column을 허용**: 다른 I/O 구조체의 동명 필드가 섞였다.
12. **물리명으로 column 의미 추정**: 사람/계정/기관/암호화 경계를 바꿨다.
13. **Util을 무방향 component bridge로 사용**: 무관 도메인을 합쳤다.
14. **임의 source chunking**: ancestor guard, def-use, exit/fallback 문맥이 끊긴다.
15. **특정 은행/함수/코드값 예외**: 범용 개선이 아니며 금지한다.

## 16. 안전 경계

- 비밀번호, 토큰, 고객 원문, 개인정보를 문서에 복사하지 않는다.
- `.env` 내용을 출력하거나 기록하지 않는다.
- run artifact의 source/prompt에는 고객 코드가 있으므로 경로·hash·집계만 문서화한다.
- `git reset --hard`, `git checkout --`, `git clean`, broad delete/format 금지.
- 저장소 dirty WIP 129건을 사용자 소유로 간주한다.
- Analyzer 실제 실행은 Neo4j `test` graph와 run logs를 초기화하는 파괴적 full rebuild다.
- 전체 재실행, DB 삭제/초기화, 기존 run output overwrite는 사용자 새 승인 없이는 금지한다.
- GPU 서버 설정 변경은 권한 밖이다.
- focused unit test, hash, diff, immutable artifact read는 안전한 read-only/로컬 검증이다.

## 17. 다음 작업의 정확한 순서와 완료 증거

추가 실제 재분석 없이 진행할 수 있는 다음 설계 작업이다.

### 1단계 — 현행 상태 재고정

1. §1 원문을 UTF-8로 모두 읽는다.
2. branch/HEAD/status와 핵심 file hash를 다시 확인한다.
3. final manifest/review hash가 §12와 같은지 확인한다.

완료 증거:

- 새 시각의 branch/HEAD/status 기록
- hash mismatch 없음, 또는 mismatch가 있으면 이 문서에 diff 원인 추가

### 2단계 — 남은 실패를 구조 요구사항으로 변환

1. `zapam10020` raw SOURCE/branch index/rules response를 읽는다.
2. 같은 selector를 공유하는 sibling decision을 결정론적으로 group할 수 있는지 확인한다.
3. default/fallback/initial result/exit를 inventory로 완전 추출할 수 있는지 확인한다.
4. `b450` raw response에서 delimiter 실패의 정확한 field와 token 위치를 기록한다.
5. stereotype 2개 오답의 direct read/write/state mutation/input-output mapping facts를 추출한다.
6. 현재 `Rule max 48`과 `Rule별 Example max 8`의 모든 생산자·소비자·테스트·prompt hash를
   저장소 전체에서 찾는다.

완료 증거:

- 특정 함수 이름을 코드에 박지 않은 language-neutral inventory contract
- gold claim마다 inventory ID로 연결 가능한 coverage table
- 추출할 수 없는 사실은 `unknown`과 반증 명령으로 기록

### 3단계 — SDD로 구조 개선안 확정

새 spec 또는 spec 110 후속 amendment에 다음을 명시한다.

- full SOURCE 유지
- `decision_groups`, `condition_tree`, `effect_inventory`, `exit_inventory`,
  `symbol_flow` schema
- 전체 SOURCE를 유지하고 구조 블록만 관심사별로 분리하는 prompt input 계약
- stable evidence ID 규칙
- LLM output의 `evidence_refs`/`mappings`
- validator의 exact preservation 규칙
- Rule 48/Example 8 고정 상한 제거와 evidence 기반 유한성
- missing/unknown/duplicate evidence의 실패·partial·저장 계약
- `domain_terms max 10` 유지와 `Question max 15` 재검토 경계
- ontology basis의 결정론/LLM 책임 경계
- schema restraint와 토큰 예산
- migration/consumer 영향

완료 증거:

- constitution check
- data model, contract, tasks
- positive/negative/adversarial test cases

### 4단계 — 구현

1. 기존 `branch_inventory.py`/parser data에서 구조 fact만 추출한다.
2. 모든 의미 호출에 전체 SOURCE를 유지하고 필요한 구조 evidence 블록만 추가한다.
3. response model에 `evidence_refs`/`mappings`를 최소 구조로 추가한다.
4. Pydantic과 품질 계약에서 Rule 48/Example 8 고정 상한을 제거한다.
5. validator는 ID/값/경계와 missing/unknown/duplicate만 검사하고 의미를 고쳐 쓰지 않는다.
6. token budget과 whitespace/repetition guard는 폭주 피해 제한 장치로 유지한다.
7. graph mapping 변화가 필요하면 occurrence/provenance migration을 명시한다.

완료 증거:

- corpus-specific literal이 없는 diff
- `RULE_CAP=48`, Rule `max_length=48`, Example `max_length=8`의 의도치 않은 잔존 참조 0
- 유한 evidence 입력에서 출력이 무한 반복되지 않는 adversarial test
- 필수 evidence가 누락되면 성공 저장되지 않는 test
- unit tests
- prompt hash
- 기존 210-test 관련 범위 회귀

### 5단계 — 실행 승인을 받은 뒤에만 재검증

사용자가 새 실제 run을 승인할 때만:

1. 새 output dir과 Neo4j DB 범위를 명시한다.
2. 동일 고정 20/canonical 66을 추출한다.
3. 자동 semantic score 없이 직접 대조한다.
4. baseline D와 field별 delta를 기록한다.
5. cap hit, flood, partial, evidence coverage를 함께 보고한다.

완료 증거:

- 새 run ID/manifest/prompt hash
- immutable review packet
- 직접 판정 ledger
- 로직/프롬프트/model/parser/write 최초 원인

## 18. 재개 첫 15분 체크리스트

0~3분:

```powershell
Set-Location 'D:\work\robo\project\robo-data-analyzer'
Get-Content -Raw -Encoding UTF8 'C:\Users\roede\.codex\memories\principles.md'
Get-Content -Raw -Encoding UTF8 'C:\Users\roede\.codex\memories\manual.md'
Get-Content -Raw -Encoding UTF8 'C:\Users\roede\.codex\memories\speckit.md'
Get-Content -Raw -Encoding UTF8 'D:\work\robo\AGENTS.md'
```

3~6분:

```powershell
git branch --show-current
git rev-parse HEAD
git status --short
Get-Content -Raw -Encoding UTF8 '.specify\memory\constitution.md'
Get-Content -Raw -Encoding UTF8 'specs\110-fixed-20-semantic-accuracy\HANDOFF.md'
```

6~10분:

```powershell
Get-FileHash -Algorithm SHA256 `
  'D:\work\robo\_runs\analyzer-semantic20-20260730-final-rerun6\manifest.json'
Get-Content -Raw -Encoding UTF8 `
  'D:\work\robo\_runs\analyzer-semantic20-20260730-final-rerun6\manifest.json'
Get-Content -Raw -Encoding UTF8 `
  'D:\work\robo\_runs\analyzer-semantic20-20260730-final-rerun6-review\all-field\manifest.json'
```

10~15분:

- `verification.md` 실행 D의 두 실패 함수를 확인한다.
- `branch_inventory.py`, `three_split_llm_fanout.py`, `code_analysis_output.py`의 현재 diff를 읽는다.
- 금지사항을 확인한다: 재실행 금지, source 임의 분할 금지, 별도 LLM repair 금지,
  dirty WIP reset 금지.
- 첫 설계 행동은 `decision_group/effect/exit/symbol_flow`의 **결정론 추출 가능성**을 현재
  parser data로 검증하는 것이다.

## 19. 단계별 완료 정의

### 문서/설계 완료

- 요구사항, 현행 구조, 제안 구조, 결정론/LLM 책임, 금지사항, 반증 기준이 spec에 있다.
- 숨은 대화 문맥이나 질문이 필요한 선택이 없다.
- 함수 slicing은 기본안이 아니며 closure 조건이 명시됐다.

### 코드 작성 완료

- schema/extractor/prompt/validator가 구현됐다.
- 모든 의미 호출이 전체 SOURCE를 유지하고 구조 evidence만 역할별 입력 블록으로 분리한다.
- Rule 48/Example 8 고정 상한이 제거되고 evidence 기반 유한성 검증이 단일 진실이다.
- missing/unknown/duplicate evidence가 명시적 실패 또는 partial로 드러난다.
- 특정 corpus 이름·코드값 hard-code가 없다.
- semantic rewrite/judge/partial repair가 없다.
- 변경 파일과 ownership이 명확하다.

### 테스트 통과

- 새 구조의 unit/property/adversarial tests 통과.
- focused tests와 관련 전체 범위 통과.
- prompt byte identity/hash 통과.
- `git diff --check` 통과.

### 실제 실행 검증

- 사용자 승인된 새 DB/output에서 공식 CLI 완료.
- run ID/manifest/hash/log 보존.
- 고정 20/219/canonical 66 재추출.
- context presence와 직접 의미 판정을 분리.

### 전체 완료

- 필드별 Coverage/Faithfulness/Extra/Exact/structure/write 결과가 있다.
- 낮은 정확도마다 최초 실패 단계와 근거가 있다.
- 고정 Rule/Example cap 제거와 evidence 기반 유한성이 실제 누락·중복·폭주 검증으로 입증된다.
- flood 방어가 실제 중단 로그와 반대 사례에 근거한다.
- 남은 한계가 random/structural/deterministic으로 구분된다.
- 사용자 승인 밖 재실행·외부 변경이 없다.

현재 상태는 “기존 범용 개선 코드 작성·테스트·최종 실제 실행·직접 판정 완료,
구조적 입력 분할·고정 Rule/Example 상한 제거·evidence 기반 유한성은 요구 확정/미구현”이다.
남은 오류가 있으므로 전체 문제 해결 완료라고 쓰지 않는다.

## 20. 문서 자체의 미확정과 갱신 규칙

### 현재 미확정

- GPU server-side whitespace 제한 설정의 실제 적용 가능성과 효과
- decision group/effect/exit/symbol inventory의 언어별 추출 완전성
- 고정 48/8 제거 후 evidence inventory가 모든 언어에서 유한성과 완전성을 동시에 보장하는지
- stereotype basis에서 ontology 우선순위
- glossary identifier/domain-term dual-use 판정의 정확 contract
- b450 delimiter 실패를 구조 단순화가 얼마나 줄이는지
- live Neo4j `test`가 final run 상태를 유지하는지

### 갱신 규칙

1. 새 사실·사용자 정정·실패·결정이 생기는 즉시 해당 절과 상태 기준 시각을 갱신한다.
2. 구현된 것과 제안을 항상 분리한다.
3. hash가 바뀌면 새 hash와 변경 이유를 기록한다.
4. 과거 보고와 현재 실행이 다르면 현재 실행을 권위로 하고 차이를 기록한다.
5. 실행 결과 없이 “테스트 통과”, “실제 실행 검증”, “전체 완료”를 쓰지 않는다.
6. 외부 링크에만 의존하지 말고 이어가기 위한 결정 내용을 본문에 남긴다.
7. 비밀·고객 원문·개인정보는 넣지 않는다.
8. 컨텍스트 압축이나 작업 전환 뒤에는 canonical memory와 이 문서를 다시 읽는다.
9. 사용자 승인 전 final pipeline을 재실행하지 않는다.
10. 이 문서가 다른 handoff와 충돌하면 현재 코드/실행 증거를 확인한 뒤 이 문서를 갱신해
    다시 단일 정본으로 만든다.

### 고립 환경 최종 감사

이 문서만 받은 새 작업자의 첫 행동은 §1 원문과 현재 Git을 읽는 것이다. 작업 경로는
`D:\work\robo\project\robo-data-analyzer`, 현재 기준 branch/HEAD는
`main/a3779491...`, 최종 증거는 `final-rerun6`과 그 review다. 새 작업자는 실제 재분석,
DB 초기화, dirty WIP reset, 임의 함수 분할, 별도 LLM repair를 하면 안 된다. 다음 작업은
전체 SOURCE를 유지한 채 입력 정보를 decision/condition/symbol/effect/exit/fallback 블록으로
구조 분할하고, Rule 48/Example 8 고정 상한을 제거하며, 유한 evidence obligation의
missing/unknown/duplicate를 결정론 검증하는 것이다. 완전성은 gold claim과 stable evidence
ID로 증명해야 한다.
