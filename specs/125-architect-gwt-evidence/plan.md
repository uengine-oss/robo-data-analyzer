# plan 125

1. shopmall gold와 실제 MCP 호출로 구조 정확도·응답 크기 기준선을 고정한다.
2. RULE 동적 wire를 NL-only로 줄이고 단일 정규화 경계에서 cond/then을 결정론 물질화한다.
3. AST assignment operator를 직접 효과 증거에 보존한다.
4. datasource sample snapshot을 기존 TABLE upsert 계층에 부착한다.
5. 기존 node detail reader에 `gwt` 투영을 추가하고 MCP 목록 RULE을 현행 계약에 정합한다.
6. 단위·계약·gold·직접 의미·실 MCP 크기 검증 후 Architect 소비자 E2E로 넘긴다.

## 영향 경계

- 변경: `pipeline/models/rule_response_schema.py`, RULE 증거의 assignment 표현,
  datasource sample TABLE 저장, `api/mcp_app.py`, `search/corpus/node_detail.py`.
- 불변: 검색 랭킹, Neo4j 노드/관계 식별, 기본 full detail, LLM summary/table insight,
  Data Fabric read-only 정책.

