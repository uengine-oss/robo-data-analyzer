# 저장 의미 모집단 전수 inventory

**기준일**: 2026-08-11  
**도구**: `scripts/inventory_semantic_outputs.py`  
**판정 경계**: 도구는 actual prompt와 graph의 구조·빈값·상태만 세며 사람 의미 판정을 하지 않는다.

실행 대상은 Framework
`20260811_052508_ebcac2e2c44c`/`shopmallseq`, DBMS
`20260811_054711_c70a30c7114f`/`rwisverify`다. 두 graph와 보존 logs는 read-only로 조회했다.

## 1. 전수 회계

### 1.1 actual prompt parsed output

| corpus | SUMMARY | RULE NL | TABLE | COLUMN | finding |
|---|---:|---:|---:|---:|---:|
| Framework | 124, blank 0 | 2,046, blank 0 | 22, blank 0 | 153, blank 0 | 0 |
| DBMS | 345, blank 0 | 64, blank 0 | 16, blank 0 | 122, **blank 122** | 0 |

- Framework COLUMN은 logical+description 140, logical-only 13이다.
- DBMS final TABLE target 16개는 모두 table description이 있지만 그 안의 COLUMN 122개는
  logical_name과 description이 전부 비었다.
- 두 run의 `analysis_findings_summary.json`은 `total=0`, completed다. row 파일이 없는 것은
  summary의 0과 일치하지만 의미 completeness checker가 없었다는 사실도 함께 뜻한다.
- 이 run은 sufficiency 변경 전 schema다. SUMMARY/TABLE/COLUMN 모든 output에서
  `evidence_status`와 `missing_context`가 **absent**다. 따라서 `partial=0`이나
  `insufficient=0`이라고 해석할 수 없다. 상태를 표현할 필드 자체가 없었다.
- DBMS RULE graph 67개 중 3개는 별도 gate prompt가 생성한다. 위 RULE NL 64는
  `code_rules_examples_batch.jsonl`만 센 값이며 graph 총계와 단위를 섞지 않는다.

### 1.2 저장 graph

| corpus / 종류 | 전체 | 의미 blank | 상태 필드 | missing_context |
|---|---:|---:|---:|---:|
| Framework SUMMARY | 124 | 0 | absent 124 | 0 |
| Framework RULE | 2,046 | NL 0 / cond 199 / then 121 | 해당 없음 | 해당 없음 |
| Framework EXAMPLE | 1,847 | given 0 / then **121** | 해당 없음 | 해당 없음 |
| Framework TABLE | 22 target | 0 | absent 22 | 0 |
| Framework COLUMN | 153 target | 0 | absent 153 | 0 |
| DBMS SUMMARY | 463 | 0 | absent 463 | 0 |
| DBMS RULE | 67 | NL 0 / cond 23 / then 10 | 해당 없음 | 해당 없음 |
| DBMS EXAMPLE | 44 | given 0 / then **10** | 해당 없음 | 해당 없음 |
| DBMS TABLE | 51 = target16 + non-target35 | target 0 / non-target 35 | absent 51 | 0 |
| DBMS COLUMN | 251 = target122 + non-target129 | target 122 / non-target 129 | absent 251 | 0 |

DBMS SUMMARY의 `463 graph` 대 `345 GPU parsed` 차이는 누락 118건이 아니다. 현재
`deterministic_summary.py`가 COMMIT/CALL/cursor/EXIT/literal-DUAL 단일 행을 정규식과 AST label로
자연어화한 118건이다. 이는 graph 단위와 GPU 출력 단위를 먼저 분리해야 발견되는 별도 제품 결함이다.

## 2. 정상 빈값과 제품 결함 분류

### 정상 구조 빈값

- RULE의 empty cond Framework199/DBMS23은 unconditional row다. 이 행들에는 EXAMPLE을 만들지
  않아 `RULE - empty cond = EXAMPLE` 수가 정확히 일치한다.
- direct effect 없이 child control만 여는 RULE의 empty then은 Framework68/DBMS5다. RULE 자체는
  “하위 분기를 평가한다”는 control row이므로 구조적으로 허용할 수 있다.
- DBMS에서 접근되지 않아 final table-description 대상이 아닌 TABLE35/COLUMN129의 빈 분석 의미는
  현재 skip 계약상 정상이다. source comment 같은 원본 속성은 별도 보존한다.
- 봉인 표본의 `RDITAG_TB.ADBA_VAL`, `RDF01HH_TB.ETC`는 source에서 업무 의미를 확정할 수 없어
  `needs_context`가 정답이다. 다만 구 graph에는 status가 없으므로 **현재 저장 상태까지 정상인 것은
  아니다**. 새 계약에서는 빈 의미와 `insufficient + missing_context`가 함께 있어야 한다.

