# 최소 출력·evidence projection 설계와 제한 replay

**기준일**: 2026-08-11  
**범위**: T131-13, 현재 배포 Qwen3.6/SGLang와 보존 actual prompt의 제한 호출

## 채택 결과

자연어 의미 생산자는 GPU 한 경로로 통일한다. 결정론 코드는 full identity, source 좌표,
parser 구조, CALL/RW, table/column 소유권과 provider-visible 상태 조합만 만들며 summary나
column 업무 설명을 렌더하지 않는다.

- DBMS 구조 leaf 118개도 기존 summary batch에 포함한다. 118개의 별도 호출을 만들지 않는다.
- 같은 range 노드는 full node id를 의미 identity로 유지하고 wire key가 충돌할 때만
  `range#sha256(full-id)[:16]`을 쓴다. range는 locator다.
- summary 출력은 다섯 의미 필드만 유지한다. Framework single wire는 strict tool 변환이 가능한
  root `result` 아래에 status-discriminated union을 둔다. DBMS batch는 기존 `nodes[key]` 아래에
  같은 union을 둔다.
- table/column 최종 wire는 물리 identity와 `meaning`을 분리한다. 각 `meaning` 안의
  sufficient/partial/insufficient union이 빈 의미·missing_context 조합을 provider 단계에서
  강제하고 저장 직전 canonical 평면 모델로 한 번 정규화한다.
- column response는 physical identity/좌표를 반복하지 않고 exact dynamic key별 usage 한 문장만
  낸다. normalizer가 결정론 provenance를 다시 결합한다.

## 공식 자료와 현재 배포에 적용한 경계

- SGLang은 JSON Schema constrained decoding을 제공하고 prompt에도 출력 지침을 함께 넣도록
  권고한다. 형식 보장은 의미 정확성 보장이 아니므로 schema는 상태·키·유한성만 닫고 업무 의미는
  prompt/source 판정으로 남겼다.  
  <https://docs.sglang.io/docs/advanced_features/structured_outputs>
- SGLang의 prefix caching/RadixAttention은 공통 prefix 재사용 기능이다. logical input token 감소를
  실제 server KV hit로 부르지 않으며 static system/common prefix는 byte 계약으로 별도 검사한다.  
  <https://docs.sglang.io/>,
  <https://docs.sglang.io/docs/advanced_features/attention_backend>
- JSON Schema object의 `required`와 `additionalProperties`는 누락/추가 key를 닫는 계약이다.
  cross-field 의미 상태는 discriminator union으로 별도 닫았다.  
  <https://json-schema.org/understanding-json-schema/reference/object>
- Oracle PL/SQL SQL 문에서 column과 local variable 이름이 충돌하면 column이 우선한다. Oracle
  unqualified lexical projection은 이 방언 근거와 DDL allowlist가 함께 있을 때만 허용한다.  
  <https://docs.oracle.com/en/database/oracle/oracle-database/26/lnpls/column-name-precedence.html>
- PostgreSQL PL/pgSQL은 variable conflict 기본 동작이 같지 않고 `use_column` 설정이 필요하다.
  parser diagnostic이 PostgreSQL이면 unqualified projection을 하지 않고 qualified evidence만 쓴다.  
  <https://www.postgresql.org/docs/16/plpgsql-porting.html>
- Oracle `DBMS_OUTPUT.PUT_LINE`은 line을 buffer에 넣는다. `표준 출력`으로 확장하는 것은 source 밖
  채널 창작이므로 구조 leaf replay의 실패로 판정했다.  
  <https://docs.oracle.com/database/121/ARPLS/d_output.htm>

## 제한 replay

모든 산출물은 `D:\work\robo\_runs\spec131-bounded-replay-20260811-*`의 새 root에만 썼다.
기본 DB와 보존 run은 수정하지 않았다.

### 구조 leaf summary

`PRC_4RVR_SUPPLY_CALC_debug.sql`의 OPEN77, EXIT82, COMMIT118, CLOSE125, CALL232를 사용했다.

