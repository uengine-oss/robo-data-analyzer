# 현재 LLM 산출물 검증 매트릭스

기준 실행: `20260730_062658_3b1c50c4c38d`

| LLM concern | 원시 응답 필드 | 최종 사용·저장 위치 | 직접 검증 표본 | 정답 근거 | 판정 |
|---|---|---|---|---|---|
| `module_pre_analysis` | `intent` | 함수 3분할 prompt의 임시 context | FILE 23 | gold `module_intent`, source header/signature | Coverage, Faithfulness, downstream distortion |
| `code_summary` | `summary` | FUNCTION.summary | 고정 20 | field gold + atomic claims + source | Coverage, Faithfulness, Extra |
| `code_summary` | `search_text` | FUNCTION.search_text, callee handoff, 의미 검색 | 고정 20 | search required claims + source | Coverage, Faithfulness, Extra |
| `code_summary` | `logical_name` | FUNCTION.logical_name | 고정 20 | required identity + source | 구별 책임 Coverage, Faithfulness |
| `code_summary` | `stereotype` | FUNCTION stereotype/label | 고정 20 | `stereotype_gold` | exact |
| `code_summary` | `domain_terms[*].term/meaning/evidence` | FUNCTION.domain_terms_json, glossary 입력 | 고정 20의 전 항목 | source evidence, domain notes | 항목별 Faithfulness, 불필요 용어 Extra |
| `code_table_insights` | `table`, `role`, `usage_summary` | 함수 임시 table_insights → TABLE 종합 근거 | 고정 20 중 직접 R/W 함수 | scanner reads/writes, relationships, source | 귀속 exact, role/설명 Faithfulness |
| `code_table_insights` | `columns[*].name/logical_name/description` | TABLE/COLUMN 종합 근거 | 위 함수가 직접 사용하는 column | symbols/tables/source | 이름 exact, 의미 Faithfulness |
| `code_rules_examples` | `rules[*].statement/anchor_line` | RULE, HAS_RULE | 고정 20 / active 219 | atomic claims + source spans | claim Coverage, output Faithfulness/Extra, anchor |
| `code_rules_examples` | `examples[*].given/when/then` | EXAMPLE, HAS_EXAMPLE | 고정 20 / active 219 | Rule 경계 + source | claim Coverage, output Faithfulness/Extra |
| `code_rules_examples` | `affected_tables[*].table/op` | scanner와 결합 후 AFFECTS_TABLE | 고정 20의 모든 Example | direct R/W, operation, source | table/access/op/op_source exact |
| `code_rules_examples` | `questions[*].text/reason` | QUESTION | 고정 20 전 항목 | 전체 evidence로 답할 수 없는지 | 필요성, Faithfulness, Extra |
| `module_summary` | `summary/logical_name/stereotype` | FILE 속성/label | FILE 23 전부 | module intent + member function gold/summary + source | 필드별 Coverage/Faithfulness, stereotype exact |
| `table_description` | table `logical_name/description` | TABLE.logical_name/analyzed_description | TABLE 7 전부 | tables gold, DDL, direct R/W evidence | Coverage, Faithfulness |
| `table_description` | columns `name/logical_name/description` | COLUMN.logical_name/analyzed_description | canonical COLUMN 36 | tables/symbols gold, DDL, usage evidence | name exact, Coverage, Faithfulness |
| `glossary_synthesis` | `meaning/aliases/evidence` | GLOSSARY.terms_json; evidence는 수용 검사 후 미저장 | 현재 최종 184 term 전 항목 | 함수 domain term evidence + source/domain notes | 항목별 Faithfulness/Extra; Coverage는 gold gap 분리 |
| `component_naming` | `name` | 19개 component의 FUNCTION.component_name | component 19 전부 | 결정론 member 집합 + member logical_name/summary/source | 군집 대표성 Coverage/Faithfulness |
| `component_naming` | `description/stereotype` | 현재 코드에서 사용·저장하지 않고 폐기 | schema/dataflow 감사 | response model과 소비 코드 | 정확도 분모가 아닌 dead-output 문제 |

## 기존 정답지 상태

- 확정된 대표 선정
  - 함수: 71개 all-field 원장.
  - 빠른 재실행 회귀: 고정 20개.
  - 스키마: FILE 23, TABLE 7, COLUMN 36.
- 확정된 함수 근거
  - 전체 416함수 source span, atomic claims, must-not-claim, stereotype gold.
  - 고정 20 Rule/Example active claim 219개.
  - 일부 pilot에는 summary/search/logical/rule/example/question/glossary의 명시적 field gold가 있다.
  - 나머지는 기존 직접 판정 원장과 atomic claim/source를 함께 사용해 field obligation을 복원해야 한다.
- 보완이 필요한 현재 산출물
  - raw `table_insights`, 최종 184 glossary term, 19 component name은 예전 8필드 원장에
    완전한 독립 gold가 없다.
  - 이 항목은 출력에서 빼지 않는다. 전 항목 Faithfulness/구조 검증을 수행하고,
    expected-set Coverage만 `gold gap`으로 별도 표기한다.

## 현재 graph 확인

- FUNCTION 계열: 416개.
- RULE: 1,663개.
- EXAMPLE: 2,449개.
- QUESTION: 3개.
- FILE: 23개.
- TABLE: 7개.
- COLUMN: 73개 중 canonical 표본 36개.
- GLOSSARY: 1노드, 184 terms.
- component: 19개, 멤버 FUNCTION에 `component_id/component_name` 저장.

## 이미 확인된 dataflow 문제

`ComponentNamingOutput`은 `name`, `description`, `stereotype`을 생성하도록 되어 있으나
`build_components_step._name_one()`은 `result.name`만 반환한다. 즉 description과
stereotype은 모델 비용을 쓰고도 소비·저장되지 않는다. 이는 의미 정확도 오답과 별개인
response-schema/consumer 불일치로 원인 ledger에 기록한다.
