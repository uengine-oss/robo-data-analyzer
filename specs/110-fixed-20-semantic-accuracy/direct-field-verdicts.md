# 2026-07-30 고정 20 전 속성 직접 판정 장부

이 문서는 `20260730_062658_3b1c50c4c38d` 재분석 결과를 사람이 원문·고정 골드와
직접 대조한 장부다. 별도 LLM judge, 문자열 유사도, 종합 점수를 사용하지 않는다.

## 판정 단위

- Coverage: 해당 필드 책임상 반드시 보존해야 하는 골드 의무가 의미적으로 들어 있는가
- Faithfulness: 실제 출력의 모든 원자 주장이 활성 원문과 모순되지 않는가
- Extra: 원문에 맞더라도 해당 필드 책임 밖의 불필요한 산출이 있는가
- Exact: 닫힌 라벨과 식별자는 글자 그대로 맞는가
- `summary`, `search_text`, `logical_name`은 서로 대체하지 않는다.
- Rule/Example 219개 의무의 직접 판정은
  `fixed20/direct-verdict-skeleton.json`에서 별도로 진행한다.

## 함수 설명 필드 1차 직접 판정

| 함수 | summary C/F | search C/F | logical | stereotype exact | 핵심 근거 |
|---|---|---|---|---|---|
| `zordmfax:a000_input_validation` | N/Y | N/Y | Y | Y | A/U/D와 M의 필수값 차이, SKIP의 정상 반환과 사유 보존 누락 |
| `zapam10090:b000_main_proc` | Y/Y | N/Y | Y | Y | summary는 회사별 갱신·이력·비배치 연계를 보존, search는 조건 목록 차이를 잃음 |
| `zapam10060:b000_main_proc` | Y/Y | N/Y | Y | Y | summary는 인증 판정·이력·메시지를 보존, search는 업무구분/결과코드 경계를 잃음 |
| `zapam10020:b000_main_proc` | Y/Y | N/Y | Y | N* | search가 기준정보 우선·은행별 fallback·기타 오류를 잃음 |
| `zapam10080:c210_insert_zpay_spc` | Y/Y | N/Y | Y | Y | search가 전송제외 예외와 EDI/FB 대칭 규칙의 방향·값을 잃음 |
| `zapam10100:b200_SMS_transfer` | N/Y | N/Y | Y | Y | 1/6/9 묶음과 대표 템플릿·발송실패 정상 처리 누락 |
| `zapam10200:d400_mms_transfer` | N/Y | N/Y | Y | Y | 1/5/6/9 묶음, N계정 분기, 발송실패 정상 처리 누락 |
| `zordmutl:ordf_chk_adlt_vald` | N/Y | Y/Y | Y | Y | summary가 잘못된 형식의 정상 통과를 누락, search는 이를 보존 |
| `zapam10011:b100_bill_isue_typ_chk` | N/N | N/Y | Y | Y | summary가 대상 없음 정상 진행을 즉시 실패로 뒤집음; search도 그 핵심 예외 누락 |
| `apascrtu:c000_main_proc` | N/Y | N/Y | Y | Y | UI 요청과 인증결과 수신의 상이한 두 경로를 모두 한 일반 문장으로 소실 |
| `zapam10140:c300_call_zapamcom10050_proc` | Y/Y | N/Y | Y | Y | summary는 인증유형 설정·정상 조기종료 보존, search는 대표 overwrite 조건 누락 |
| `apascrtu:c010_rltm_auth_proc` | N/Y | N/Y | Y | Y | 5조건 KSNET, 외부인증 조기종료, EDI/CMS 변경 차이 누락 |
| `zapam10140:d100_call_zapamcom10060_proc` | N/N | N/Y | Y | N | summary가 원장등록 대상의 결과코드 overwrite+호출을 인증 생략으로 바꿈 |
| `zapam20010:b000_main_proc` | N/N | N/Y | Y | Y | summary가 요금계산일 대상 없음 정상 진행을 오류로 바꿈 |
| `zngmmcap:a000_input_validation` | Y/Y | Y/Y | Y | Y | 기능별 필수값 검증과 06/07→03 변환을 보존 |
| `zapam10070:c000_main_proc` | Y/Y | N/Y | Y | Y | summary는 분배기 책임 보존, search는 op_cl별 합류·분리 매핑 누락 |
| `zngmmcap:get_card_ser_num` | Y/Y | Y/Y | Y | Y | 암호화·두 조회·대상 없음 반환을 보존하고 search는 실제 조회명을 보존 |
| `zapam10140:d510_zpay_ap_smpay_rgst_tmp_creat_proc` | Y/Y | Y/Y | Y | Y | 계정별 채번·채널 판정·임시 저장을 보존 |
| `zngmmcap:get_ctz_num` | N/Y | N/Y | Y | N | 실번호와 부분정보를 함께 반환하는 계약 및 RC_NFD 위치 구분 누락 |
| `zordmfax:b450_mq_trns` | Y/Y | N/Y | Y | Y | summary는 변환·라우팅·전송 보존, search는 5+예비 큐와 실패 rc 보존 누락 |

