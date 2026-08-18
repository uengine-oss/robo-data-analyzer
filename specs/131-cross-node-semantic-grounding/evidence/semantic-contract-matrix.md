# Analyzer 의미 생성 계약 matrix

**기준일**: 2026-08-11  
**대상 코드**: analyzer `19e1264` 위 사용자 WIP, parser `42e671b` 위 사용자 WIP  
**actual prompt 기준**: Framework `20260811_052508_ebcac2e2c44c`, DBMS
`20260811_054711_c70a30c7114f`

이 문서는 T131-10의 입력·출력 경계를 고정한다. actual run은 현재 sufficiency schema 변경 전
기록이므로, 저장된 prompt의 사실을 보여 주지만 현재 schema가 실제 GPU run에서 검증됐다는 뜻은
아니다. 업무 의미는 GPU가 source evidence로 생성하고, 결정론 코드는 구조·좌표·소유권·CALL/RW·
전달·검증만 담당한다.

> **2026-08-19 현행 override**: 아래 RULE prompt/bytes 표는 역사 기준선이다. 현재 RULE은
> semantic slot이나 GPU 출력이 없고, branch inventory의 모든 required row를 공통 결정론
> materializer가 exact cond/then/when으로 만든다. `Rule.nl`은 absent/empty이며 검색·MCP·Architect는
> 같은 구조를 해석 없이 렌더한다. `rule-structures/deterministic-rule-v1`이 전수 회계를 소유한다.

## 1. 의미 종류별 계약

| 종류 | 필수 입력 | 선택 입력 | 제외하거나 축소할 노이즈 | 현재 GPU 출력 | validator / normalizer | 저장·소비자 |
|---|---|---|---|---|---|---|
| SUMMARY | target id·절대 파일 좌표·signature·해당 SOURCE, 입력/반환/상태 변화, branch/control index, direct CALL/RW/table operation, transaction effect | 실제로 연결된 parameter/local symbol, callee summary, API contract, external evidence, module/codebase intent | 같은 source의 raw/prompt/numbered 삼중 복제, 빈 evidence section, 실행되지 않는 sibling 원문, 전체 DDL·sample dump | `summary`, `stereotype`, `evidence_status`, `missing_context`, `questions` | 닫힌 exact schema. `sufficient`는 summary+stereotype 및 missing 0, `partial`은 의미+missing, `insufficient`는 의미 빈값+missing을 강제 | `CodeNode.extra` → graph `summary`, `stereotype`, `summary_evidence_status`, `summary_missing_context_json` → node detail/MCP |
| RULE | 절대 좌표가 있는 branch row, 원문 condition, path/guard, direct effect(CALL/RW/RETURN/RAISE/transaction), owner direct CALL/RW | condition에 실제로 등장한 symbol·callee/API evidence | whole procedure 재출력, table sample/DDL, 이름에서 추측한 업무 용어, LLM의 cond/then 재구성 | 각 required row의 `nl`만 | dynamic exact row-key schema. `cond`와 `then`은 row evidence에서 결정론 생성하고 NL의 좌표/참조 누출을 검사 | `RuleOutput` → branch table/graph RULE. `read_rules`가 cond/then/NL/examples/tables를 제공 |
| EXAMPLE | 검증된 RULE id·좌표·결정론 `cond`·`then` | grounded NL은 표시 문맥으로만 사용 | 별도 GPU prompt, source 밖 입력값·결과값 창작 | GPU 출력 없음 | `example_synthesis`가 cond/then에서 결정론 생성, mapping builder가 ID/edge를 fail-fast 검증 | graph EXAMPLE/QUESTION 및 RULE 연결; rule corpus/MCP |
| TABLE usage | code node id·절대 SOURCE·exact table assignment·READ/WRITE/operation·parser data-object evidence | 그 node에 실제 연결된 symbol/callee/API, source DDL comment와 작은 sample은 source 의미 판별에 필요한 경우만 | unrelated ancestor/sibling source, full codebase notes, 다른 table sample, table-wide 중복 context | table별 `role`, `usage_summary`; 현재 `columns` 출력 없음 | required node/table key exact schema, direct assignment allowlist; normalizer는 현재 `columns=[]` 고정 | code-node `table_insights`; step8이 table candidate로 집계 |
| 최종 TABLE | exact db/schema/name, DDL/comment, code-node table usages와 좌표, direct operations, 실제 sample | logical/description source metadata | unrelated code 원문, 빈 candidate 반복, 모든 column의 장문 설명을 table 의미에 재주입 | identity, `logical_name`, `description`, `evidence_status`, `missing_context`, columns bundle | identity/column allowlist exact, sufficient/partial/insufficient 상호 제약, source 없는 번역·확장 금지 | state/graph TABLE → evidence reader/node detail/MCP |
| 최종 COLUMN | exact table/column identity, DDL type/comment, **그 column의 원문 usage + 절대 좌표 + 결정된 table owner + access/operation/condition**, 실제 sample | 같은 table의 최소 table role | table 단위 usage만 주고 column 의미를 요구하는 것, 이름 번역, 다른 table의 동명 column, 빈 `candidates=[]` 반복 | column별 `logical_name`, `description`, `evidence_status`, `missing_context` | exact column allowlist, 의미가 있으면 grounded source 필요; 충분/부분/부족 상호 제약 | state/graph COLUMN `semantic_*` → evidence reader/table node detail/MCP |

