# 고정 20 의미 정확도 검증

문자열 유사도나 별도 LLM judge를 사용하지 않는다. 고정 gold claim, 활성 source,
실제 prompt/parsed response, 최종 graph read-back을 사람이 직접 비교한다.

## 비교 실행 A — prompt 충돌 제거 후

- run ID: `20260730_075154_480012698fb3`
- prompt tree:
  `11e561bc764396f6a90216d88e50bb712528f06a10aa28d29b3f285f391bd393`
- elapsed: 700.9초
- LLM: 991 calls, 7,725,266 tokens
- 고정 분모: 함수 20개, 활성 gold claim 219개
- 출력: Rule 222개, Example 376개, fixed20 domain term 0개, Question 0개

### Rule claim 직접 판정

| 순서 | 함수 | 충족/분모 |
|---:|---|---:|
| 1 | `zordmfax:a000_input_validation` | 11/15 |
| 2 | `zapam10090:b000_main_proc` | 12/13 |
| 3 | `zapam10060:b000_main_proc` | 12/18 |
| 4 | `zapam10020:b000_main_proc` | 3/8 |
| 5 | `zapam10080:c210_insert_zpay_spc` | 8/8 |
| 6 | `zapam10100:b200_SMS_transfer` | 14/16 |
| 7 | `zapam10200:d400_mms_transfer` | 15/16 |
| 8 | `zordmutl:ordf_chk_adlt_vald` | 8/15 |
| 9 | `zapam10011:b100_bill_isue_typ_chk` | 5/6 |
| 10 | `apascrtu:c000_main_proc` | 9/14 |
| 11 | `zapam10140:c300_call_zapamcom10050_proc` | 15/15 |
| 12 | `apascrtu:c010_rltm_auth_proc` | 8/12 |
| 13 | `zapam10140:d100_call_zapamcom10060_proc` | 0/5 |
| 14 | `zapam20010:b000_main_proc` | 7/12 |
| 15 | `zngmmcap:a000_input_validation` | 8/8 |
| 16 | `zapam10070:c000_main_proc` | 8/8 |
| 17 | `zngmmcap:get_card_ser_num` | 5/5 |
| 18 | `zapam10140:d510_zpay_ap_smpay_rgst_tmp_creat_proc` | 11/11 |
| 19 | `zngmmcap:get_ctz_num` | 2/3 |
| 20 | `zordmfax:b450_mq_trns` | 7/11 |
| **합계** |  | **168/219 (76.7%)** |

수정 전 직접 판정 `151/219` 대비 17개 claim이 증가했다. 다만
`d100_call_zapamcom10060_proc` 5개 claim은 rules concern의 whitespace flood
중단으로 Rule/Example이 0개다. 이 5개를 제외한 의미 응답만의 분모는
`168/214 (78.5%)`다. 제외 수치는 전체 정확도를 대체하지 않는다.

### 컨텍스트 전달과 해석

- 219개 gold claim의 실행 근거 줄은 모두 실제 Rule prompt에 글자 그대로 있었다.
- `LEN_ZAPAMCOM10060_IN_CHECK_CARD_RESULT=3`과
  `YY_EIGHTEEN/YY_NINETEEN/YY_TWENTY="18"/"19"/"20"`도
  `referenced_symbols_json`에 있었다.
- 그럼에도 모델은 3자리 prefix를 완전 일치처럼 서술하고, 두 자리 접두사와 입력
  생년월일의 연결을 `1918/1919/2000`으로 잘못 조립했다. 최초 실패는
  `prompt/model-interpretation`이다.
- `zapamcom10020:b000_main_proc`에는 `PFM_TRY` 64회와
  `PFM_CATCH: return RC_ERR`가 있었지만 `PFM_TRY`의 제어 계약은 prompt에 없었다.
  은행 검사 실패를 정상 반환으로 바꾼 최초 실패는
  `context/framework-runtime-handoff`다.
- 따라서 source 문자열 포함과 의미적으로 닫힌 context를 별도 판정한다.

### 폭주와 상한