현재 1차 집계:

- summary Coverage 10/20, Faithfulness 17/20
- search_text Coverage 4/20, Faithfulness 20/20
- logical_name Coverage/Faithfulness 20/20
- stereotype frozen-gold exact 17/20
- QUESTION은 20개 모두 0개이며, 고정 골드에 별도 미확정 요구가 없어 Coverage/Faithfulness
  모두 N/A로 둔다.

\* `zapamcom10020:b000_main_proc`의 frozen gold는 `Router`, 현재 출력은 `Validator`다.
현행 stereotype 계약은 “여러 검사 경로가 같은 유효/무효 판정을 만들면 Validator”라고
명시한다. 따라서 이 건은 출력 오류로 단정하지 않고 **골드와 현행 ontology의 계약 버전
충돌**로 별도 분리한다. 골드를 몰래 바꾸지 않는다.

## domain_terms 직접 판정

고정 20개 중 11개 함수에서 최종 70개 항목이 남았다. 현재 계약은 변수명·함수명·테이블명,
일반 프로그래밍어, 뜻이 설명되지 않은 상태 코드를 용어로 금지한다.

직접 검토상 보존 후보는 다음 7개다.

- `N계정`
- `내맘대로인출일코드`
- `SKPAY`
- `내맘대로출금`
- `당월인출가능여부`
- `요금계산일`
- `주민번호사용제한PJT`

나머지 63개는 `zapamcom10060`, `mpfmdbio`, `zpay_ap_req_spc`, `RC_ERR`,
`enc_ctz_corp_biz_num` 같은 모듈·함수·테이블·상수·변수 식별자가 대부분이다.
원문에 표기와 주변 구절이 있다는 사실만으로 “사전 표제어”가 되지는 않는다.

### 확인된 원인

1. 모델이 `0~10`, “10은 목표가 아님” 지침을 받았어도 일부 함수에서 정확히 10개를
   채우며 명시적으로 금지된 식별자를 출력했다.
2. `normalize_domain_terms()`는 `term`과 `evidence`가 원문 부분문자열인지까지만 본다.
   `meaning` 전체가 증거로 입증되는지와 후보가 금지된 식별자 종류인지는 판정하지 않는다.
3. 최종 glossary synthesis도 upstream 후보 집합을 전제로 하므로, 물리 식별자 후보가 대량
   유입된 뒤에는 근거 구절을 복사하는 것만으로 살아남을 수 있다.
4. 그러므로 단순히 상한을 10에서 5나 2로 줄이는 것만으로는 해결되지 않는다. 먼저 후보
   자격을 고쳐야 하며, 상한은 그 다음 폭주 방지 장치다.

## table_insights 직접 판정

고정 20개 중 직접 테이블 접근이 있는 8개 함수, 11개 테이블 항목을 확인했다.

- 테이블 ID와 닫힌 role은 주입된 직접 R/W 범위 안에 있다.
- `zpay_ap_smpay_rgst_tmp`의 READ_WRITE처럼 조회 후 삽입이 모두 있는 경우만 복합 role을
  사용한다.
- `zapamcom10200:d400_mms_transfer`의 `zngm_comm_cd_dtl` 설명은 원문의 MMS 발송
  지연 파라미터 조회를 “조회된 메시지를 출력 구조체에 복사”로 바꿨다. 이 항목은
  Faithfulness 실패다.
- 나머지 항목은 현재 직접 사용 근거와 일치한다. 컬럼별 표현은 canonical 36 컬럼 판정과
  중복 계산하지 않는다.

## 데이터 흐름/구조 결함

- `ComponentNamingOutput`은 `name`, `description`, `stereotype` 세 필드를 생성하지만 현재
  저장 경로는 `name`만 소비한다. 나머지 두 필드는 정확도 대상이 아니라 제거하거나 실제
  저장 계약으로 승격해야 하는 dead output이다.
- component naming 19콜의 immutable log `target_id`가 모두 `unknown`이다. 실제
  `component_id`는 user content 안에만 있어 원인 추적성이 깨진다.
- search_text와 summary는 고정 20개 중 4개가 글자까지 완전히 동일하다. 동일성 자체는
  금지하지 않지만, 복잡 함수에서 search_text가 대표 조건·값을 잃은 현상과 함께 나타난다.

## 다음 판정

1. Rule/Example 활성 219개를 output ID와 evidence span으로 직접 판정
2. Rule/Example에 없는 추가 출력 530개(현재 Rule 184 + Example 346)의 Faithfulness/Extra 판정
3. FILE 23, TABLE 7, canonical COLUMN 36의 현재 출력 직접 판정
4. module pre-analysis 23, module summary 23, final glossary 184, component name 19의
   독립 골드가 없는 부분은 source-grounded supplemental ledger로 분리
