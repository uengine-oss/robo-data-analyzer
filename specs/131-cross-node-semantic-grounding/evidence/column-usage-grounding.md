# code-node → final COLUMN usage grounding

**기준일**: 2026-08-11  
**범위**: T131-12, GPU 재분석 전 생산자/전달/검증 경로

## 결과

step7의 모든 analysis context에 `table → column → source coordinate` projection을 만들고,
table-insight GPU가 각 projected column의 **직접 사용 설명 한 문장만** 생성하도록 연결했다.
normalizer가 full identity와 source를 다시 결합하며 step8은 이를 final COLUMN `candidates` 한 경로로
집계한다.

```text
parser qualified column / parser v2 unqualified SQL identifier
  + exact direct TABLE owner + DDL HAS_COLUMN
  + full code node id + absolute source line
    → column_usage_candidates
      → TABLE insight columns {physical_column: usage sentence}
        → canonical ColumnInsight(identity/owner/lines/source/usage)
          → step8 table별·column별 candidates
            → 기존 final TableDescription/COLUMN schema
              → state/graph/API/MCP
```

## 소유권 규칙

1. parser가 `alias.column`을 exact table에 귀속한 경우 `qualified`다.
2. parser diagnostic의 `language=oracle`인 DBMS leaf node의 exact direct table이 하나뿐이고
   Parser v2 unqualified identifier가 그 table의 DDL column이면
   `sole_direct_table`이다.
3. 같은 DBMS query scope에 direct table이 여러 개여도 그 token이 한 table의 DDL에만 존재하면
   `ddl_unique_in_scope`다.
4. 여러 table에 존재하는 unqualified column, descendant DML/query source가 겹치는 container,
   같은 file/start/end를 공유하는 node는 unqualified ownership을 만들지 않는다.
5. Framework 문자열 SQL은 현재 parser가 column AST를 생산하지 않으므로 source 문자열을 다시
   정규식 파싱하지 않는다. qualified producer가 없으면 후보를 비우고 final needs_context로 닫는다.
6. Oracle outer SELECT가 child SELECT의 projected column을 Parser-owned exact identifier로 소비하고 그 physical
   owner가 하나뿐이면 `derived_output_unique` def-use를 child candidate에 연결한다. parent full node
   id와 line/text를 source line별로 보존한다. 동명 owner가 둘 이상이면 연결하지 않는다.
7. `.sql` 확장자로 방언을 추측하지 않는다. step2가 immutable
   `diagnostics/<source>.parse.json`의 sourcePath/language 일치를 검사해 `source_language`를 넘기며,
   PostgreSQL/diagnostic 부재·불일치에서는 unqualified/derived projection을 닫는다.

이 코드는 column 이름의 업무 뜻을 만들지 않는다. PL/SQL grammar/listener가 function name, SELECT
output alias, 물리 object name, qualified prefix와 nested-query 소유자를 제외한 exact identifier
coordinate를 evidence v2 AST로 낸다. Analyzer는 DDL allowlist와 query-scope owner만 검증한다. 자연어
`usage_summary`는 GPU가 SOURCE에서 생성한다.

## 최소 출력 계약

모델 response는 기존 required node/table key 아래에 다음만 추가한다.

```json
{
  "columns": {
    "PHYSICAL_COLUMN": "이 source에서 입증되는 직접 사용 역할 한 문장"
  }
}
```

column name·owner·line·source를 GPU가 반복 출력하지 않는다. dynamic exact-key schema가 후보의
누락·추가를 막고 normalizer가 결정론 evidence를 결합한다. 후보가 없으면 required `{}`다.

최종 candidate는 `source_id`, `table_role`, `usage_summary`, `ownership`, `evidence_lines`,
`source_lines[{line,text,source_id,ownership}]`를 보존한다. final table/column prompt는 이 candidate를 다른 컬럼에
복사하거나 이름 번역으로 확장할 수 없다.

## 최초 DBMS COLUMN 오좌표 재투영

보존 parser AST의
`PRC_CALC_RDD01DD_B2EN2_TEST.sql:SELECT@90-104`에는 alias `B`가
`CALC_RDF01HH_TRNS_TB`이고 다음 qualified evidence가 있다.

| column | owner | absolute lines | source 역할 |
|---|---|---|---|
| `COMPUTE` | qualified `B` | 90 | `B.COMPUTE`를 projection하여 상위 합산식에 전달 |
| `COM_UNIT` | qualified `B` | 90 | `B.COM_UNIT`을 projection하여 상위 환산식에 전달 |
| `SUB_TAGSN` | qualified `B` | 90, 99 | projection 및 `C.TAGSN` join |
| `TAGSN` | qualified `B` | 97, 104 | `A.TAGSN` join과 하위 TAGSN filter |

별도 단일-table node `FN_CALC_RDF01HH_TRNS_TB.sql:10-15`도 Parser v2 identifier projection으로
`TAGSN:10,15`, `SUB_TAGSN:11`, `COM_UNIT:12`, `COMPUTE:13`을 정확히 복구한다. `ETC`처럼
SOURCE에 없는 DDL column은 후보에 생기지 않는다.

parent SELECT9-20의 child output def-use까지 연결하면 `SUB_TAGSN:9,18,20`, `COM_UNIT:9`,
`COMPUTE:9`가 parent full id와 `derived_output_unique`로 추가된다. 따라서 line9의
`DECODE(COMPUTE,'+',1,'-',-1)`가 실제 column prompt까지 도달한다.

새 column candidate와 table usage를 final 한 prompt에 함께 전달하므로 table 설명에만 흡수되고
column이 비는 이전 단절을 제거한다. T131-13 bounded replay에서 COMPUTE/TAGSN은 non-empty
`sufficient`로 통과했다. 전체 모집단은 T131-15 격리 run에서 별도로 판정한다.

## 검증

- single-table unqualified, multi-table qualified, DDL-unique unqualified, ambiguous shared column,
  same-range fail-closed, Framework no-regex-fallback fixture를 추가했다.
- dynamic schema는 exact column key required/extra-forbid와 normalizer evidence 결합을 검증한다.
- step8 집계 테스트는 full source id/line/text가 final candidate까지 유지됨을 검증한다.
- 관련 국소 회귀와 bounded replay는 `semantic-generation-design.md`가 소유한다.
- C-028/C-033 책임 슬라이스는 Parser 전체 `157 tests, 8 skipped`, Analyzer 전체
  `1093 tests, 6 skipped`, prompt ledger `17/17`을 통과했다. Neo4j `test` transaction에서 quoted
  COLUMN을 저장·조회한 뒤 rollback해 잔여 0을 확인했다.
- 보존 DB/log는 수정하지 않았고 실제 DBMS raw/parsed replay는 `_runs/dbms` 부재로 미검증이다.