- whitespace/repetition guard 중단은 7건이다: rules 6건, summary 1건.
- fixed20에서는 `d100_call_zapamcom10060_proc` rules 1건이 영향받았다.
- 용어 concern에서 폭주한 건은 없으므로 이번 실행의 직접 원인은 용어가 아니다.
- guard는 재시도하지 않고 해당 concern을 partial 실패로 보존했다.
- 서버 정보상 `grammar_backend=xgrammar`,
  `constrained_json_disable_any_whitespace=false`다. 클라이언트 guard는 손상
  제한이며 서버 decode 자체의 공백 생성을 예방하지 못한다.
- 함수당 Rule 최대 18개로 Rule 비상 상한 32에는 닿지 않았다.
- Rule당 Example 최대는 8개이며 MMS의 한 Rule이 비상 상한 8에 실제 도달했다.
  나머지 분포는 1개 108 Rule, 2개 87 Rule, 3개 19 Rule, 4개 6 Rule,
  5개 1 Rule이다. 상한을 2로 낮추면 필수 경계가 잘릴 수 있다.

### 다른 산출물

- component 19건의 `target_id`는 모두 실제 ID이며 응답은 소비되는 `name` 하나만 가진다.
- fixed20 raw domain term은 0개다. 이전의 물리 식별자 과다는 제거됐지만,
  `N계정`, `SKPAY` 같은 고유 업무어까지 놓친 과억제 상태다.
- 최종 glossary 후보는 `ICAS`, `실시간 인증결과메시지` 2개였고 후자는
  빈 의미·근거로 거절됐다.
- canonical COLUMN 36개 중 다음 3개 의미 오류가 재현됐다.
  - `skvrq_yn`: SK-VAN 원장등록 여부를 일반 자동납부 신청가능 여부로 확대
  - `drw_bank_ctz_num_pinf`: 주민 부분정보를 은행 지점번호로 변경
  - `drw_bank_ctz_ser_num`: 주민 일련번호를 은행 지점 일련번호로 변경

## 비교 실행 B — context 폐쇄 및 값 역대입

- profile:
  `D:\work\robo\_runs\analyzer-semantic20-20260730-context-closed-profile.yaml`
- run output:
  `D:\work\robo\_runs\analyzer-semantic20-20260730-context-closed-rerun3`
- run ID: `20260730_083055_7b29d1c31792`
- prompt tree:
  `594093e0d8c692b45ab98444f8d053edcee277f3d753c9562034e134c1dcee55`
- elapsed: 757.2초
- LLM: 1,003 calls, 4,255,025 tokens, response cache hit 527건
- 검토 산출물:
  `D:\work\robo\_runs\analyzer-semantic20-20260730-context-closed-rerun3-review`

변경 내용은 다음과 같다.

- referenced symbol의 선언값·초기화 출처를 비교 길이·접두사·연결식에 역대입
- ProFrame skill의 `PFM_TRY` 제어 계약을 결정론적으로 `codebase_notes`에 전달
- source column 중심 명사와 의미 경계를 제출 직전 역대조
- 물리 식별자를 금지하면서 활성 동작이 뜻을 입증하는 고유 업무 표기는 허용
- JSON 문자열 밖 연속 whitespace 조기중단을 64자에서 32자로 축소

집중 회귀는 160 tests, 146 subtests를 통과했고 prompt hash 검사도 통과했다.

### 직접 비교에서 확정한 후속 원인

- 실행 source 근거는 219개 claim 모두 prompt에 들어왔다. 대부분의 잔여 누락은 source
  부재가 아니라 모델의 Rule 과압축 또는 입력에 있는 값 매핑 미사용이다.
- Rule 수 상한 32에는 닿지 않았지만 일부 함수는 상세 값→결과를 Rule statement에서
  지우고 Example에만 펼쳤다. Rule 개수를 줄이는 것보다 statement 완전성이 우선임을
  명시하고, 완전한 한 문장으로 묶을 수 없으면 관측 결과별로 나누도록 계약을 보강했다.