5. 실패를 prompt / source context / response model / normalize-filter / graph merge-write로
   분류한 뒤 범용 수정

## Rule 219개 직접 대조 — 수정 전 원인 장부

고정 20개 모두에 대해 활성 gold claim과 현재 Rule statement를 함수별로 직접 대조했다.
문자열 유사도나 별도 LLM judge를 사용하지 않았다. 현재 엄격 기준의 잠정 완전 충족은
`151/219`이며, Example의 독립 판정과 output ID 연결을 마친 뒤 최종 수치를 고정한다.

엄격 판정에서 반복된 실패는 다음과 같다.

- 바깥 가드 누락: 회사구분·업무구분·납부방법·인증유형의 결합 조건 중 일부만 보존
- 필드 혼동: 인증결과코드와 인증유형, 주민 부분정보와 계좌번호, 대상구분과 서비스구분을
  같은 뜻으로 바꿈
- 종료 계약 소실: 대상 없음 정상 진행, 실패 후 기본값 계속, 발송 실패 정상 반환 누락
- 값 매핑 불완전: 상태코드 집합 일부 누락·추가, `3자리 prefix`를 전체 값 비교로 변경
- 실행 순서 소실: 먼저 설정하는 기본값, overwrite, 복구 뒤 재호출, 조기 정상 종료 누락
- 데이터 조립 누락: 메시지 ID 필드 연결, 고정 길이 패딩, 조회 키 조합, 최종 표식
- 주석/비활성 경계 오류: 비활성 과거 코드의 값 또는 현재 활성 처리의 예외를 뒤바꿈

대표적인 사실 오류:

- `ordf_chk_adlt_vald`: 출생 세기 접두사 `18/19/20`을 `1918/1919/1920`으로 생성
- `zapamcom20010`: 회사 `T`·업무구분 `4` 조건을 업무구분 `0`으로 변경
- `d100_call_zapamcom10060_proc`: 인증결과코드 overwrite를 인증유형 변경으로 오인
- `get_card_ser_num`: 별도 DB 조회로 얻는 부분정보를 원본 번호 직접 추출로 설명
- `d510_*`: 플랫폼 대상 없음과 시스템 오류를 합치고, 미매칭 채널의 빈 값을 SWG로 변경
- `b450_mq_trns`: 메시지 ID의 대상구분·주변경코드를 서비스구분·변경코드로 바꿈

이 오류들은 최종 graph write 이전 LLM `code_rules_examples` 응답에 이미 존재한다. parser·merge가
새로 만든 의미 오류가 아니다. 반대로 affected table/op처럼 구조 입력과 결합하는 필드는
후처리 책임을 별도 추적한다.

## canonical schema 66개 1차 직접 대조

- TABLE 7개의 logical_name은 모두 원 스키마 대상과 일치한다.
- TABLE description은 대체로 직접 사용과 맞지만 `zord_acnt`는 선택 근거보다 넓은 계정 속성을
  열거해 Extra 여부를 원문 전체 기준으로 재확인한다.
- COLUMN 36개 중 명확한 의미 오류는 최소 3개다.
  - `zngm_comm_cd_dtl.skvrq_yn`: SK-VAN 원장등록 여부를 일반 자동납부 신청 여부로 변경
  - `zpay_ap_rltm_auth_hst.drw_bank_ctz_num_pinf`: 출금은행 주민 부분정보를
    출금계좌번호 암호화값으로 변경
  - `zpay_ap_rltm_auth_hst.drw_bank_ctz_ser_num`: 출금은행 주민 일련번호를
    출금계좌 일련번호로 변경
- FILE 23개 중 독립 gold intent가 있는 21개를 비교했다. 14개 summary는 핵심 의도를
  충족했고, 7개는 핵심 경로·예외가 빠졌다. `zapamcom10011`은 현재 판정이 무력화됐다는
  gold를 반영하지 않아 사실성도 실패했다.
- module stereotype의 별도 frozen gold는 없으나, 공통 유틸리티 분배기인
  `zordmutl00010`을 `Service`로 둔 것은 현행 모듈 ontology 기준 재검토 대상이다.

## 수정 전 cardinality 관측

- 고정 20: Rule 184개, Example 346개
- 함수당 Rule 최대 18개로 hard emergency cap 32에 닿은 대상은 없다.
- Rule당 Example 관측 최대는 5개로 hard cap 8에 닿은 대상은 없다.
- 따라서 현재 누락의 원인은 cap hit가 아니다. hard cap을 더 낮추면 정확도 문제를 해결하지
  못하고 필수 경로만 버릴 수 있다.
- soft 기준은 대표 1개, 필요한 경계·fallback 포함 1~2개가 적절하다. 3개 이상이면 같은
  결과의 값 변주 통합 또는 Rule 경계 재검토를 요구하고, 8개는 비상 상한으로 유지한다.