`TABLE usage`와 `최종 COLUMN` 사이가 현재 끊겨 있다. step7 schema는 column 출력을 금지하고
normalizer도 `columns=[]`로 만들지만 step8은 `insight["columns"]`에서 column candidate를 모은다.
따라서 final COLUMN prompt는 DDL/comment/sample 외의 code usage를 받지 못한다. T131-12는 이 한 경로를
복구해야 하며, 특정 column 이름이나 정규식 뜻풀이로 우회하지 않는다.

## 2. actual prompt 크기와 top-level payload

아래 byte는 보존 `llm-prompts/*.jsonl`의 UTF-8 `user_content`를 직접 센 값이다.

| corpus / record | records | system B | user B min / p50 / p95 / max | user 총 B | 실제 response schema 주요 필드 |
|---|---:|---:|---:|---:|---|
| Framework `code_summary` | 124 | 950 | 9,888 / 16,858 / 30,451 / 63,735 | 2,246,208 | 구 run: summary/stereotype/questions |
| Framework `code_rules_examples` | 409 | 713 | 5,354 / 12,245 / 22,798 / 25,281 | 5,205,783 | `rules`; 각 row NL |
| Framework `code_table_insights` | 70 | 950 | 7,377 / 14,556 / 26,876 / 53,976 | 1,126,386 | `table_insights`; role/usage_summary |
| Framework `table_description` | 22 | 309 | 8,887 / 11,241 / 15,480 / 18,912 | 259,210 | 구 run: identity/logical_name/description/columns |
| DBMS `code_summary_batch` | 231 | 950 | 7,959 / 13,826 / 18,225 / 21,085 | 3,176,079 | 구 run: node별 summary/stereotype/questions |
| DBMS `code_rules_examples_batch` | 29 | 713 | 5,882 / 7,032 / 13,842 / 15,739 | 245,274 | node/row별 NL |
| DBMS `code_table_insights_batch` | 110 | 950 | 6,667 / 12,458 / 17,894 / 19,771 | 1,396,370 | node/table별 role/usage_summary |
| DBMS `table_description` | 16 | 309 | 8,249 / 9,883 / 27,815 / 37,626 | 226,785 | 구 run: identity/logical_name/description/columns |

공통 prompt 조립은 target/source/coordinates, reads/writes/table operations,
`data_object_usage`, referenced/external/callee/API evidence와 ancestor/module context를 전달한다.
Framework summary/table은 넓은 공통 envelope를 공유하고 DBMS summary/table도 generic
`_build_batch_values`를 공유한다. 반면 RULE은 전용 최소 builder를 사용한다. 그러므로 bytes가 크다는
사실만으로 누락이 없거나 의미가 좋아졌다고 판정할 수 없고, 종류별 projection이 필요하다.

기록기는 각 row에 `system_prompt`, `user_content`, response schema의 `sha256/bytes/schema`,
`response.raw/parsed`, cache/latency/token metadata를 보존한다. cache hit row는 `raw=null`일 수 있으므로
동일 schema와 cache key의 원 raw를 별도로 좇아야 한다.

## 3. 지원 언어·방언 생산자 범위