### 확정 제품 결함

1. **DBMS target COLUMN 122/122 silent blank** — 모든 항목을 non-empty로 만들라는 뜻이 아니다.
   근거 충분 omission과 정상 needs_context를 구별할 status가 없고, per-column usage도 final prompt에
   전달되지 않았다. 봉인 좌표 중 `COMPUTE`, `TAGSN`은 source 충분 omission, `ADBA_VAL`, `ETC`는
   정상 needs_context임이 직접 확인됐다. 나머지 118개는 T131-12의 usage 전달 후 새 schema가 판정한다.
2. **empty-outcome EXAMPLE 131건** — Framework121 + DBMS10은 given은 있지만 then이 빈 예제를
   graph에 생성했다. 원 RULE이 정상 control-only여도 빈 expected outcome을 가진 EXAMPLE 생성은
   제품 결함이다. effect가 없는 RULE은 예제를 만들지 않거나 검증된 effect를 먼저 생산해야 한다.
3. **DBMS EXIT effect 5건 소실** — empty-then RULE 가운데
   `EXIT WHEN C_DATA%NOTFOUND` 계열 5건은 source에 직접 loop 종료가 있지만 rule evidence의
   `direct=[]`다. NL은 종료를 설명하지만 결정론 then과 EXAMPLE then은 비었다. parser/effect
   vocabulary 생산자 결함이다.
4. **DBMS 동일 좌표 SUMMARY 3그룹/6노드** — 한 줄에 중첩 SELECT가 있을 때 서로 다른 full node id가
   같은 `file:start-end`와 같은 SOURCE를 갖는다. 실제 graph에는 서로 다른 summary가 저장됐지만
   prompt `node_key`는 range뿐이라 대상을 구별하지 못한다. full node path와 AST-owned evidence를
   prompt/schema/validator까지 유지해야 한다.
5. **결정론 자연어 SUMMARY 118건** — source 정규식과 label로 한국어 summary/stereotype을 직접
   만든다. 구조 fact를 만드는 범위를 넘어 업무 의미 생산 경로를 둘로 나누므로 현재 원칙 위반이다.
   즉시 GPU 118호출로 바꾸는 대신 T131-13에서 최소 projection과 비용을 함께 설계한다.

### 추가 생산자 감사가 필요한 구조 빈값

- Framework empty-then RULE 121개 중 68개는 nested control, 나머지 53개는
  `direct=[] && nested_control!=true`다. 대표 source에는 void `return;`, `start++`, `len--`처럼
  실제 effect가 있는 행과 진짜 no-op complement가 섞여 있다. 제품 코드에서 정규식 의미를 만들지
  않고 parser AST/direct-effect producer와 no-op 표식을 감사해야 한다.
- 기존 봉인40의 사람 판정은 `semantic-review-40.md`가 소유한다. 수정 전 실패는 SUMMARY omission7,
  contradiction1, DBMS RULE omission1, DBMS COLUMN omission2다. 대표 SUMMARY2와 transaction
  producer만 current-contract replay를 통과했고 전체40 새 graph 판정은 아직 없다.

## 3. checker·좌표·집계 단위 결론

- finding0은 fabrication/omission0이 아니다. 구 validator는 DBMS COLUMN silent blank, empty EXAMPLE,
  같은-range 중첩 노드를 finding으로 만들지 않았다.
- prompt template에 필드가 있는지와 실제 record bytes는 다르다. CALC 좌표에서는 table usage가
  actual prompt/parsed까지 갔지만 column schema 경계에서 제거됐다.
- graph node, GPU parsed item, RULE obligation, gate RULE, accessed final table target은 서로 다른
  모집단이다. 위 표는 합치지 않고 각각 셌다.
- 동일 `start-end`는 DBMS 한 줄 nested query에서 유일한 의미 좌표가 아니다. 검사기/gold 조인은
  full node id를 우선하고 file/range는 source locator로만 사용해야 한다.

## 4. 재현 명령

```powershell
$env:PYTHONIOENCODING='utf-8'
.\.venv\Scripts\python.exe scripts\inventory_semantic_outputs.py `
  logs-sequence-20260811\20260811_052508_ebcac2e2c44c `
  --database shopmallseq --env-file .env
.\.venv\Scripts\python.exe scripts\inventory_semantic_outputs.py `
  logs-dbms-sequence-20260811\20260811_054711_c70a30c7114f `
  --database rwisverify --env-file .env
```

도구는 `semantic_judgment=false`를 출력하며 지정 run과 database를 읽기만 한다.