- Example은 대부분 1~2개였지만 복잡한 매핑에서 3개 이상이 계속 나왔다. 따라서 hard max를
  2로 낮추지 않고 현재 8을 비상 상한으로 유지한다.
- stereotype은 20개 중 3개가 달랐다. Validator/Router는 “최종 반환 모양”이 아니라
  직접 판정인지 전문 처리 계약 선택인지로, Adapter는 호출자 I/O↔callee I/O 매핑 책임으로,
  Query는 암호화·조회 보조 호출이 있어도 외부 상태를 바꾸지 않는지로 경계를 보강했다.
- canonical schema의 기존 3개 오류(`skvrq_yn`, 주민 부분정보, 주민 일련번호)는 모두
  source 의미를 보존하도록 수정된 결과에서 해소됐다.
- 반대로 구조체 필드 식별자가 domain term으로 유입되고, table insight 컬럼이 다른 I/O
  구조체의 동명 필드와 섞이는 회귀가 확인됐다. 두 건 모두 prompt 문구가 아니라
  구조 식별자·컬럼 소유권 handoff 로직을 우선 수정했다.
- whitespace guard는 period-1 공백 폭주 4건을 32자에서 중단했다. 서버 decode 설정에
  직접 개입하지 않았으며 부분 JSON을 채택하거나 반복 폭주를 재시도하지 않았다.

## 비교 실행 C — 소유권·분기 계보·구조 잘림 보강

재실행 전 적용한 범용 변경:

- 테이블 이름과 DBIO/ORM 구조체 소유 관계가 입증한 컬럼만 prompt에 전달
- prompt에 보인 컬럼 부분집합을 후처리 allowlist로 동일 사용
- source 구조체 멤버를 domain term 금지 식별자에 포함
- complement 분기에 직접 대응 decision과 상속 외곽 decision 좌표 제공
- 미닫힌 괄호·대괄호·백틱 Rule/Example 문자열을 구조 실패로 거절
- Rule을 줄이기 위한 불완전 매핑 압축 금지와 stereotype 경계 보강

의미를 사후 수정하거나 별도 LLM으로 재판정하는 로직은 추가하지 않았다.

### 실행 C 관측

- run ID: `20260730_091303_e670c3e20eb6`
- prompt tree:
  `2f1213eacc3db9f11bbeef55ca59062c8ce79aee5c28da3dfdff4810eaa7217b`
- 고정 20함수의 active gold 실행 근거는 Rule prompt에 219/219 모두 들어갔다.
  실행 줄 기준으로도 4,139/4,139가 확인됐다. 빠진 세 claim의 full evidence는
  실행문이 아니라 주석 부분이므로 실행 컨텍스트 누락으로 판정하지 않았다.
- MMS 전송 함수는 Rule 32개 상한에 정확히 닿은 뒤 뒤쪽 `T/S/P/default`,
  수신자 거절, 전송 결과 분기가 잘렸다. 따라서 32는 비상 상한으로도 낮으며
  Rule별 의미 완전성을 우선하고 hard max를 48로 올렸다. Example 상한은 Rule당 8로
  유지한다.
- 테이블 컬럼 소유권 격리는 동명 타 구조체 컬럼 오염을 제거했다. 다만 source의
  `comm_cd_val`과 DDL의 `com_cd_val`처럼 인접 문자 하나가 중복된 표기 차이까지
  제거해 버린 새 누락이 확인됐다.
- complement 분기 계보 보강으로 `d510`의 바깥 조건 오상속은 해소됐다.
- MQ Rule의 미닫힌 `mpfmdbio(` 문자열은 구조 검증으로 제거됐고 정상 JSON만
  graph에 기록됐다.
- table description 모델은 source의 `출금은행 주민 부분정보 (암호화)`를
  `계좌 번호의 일부 정보`로 바꿨다. prompt에는 원문 보존 계약이 이미 있었으므로
  최초 실패는 model response이며, 후처리는 새로운 의미를 추론하지 않고 source
  의미 머리말 보존 여부만 검증하도록 했다.
