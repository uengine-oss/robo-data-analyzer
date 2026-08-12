# Framework·DBMS 봉인 의미 표본 40 최종 직접 판정

검증일: 2026-08-12  
봉인 목록: `sealed-sample-40.json`  
Framework DB/run: `spec131fw1520260812` /
`D:\work\robo\_runs\spec131-analyzer-framework-final15-20260812\20260811_164023_345a04a01811`  
DBMS DB/run: `spec131db920260812` /
`D:\work\robo\_runs\spec131-analyzer-dbms-final9-20260812\20260811_160854_56418de412d9`

이 문서는 같은 사전 봉인 표본의 수정 후 graph 값을 source, actual prompt, raw/parsed, 저장 결과와
직접 대조한 증거다. 별도 LLM judge를 쓰지 않았다.

## 1. 판정 계약

```text
source 좌표 → parser AST → Analyzer context/결정론 조립
→ 실제 llm-prompts system/user/schema → raw/parsed
→ normalizer/validator/finding → graph/API/MCP
```

- `exact`: 조건 범위·극성·대상·효과·순서·transaction을 원문과 동등하게 보존했다.
- `needs_context`: 현재 입력에 업무 의미 근거가 없어 빈 의미와 누락 근거를 유지한 것이 맞다.
- `omission`: 말한 내용은 맞지만 결과를 바꾸는 필수 흐름 일부가 빠졌다.
- `contradiction`: 활성 원문과 반대다.
- `fabrication`: 현재 근거에 없는 업무 의미·행동을 만들었다.

TABLE/COLUMN은 이름 번역을 정답으로 인정하지 않았다. DDL/comment/code usage/sample로 직접
입증되는 역할만 인정했다.

## 2. 최종 결과

| 판정 | 수 | 비율/해석 |
|---|---:|---|
| exact | 37 | 원문과 의미 동등 |
| needs_context | 2 | 근거 부족을 창작 없이 보존 |
| omission | 1 | summary의 바깥 handler 한 건 누락 |
| contradiction | 0 | 목표 충족 |
| fabrication | 0 | 목표 충족 |
| 합계 | 40 | join 누락 0 |

정답으로 인정되는 `exact + needs_context`는 `39/40 = 97.5%`다. 이 수치는 봉인40에만 적용하며
RULE 전체, 다른 corpus, 다른 언어·DBMS로 일반화하지 않는다.

| concern | exact | needs_context | omission | contradiction | fabrication |
|---|---:|---:|---:|---:|---:|
| SUMMARY 8 | 7 | 0 | 1 | 0 | 0 |
| RULE 10 | 10 | 0 | 0 | 0 | 0 |
| EXAMPLE 8 | 8 | 0 | 0 | 0 | 0 |
| TABLE 6 | 6 | 0 | 0 | 0 | 0 |
| COLUMN 8 | 6 | 2 | 0 | 0 | 0 |

## 3. SUMMARY 8

| corpus | 대상 | 판정 | 직접 확인한 핵심 |
|---|---|---|---|
| FW | `load_stock_row` | exact | 입력 검증, `W01` fallback, 없음/조회 실패 구분, 출력 매핑, 음수 0 보정, 반환 |
| FW | `settlement_close` | exact | 날짜·대상·보류 조건, rollback/commit, 통지와 통지 실패 |
| FW | `load_target_orders` | exact | 날짜, 대상 필터, 정산 제외, 결제 오류, 최대·반환 건수 |
| FW | `promotion_apply_coupon` | exact | 입력·등급·정책, 0원 경계, 경합 갱신, 재계산·이력·포인트·출력 |
| DBMS | `PRC_CALC_RDD01DD_B2EN2_TEST` | omission | 태그별 계산, 조건별 집계·쓰기, 단계 handler와 transaction은 정확하나 초기화 오류용 root handler 누락 |
| DBMS | `PRC_DATA_ANALYSYS_TEST2` | exact | 기간/태그, 시간·일 집계, 반복 저장, rollback |
| DBMS | `FN_EDIT_YN` | exact | 데이터 종류·처리 유형별 Y/N, 공급량·적산차 예외, 반환 |
| DBMS | `PRC_INSERT_RDD01DD_TB2` | exact | 활성 MERGE update/insert, commit/rollback, 후속 호출·최상위 오류 처리; 주석 DELETE를 실행으로 만들지 않음 |

유일한 omission은 맞는 내용을 뒤집지 않으며 fabrication/contradiction이 아니다.

## 4. RULE 10

아래 10개는 모두 exact다.