- v1: 직접 동작을 모두 설명하고도 status를 `insufficient`로 내 provider 통과 뒤 내부 validator가
  거절했다. 기존 schema가 cross-field 상태를 provider에 보이지 않은 결함이었다.
- v2: status union 뒤 5/5가 non-empty sufficient/Helper로 통과해 GPU 단일 생산자 가능성을
  확인했다.
- v3~v6: exact 식별자 규칙을 강화했지만 EXIT의 `%NOTFOUND`를 EOF/다음 행 없음으로, CALL의
  `DBMS_OUTPUT.PUT_LINE`을 표준 출력으로 확장하는 결과가 남았다. format 통과는 fabrication 0이
  아니며 full run의 source 대조 validator/직접 판정이 필요하다.
- Framework 보존 `create_trace` actual prompt를 current contract로 strict function replay했다.
  root union은 LangChain tool 변환 전 `parameters` 오류가 났고 root `result` + nested union으로
  바꾼 뒤 2.7초, input8016/output296 token, schema/내부 validation을 통과했다.

### COLUMN omission 좌표

`FN_CALC_RDF01HH_TRNS_TB.sql`의 outer SELECT9-20 → child SELECT10-15를 사용했다.

- 최초 column insight는 child line10-15만 받아 COMPUTE를 “SELECT 결과 포함”으로만 설명했다.
- parser parent/child와 exact identifier를 사용한 derived-output def-use projection을 추가했다.
  line9의 `DECODE(COMPUTE,'+',1,'-',-1)`는 child의 유일한 physical COMPUTE projection으로 연결됐고,
  source_id/ownership/line/text가 actual prompt에 저장됐다.
- 새 code-table insight는 COMPUTE를 “DECODE를 통한 계산 부호 결정”, TAGSN을 `tagNo` filter로
  출력했다(input2863/output193 token).
- 최종 TABLE/COLUMN replay는 COMPUTE와 TAGSN 모두 non-empty `sufficient`, missing_context 빈 배열로
  통과했다(input4300/output408 token). COMPUTE 설명은 `+/-`에 따라 1/-1로 변환돼 계산식에
  적용된다는 source 역할을 보존했다.
- final v1은 table status/meaning 조합이 내부 validator에서 거절됐고 v2의 root sibling oneOf는
  provider가 부분 객체만 생성했다. nested `meaning` union으로 바꾼 v3는 정직한 insufficient를,
  prompt/derived evidence가 갖춰진 v4는 근거 충분 sufficient를 정상 생성했다.

## 남은 제한

T131-14에서는 prompt만으로 막히지 않은 구조 식별자 변형을 공통 validator 경계에서도
검사하도록 했다. CALL 대상, EXIT/RETURN/THROW/RAISE 식의 물리 식별자, ASSIGNMENT 대상을
모델 문장에 그대로 보존하지 않으면 후보를 `rejected` finding으로 격리한다. 이 검사는
식별자로 자연어 의미를 만들지 않고 parser가 제공한 구조의 누락만 검증한다. 같은 검사를
Framework 단건과 DBMS batch 생산자 모두에 적용했다. C의 독립 `++`/`--` 문장은 parser가
정규 ASSIGNMENT 구조로 생산하게 고쳐 Analyzer와 모든 후속 소비자가 같은 사실을 받는다.

- Analyzer 관련 회귀: `624 passed, 160 subtests passed`
- parser 전체 회귀: `mvnw test` 통과
- prompt byte baseline: 28개 갱신 후 `OK - 28 prompts byte-identical`

- replay는 전체 corpus나 모든 언어의 품질을 증명하지 않는다.
- 구조 leaf exact API/식별자 보존은 schema만으로 강제할 수 없다. 새 격리 run에서 actual raw와
  source를 대조해 fabrication/contradiction을 별도 0건으로 판정해야 한다.
- derived-output projection은 Oracle SELECT parent/child, exact identifier, 유일한 physical owner일
  때만 열린다. alias/동명/동일 range는 fail closed다.
- ADBA_VAL처럼 값 복사만 보이는 column과 ETC처럼 직접 usage가 없는 column은 새 schema가
  `insufficient + missing_context`를 허용한다. 실제 판정은 격리 run에서 확인한다.