- component 군집은 cross-cutting `Util` 호출을 무방향 Leiden edge로 사용해
  무관한 FAX·계약자·문자열 비교 함수를 한 component로 합쳤다. 이는 naming prompt가
  아니라 입력 군집 로직 문제다.
- stereotype 경계는 여전히 Router/Adapter/Query 및 일부 ApplicationService에서
  흔들렸다. 최종 반환 형태보다 직접 책임·상태 변경·호출자↔callee 매핑을 우선하는
  일반 예시를 보강했다.
- 공백 폭주는 rules와 summary concern에서 여러 차례 발생했으나 모두 조기 중단했고
  부분 JSON은 채택하지 않았다. 이는 용어 프롬프트만의 문제가 아니라 여러 concern에서
  발생하는 서버 decode/model 출력 현상으로 확인됐다.

### 실행 C 이후 범용 수정

- framework 구조체 멤버와 DDL 컬럼 사이에서 인접 동일 문자 하나만 제거되는
  표기 차이를 구조적 별칭으로 허용하되, 최종 출력 이름은 DDL 원문을 유지한다.
- Rule hard max를 48로 올리고 token budget을 함께 조정했다. 48은 목표 개수가 아니라
  무한 출력을 막는 비상 한계다.
- `Util`/`Utility` 노드와 그 incident edge를 component partition 입력에서 제외한다.
  원본 CALLS 관계는 바꾸지 않는다.
- source column 설명의 의미 머리말이 LLM 설명에서 사라지면 source 설명으로
  결정론적 보존한다. 모델의 의미를 사후 생성하거나 정답 문구를 주입하지 않는다.
- 위 변경은 집중 34 tests/24 subtests, 관련 범위 210 tests/156 subtests와
  prompt byte hash 32개 검증을 통과했다.

## 비교 실행 D — 최종 재분석

- run ID: `20260730_094632_f8837581c3a4`
- run output:
  `D:\work\robo\_runs\analyzer-semantic20-20260730-final-rerun6`
- prompt tree:
  `366122f5d9cef7c3c3c2124d6d80f0a2bd0dbb4acf7a66e0a2f81195892ef0fe`
- elapsed: 819.7초
- LLM: 977 calls, input 7,162,691 / output 600,192 tokens
- 검증 패킷:
  `D:\work\robo\_runs\analyzer-semantic20-20260730-final-rerun6-review`

### 컨텍스트·구조

- 고정 20함수 prompt 20/20, active gold 실행 근거 219/219,
  실행 줄 4,139/4,139가 실제 Rule prompt에 있었다.
- 전체 source evidence는 216/219 claim이 완전했다. 빠진 세 건은 실행문이 아닌 주석
  조각이며 실행 컨텍스트 누락으로 판정하지 않았다.
- graph read-back은 함수 416, schema 80을 다시 추출했다. 고정 표본은 함수 20,
  canonical FILE 23/TABLE 7/COLUMN 36으로 정확히 고정됐다.
- `c210_insert_zpay_spc` table insight에 DDL 원문 `com_cd_val`이 복구됐고 다른
  I/O 구조체 컬럼은 섞이지 않았다.
- `drw_bank_ctz_num_pinf`는 `출금은행 주민 부분정보 (암호화)`로 source 의미를
  보존했다. 기존 오류 세 컬럼(`skvrq_yn`, 주민 부분정보, 주민 일련번호)이 모두
  source와 일치했다.
- component naming 입력에서 `swgf_strncmp` 등 Util 노드가 사라졌고, 기존의
  FAX·계약자·문자열 비교 혼합 component는 재현되지 않았다. FAX component는
  FAX 처리와 이력 관리 함수군으로만 구성됐다.

### Rule active claim 직접 판정

문자열 유사도나 별도 LLM judge 없이 active gold claim과 최종 graph Rule을 직접 대조했다.
엄격 기준은 statement 자체에서 조건·값·결과를 모두 복원할 수 있을 때만 충족으로 잡았다.