| corpus | 좌표 |
|---|---|
| FW | `is_valid_wh:105` |
| FW | `load_pg_company:103` |
| FW | `inventory_inbound:447` |
| FW | `review_register:917` |
| FW | `cart_add_item:324` |
| DBMS | `FN_EDIT_YN:42` |
| DBMS | `FN_EDIT_YN:28` |
| DBMS | `PRC_CALC...:303` |
| DBMS | `PRC_INSERT...WATERSUISUL:13` |
| DBMS | `PRC_DATA_ANALYSYS_TEST2:215` |

마지막 좌표의 rollback은 parser AST→Analyzer effect→actual prompt→graph `then`까지 보존됐다.
Framework continuation R607의 actual prompt는 full inherited `when`을 구조 근거로 유지하면서
`narrative_focus=[]`로 표시했고, 저장 자연어는 상속 조건 나열 대신 직접 `cart_clear` 호출과 `rc`
저장을 정확히 설명했다.

## 5. EXAMPLE 8

`pick_delivery_co`, `promotion_apply_coupon`, `update_order_paid`, `cart_calc_total`, DBMS
`PRC_4RVR...` 3건과 `PRC_CALC...` 1건은 모두 exact다. EXAMPLE은 accepted RULE의 구조
given/then에서 결정론 합성됐고 orphan 0, conditional missing 0, 빈 outcome 0이다.

## 6. TABLE 6

`order_item`, `shipping`, `comm_code`, `TMP_INS_TRAN2FA_RDITAG_TB`, `RDITAGFRD_TB`,
`DEBUGMSG_TB`는 모두 exact다. 각 설명은 직접 READ/WRITE, DDL/comment와 sample 범위 안에서만
역할을 기술한다.

## 7. COLUMN 8

| corpus | 대상 | 판정 | 확인 결과 |
|---|---|---|---|
| FW | `comm_code.code_group` | exact | 코드 분류 그룹 역할과 직접 사용 |
| FW | `member.point_balance` | exact | 포인트 잔액의 적립/사용/취소 갱신 |
| FW | `payment.card_no_enc` | exact | DB 암호화 카드 번호 근거 |
| FW | `member.grade_code` | exact | 회원 등급과 정책/포인트 기준 사용 |
| DBMS | `CALC_RDF01HH_TRNS_TB.COMPUTE` | exact | `DECODE`의 `+/-`를 `1/-1`로 바꾸어 집계 계산에 쓰는 부호 역할 |
| DBMS | `CALC_RDF01HH_TRNS_TB.TAGSN` | exact | 하위 조회의 필터·조인·그룹 기준 |
| DBMS | `RDITAG_TB.ADBA_VAL` | needs_context | 직접 사용 근거 없음; 빈 설명+insufficient+missing_context가 정답 |
| DBMS | `RDF01HH_TB.ETC` | needs_context | 업무 역할 근거 없음; 빈 설명+insufficient+missing_context가 정답 |

`COMPUTE`와 `TAGSN`은 수정 전에는 source 근거가 최종 per-column prompt에 전달되지 않아 비었던
제품 omission이었다. `ADBA_VAL`과 `ETC`는 같은 빈 문자열이어도 원인이 다르며 제품 결함으로
세면 안 된다.

## 8. 전체 및 최종 소비 검증

- Framework final15: SUMMARY 124, RULE 2,049, EXAMPLE 1,747, TABLE 22, COLUMN 153,
  finding 0, structural preflight diff 0.
- DBMS final9: SUMMARY 479, graph RULE 68, EXAMPLE 39, TABLE 16, COLUMN 122, finding 0.
- 전체 회귀: `1062 passed, 5 skipped, 388 subtests passed`.
- prompt byte 원장: `28/28 OK`.
- Framework judge의 leak/vague/truncation/unmatched/orphan/conditional-missing은 모두 0.
- 실제 Analyzer MCP `node_detail(view=gwt)` 재검증:
  - `COMPUTE`: 비어 있지 않은 description, `evidence_status=sufficient`, `missing_context=[]`.
  - `ADBA_VAL`: 빈 description, `evidence_status=insufficient`, 직접 사용 근거 없음 missing_context.

초기 MCP 검사에서 graph 값은 맞지만 direct TABLE/COLUMN gwt projection이 위 세 필드를 버리는
소비자 결함을 발견했다. 공통 projection을 수정해 source description 우선순위와 Analyzer semantic
fallback을 `read_columns`와 일치시켰고, 실제 MCP 응답과 회귀 테스트로 닫았다.

## 9. 수정 전 기준선과 종료선

같은 봉인 목록의 수정 전 판정은 `27 exact + 2 needs_context + 10 omission + 1 contradiction`이었다.
이는 현재 점수가 아니라 회귀 비교 기준선이다. 현재 결과는 `37 exact + 2 needs_context +
1 omission`, fabrication/contradiction 0이며 spec131 Analyzer 합격선을 충족한다.