| 축 | parser 구조 생산자 | Analyzer 의미 소비 경로 | 2026-08-11 판정 |
|---|---|---|---|
| C (`.c/.h`) | Framework AST + CALL/control/RW | Framework fanout → SUMMARY/RULE/TABLE → final TABLE/COLUMN | 실제 Framework corpus(C) run과 봉인 표본 있음 |
| Java (`.java`) | Framework AST + CALL/control/RW | 같은 Framework 경로 | 코드/fixture 범위. 이번 corpus의 의미 E2E 직접 판정 없음 |
| Python (`.py`) | Framework AST + CALL/control/RW | 같은 Framework 경로 | 코드/fixture 범위. 관계식 A만 별도 KEEP이며 이번 의미 수리의 E2E 증명이 아님 |
| Oracle PL/SQL (`.sql/.pks/.pkb/.pls`) | PL/SQL AST, DML object/qualified-column, CALL/control/transaction | DBMS batch → SUMMARY/RULE/TABLE → final TABLE/COLUMN | 실제 RWIS corpus와 봉인 표본 있음. ROLLBACK 포함 구조 replay 있음 |
| PostgreSQL SQL/PLpgSQL | parser PostgreSQL listener와 fixture | Analyzer는 generic DBMS 경로를 탈 수 있으나 Oracle 중심 control/ontology와 실제 corpus 검증이 부족 | parser fixture 수준; 의미 E2E 미검증 |
| COBOL (`.cbl/.cob/.cpy`)·embedded C (`.pc`) | 현재 lexing/extension 인식 축 | 완전한 의미 producer-consumer 계약 없음 | 지원으로 일반화 금지; fixture와 E2E 필요 |

parser의 `qualifiedColumnReferences` 계약은 **명시적인 qualifier.column만** 보존하며 alias 없는 column
소유권은 비범위다. Oracle과 PostgreSQL listener 모두 이 optional 증거를 생산하지만, 현재 실제
RWIS AST의 단일-table SELECT도 `COMPUTE`, `TAGSN` 같은 unqualified column을 보존하지 않는다.

## 4. 한 오좌표의 end-to-end 추적

대상은 `db:CALC_RDF01HH_TRNS_TB.COMPUTE`와 `.TAGSN`이다.

1. **source** — `FN_CALC_RDF01HH_TRNS_TB.sql:10-15,24-29`는 `COMPUTE`를 select하고
   `TAGSN = tagNo`로 filter한다. 바깥 식 `:9,:23`은
   `DECODE(COMPUTE,'+',1,'-',-1)`로 합산 부호에 쓴다. 따라서 두 column의 의미 근거는 충분하다.
2. **parser AST** — 보존 AST의 SELECT `10-15`, `24-29`는
   `dataObjectReferences=[CALC_RDF01HH_TRNS_TB]`지만 `qualifiedColumnReferences=null`이다.
   PL/SQL listener는 `general_element` part가 2개 미만이면 버리는 명시 수식 전용 계약이다.
3. **Analyzer 조립** — direct READ edge와 source는 유지되지만 `data_object_usage_json`의
   `qualified_columns=[]`다. step7 TABLE schema는 column 출력을 금지하고 normalizer도 빈 배열로 만든다.
4. **실제 저장 prompt/schema** — DBMS `code_table_insights_batch.jsonl`의 node `10-15`에는 정확한
   SOURCE와 table id가 있으며 model은 table usage에 `TAGSN = tagNo`를 썼다. 그러나 schema는
   role/usage_summary만 허용한다. 최종 `table_description.jsonl`의 CALC input은 모든 column이
   `candidates=[]`다.
5. **raw/parsed** — 해당 두 record는 cache hit라 `raw=null`; parsed table insight는
   CALC table role/usage_summary를 보존한다. final parsed table 설명에는 TAGSN/COMPUTE 역할이 반영됐지만
   두 column의 logical_name/description은 빈 값이다.
6. **normalizer/validator/finding** — table insight normalizer가 column을 강제로 제거한다. 구 run의 final
   schema에는 sufficiency field가 없어 빈 column을 허용했고 finding은 0이었다. 즉 checker 성공이 의미
   completeness를 증명하지 않았다.
7. **graph/API/MCP** — `rwisverify`와 기본 `neo4j`를 read-only 조회한 결과 CALC table/두 column의 저장
   의미와 `semantic_*`가 비어 있다. READS edge/좌표는 살아 있지만 column 의미에는 연결되지 않는다.
   evidence reader와 node-detail/MCP는 graph의 빈 값을 그대로 소비한다.

결론은 모델이 source를 못 읽은 것이 아니라 parser의 unqualified-column 비범위와 step7→step8 column
usage 계약 단절이 함께 만든 구조적 omission이다. `ADBA_VAL`, `ETC`처럼 source 근거가 실제 부족한
경우와 같은 빈값으로 집계하면 안 된다.

## 5. 다음 게이트

- T131-11은 구 run의 schema 부재를 감안해 빈 문자열, `null`, status 부재, partial/insufficient,
  missing_context, finding을 별도 축으로 전수 센다.
- T131-12 설계는 qualified column만 안전한 다중-table case와 unqualified single/ambiguous-table case를
  분리한다. ownership을 이름 유사도나 업무 의미로 만들지 않는다.
- 현재 sufficiency schema를 사용한 actual prompt bytes와 graph 전달은 격리 재분석 전 bounded replay 및
  prompt contract test로 먼저 증명한다.