| 함수 | 충족/active | 누락·오류 claim |
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
| `zngmmcap:a000_input_validation` | 8/8 | - |
| `zapam10070:c000_main_proc` | 8/8 | - |
| `zngmmcap:get_card_ser_num` | 4/5 | c4 |
| `zapam10140:d510_*` | 11/11 | - |
| `zngmmcap:get_ctz_num` | 3/3 | - |
| `zordmfax:b450_mq_trns` | 0/11 | c1~c11 |
| **합계** | **171/219 (78.1%)** | |

`d400_mms_transfer`는 16 Rule로 뒤쪽 `T/S/P/default`, 수신거부, 전송 실패 정상
반환까지 모두 닫혔다. 32개 상한 절단은 해소됐다. 반대로 다음 두 구조 실패가 최종 결과를
크게 떨어뜨렸다.

- `zapam10020:b000_main_proc`: Summary concern이 period-1 공백 폭주로 중단돼
  summary/search_text/logical_name/stereotype이 모두 비었다. Rule은 은행별 분기를
  한 개씩 쪼개 48개 상한에 도달했고, 초기 결과코드와 기타 금융기관 오류 분기가 잘렸다.
- `zordmfax:b450_mq_trns`: 두 Rule 응답 모두 미닫힌 구분자를 포함해 구조 검증에서
  거절됐다. partial JSON은 채택하지 않아 최종 Rule/Example은 0개다.

Example은 보존된 각 Rule 아래에 생성됐지만 Rule statement의 누락을 Example로
보충한 경우는 충족으로 세지 않았다. Rule당 hard max 8에는 닿지 않았다. 함수 전체
Example 수가 큰 것은 Rule 과분할의 결과이며 Example 상한을 낮춰 해결할 문제가 아니다.

### 다른 생성 필드 직접 판정

- stereotype exact는 17/20이다. `zapam10020:b000_main_proc`는 Summary 폭주로
  미기록, `d100_call_zapamcom10060_proc`는 Adapter→Helper,
  `get_card_ser_num`은 Query→Helper로 남았다.
- logical_name은 19/20이 존재했고 `zapam10020:b000_main_proc`만 비었다.
- summary/search_text는 대체로 함수 목적과 핵심 경로를 보존했지만,
  `zapam10020:b000_main_proc`는 둘 다 비었다. `b100_bill_isue_typ_chk`
  search_text는 `RC_NFD` 정상 진행을 일반 조회 실패 오류처럼 넓혔다.
- `d400_mms_transfer` table insight는 컬럼 소유권은 정확해졌지만 발송 지연
  파라미터 조회를 “조회된 메시지를 본문에 복사”로 다시 설명했다. 최초 실패는
  완전한 입력을 받은 model response다.
- fixed20 domain term은 0개로 구조 식별자 오염은 막았다. 다만 glossary synthesis
  후보에는 `mpfmdbio`, `zpay_ap_auth_req_hst` 같은 기술 식별자가 남아 있어
  도메인 사전 후보 품질은 아직 충분하지 않다.
- FILE 23, TABLE 7, COLUMN 36과 module summary 23은 구조·write가 모두 존재했다.
  독립 gold가 없는 module/component/glossary 문구는 source-grounded 보조 판정이며
  닫힌 정확도 점수에 합산하지 않았다.

### 폭주와 마지막 결론

- repetition guard 중단은 총 13회였다. 12회는 period-1 공백 반복, 1회는
  period-504 일반 반복이다.
- 가드는 모두 스트림을 중단했고 부분 JSON을 채택하지 않았다. GPU 서버의
  `constrained_json_disable_any_whitespace=false`는 그대로이므로 폭주 원인은
  해소되지 않았고 피해만 제한했다.
- Rule 48은 `zapam10020:b000_main_proc`에서 실제로 닿았다. 따라서 48도
  정확도 목표가 아니라 최후의 비상 한계일 뿐이며, 큰 dispatch 함수는 향후
  source 범위 분할 또는 Rule grouping 구조를 별도 설계해야 한다.
- 이번 실행을 마지막 사이클로 고정한다. 남은 오류를 맞히기 위한 추가 재실행,
  특정 함수 예외, 별도 LLM 재작성·심판은 수행하지 않는다.
