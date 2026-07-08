# RWIS 비즈니스 규칙 정답지 (answer_key)

**작성 2026-07-08. 원문 9파일(`data/source/RWIS/*.sql`) 1회 정독 후 추출한 "정답지".**
이후 검증은 **원문 재독 없이 이 정답지 ↔ Neo4j `test` 그래프의 RULE/EXAMPLE 대조**로 한다(HANDOFF §5, 토큰 절약).

## 사용법 / 판정 규칙
- **단위 = 루틴의 톱레벨 문장(레벨1).** 규칙은 그 톱레벨 문장(IF/LOOP/MERGE/SELECT/INSERT/CURSOR)에 attach 되어야 한다. 깊은 자식 노드에 규칙이 붙으면 = 과추출/파편.
- **완전성 = 원문 기준.** 아래 "핵심 결정(R#)"이 그래프에 **누락 0**이어야 정답. 개수로 판단 금지("적을수록 좋다" 편향 금지).
- **한 톱레벨 문장에 서로 다른 결정이 N개면 규칙 N개** — 특히 UNION ALL 분기(DATA_PROCESS별 집계식), IF 사유 분기.
- **잔심부름(규칙 아님)**: 변수 선언/초기화, `DBMS_OUTPUT`, `COMMIT`/`ROLLBACK` 자체, `L_START:=GET_TIME` 등 계측, 순수 로그 INSERT(WATERSISUL_LOG_TB·RDIDEBUG_TB2·DEBUGMSG_TB의 이력/에러 기록). → 이런 노드에 RULE 0이 정답.
- **죽은코드(주석)는 규칙 근거 금지** — 각 파일 하단 "★죽은코드" 참조. 특히 TB2의 'P=대수평균'.
- **도메인 코드값(공통):** DATA_PROCESS(처리구분) = `M`순시/해당시각값 · `9`·`C`·`A` 순시·상태류 · `Q`평균 · `S`합산 · `D`적산차 · `P`대수평균(정의상)/활성코드는 단순평균 · `F`. KN_CODE=`D`(적산차 기능)·`Q`(적산). BR_CODE=`SQ`(공급량 변량). COMPUTE=`+`(가산)/`-`(감산). TAG_GUBUN=`N`원수·`L`생활·`F`공업·`O`(사외 제외).

---

## 1. FN_EDIT_YN.sql — WEB 품질관리 TAG 데이터 "수정 가능 여부" 판정
**입력** IN_TAGSN, IN_DATAKIND(15/60/24 = 15분/시간/일 테이블 구분) → **반환** V_FLAG('Y'/'N', 'N'이면 사유+하위 적산 TAG 목록).
**초기값** V_FLAG='N'. 조회: RDITAG_TB에서 DATA_PROCESS·BR_CODE(변량)·KN_CODE(기능).

**톱레벨 문장 = 2개** (IF[35-55], IF[59-99]). 깊은 노드 RULE 0.

- **R1 (IF[35-55] / 시간자료 '60' 경로, L36-40):** DATAKIND='60' **且** DATA_PROCESS NOT IN('Q','P') **且** BR_CODE≠'SQ'(공급량 아님) **且** NOT(DATA_PROCESS='D' AND KN_CODE='D')(적산차 아님) → **수정가능 'Y'**. (바깥 IF + 안쪽 IF 조건 합침.)
- **R2 (IF[35-55] / 15분 '15' 경로, L41-44):** DATAKIND='15' **且** DATA_PROCESS IN('Q','P') → **수정가능 'Y'**.
- **R3 (IF[35-55] / ELSE 경로 = 그 외 DATAKIND, L45-54):** 기본 수정불가 'N' + **사유 부착**:
  - DATA_PROCESS IN('Q','P') → `- 15분 테이블을 수정하세요.`
  - BR_CODE='SQ' → `- 공급량 태그는 수정할 수 없습니다.`
  - DATA_PROCESS='D' AND KN_CODE='D' → `- 적산차 태그는 수정할 수 없습니다.`
- **R4 (IF[59-99], L59-99):** V_FLAG='N'(수정불가)이면 → 해당 공급량/적산차 태그의 **하위 적산 TAG 목록**을 콤마 리스트로 조회해 플래그에 추가(하위 수정 유도). 목록 = 4대강 통합공급량(RDI4RVRSUPPLYTAG) ∪ 전국 생활(34162→'LIFE')·공업(34163→'INST', DECODE) ∪ 공급량의 적산차 ∪ 적산차 자기 리스트. 없으면 `!!!적산 TAG_SN을 확인 해 주세요 !!!`.

**핵심 도메인축**: 처리구분(Q/P=15분생성태그) → 시간자료 수정불가·15분자료만 / 공급량(SQ)·적산차(D&D)=수정불가·하위태그 유도.
**★죽은코드**: L102-121 주석 쿼리(적산/적산차 찾기, sub tag 찾기) = 무시. R4의 활성 서브쿼리(L67 `A.DATA_PROCESS='A' AND A.KN_CODE='Q'`)가 진짜; 그 안 주석 `--OR A.DATA_PROCESS='D'...`도 무시.

---

## 2. FN_CALC_RDF01HH_TRNS_TB.sql — 태그 계산값(시/일) 조회 TABLE 함수
**입력** tagsn, log_time → **반환** (TAGSN, 시데이터, 일데이터) TABLE.
**톱레벨 문장 = 1개** (SELECT ~ UNION ALL, L6-51).

- **R1 (합산 행, L9/L23):** 태그의 계산값 = 매핑(CALC_RDF01HH_TRNS_TB)된 **하위(SUB) 태그들의 값 × 계수(COM_UNIT, 없으면 1) × 부호를 가중합**. **부호 = COMPUTE '+'→+1, '-'→-1**(DECODE). TRUNC. 시데이터=시간테이블(RDF01HH_TB), 일데이터=일테이블(RDD01DD_TB) 각각. ROLLUP으로 '합계' 행 생성.
- **R2 (개별 태그 행, L40-50):** 매핑이 없을 때 태그 자기값 = 시간은 24시 레코드(log_time||'24'), 일은 해당일 레코드를 그대로 반환.

**핵심 도메인축**: 공급량/합산 태그값 = 구성 하위태그의 부호·계수 가중합. (RWIS 태그 계산의 기본 공식 = 여러 프로시저에서 반복됨.)

---

## 3. TRG_INS_RDITAG_TB.sql — 태그(RDITAG_TB) INSERT 후행 트리거
**AFTER INSERT FOR EACH ROW.** **톱레벨 문장 = 3개** (INSERT[7-8], IF[10-12], IF[16-22]).

- **R1 (INSERT[7-8]):** 태그 신규 등록 시 → 전송(FA)용 임시 테이블 TMP_INS_TRAN2FA_RDITAG_TB에 **전 컬럼 복제**(+ 전송 시퀀스 SEQ_TRAN2FA_RDITAG_TRSNO.NEXTVAL). = 신규 태그를 상위 시스템 전송 큐에 항상 적재.
- **R2 (IF[10-12]):** KN_CODE≠'D' **且** DATA_PROCESS≠'D'(= **적산차 태그가 아니면**) → PRC_INSERT_RDRTAGSUNSI(순시 태그 등록) 호출.
- **R3 (IF[16-22]):** KN_CODE='Q' **且** DATA_PROCESS='A'(= **적산 태그**) → RDITAGFRD_TB에 (PROCESS_YN='N') 등록 = 후처리 대상으로 미처리 상태 큐잉.

**★죽은코드**: L13 주석 VALUES = 무시.

---

## 4. PRC_INSERT_RDD01DD_TB2.sql — 작업일 일(日) 테이블 재계산·갱신
**입력** WORKDATE. **톱레벨 문장(BEGIN 본문) 중 규칙보유 = MERGE(내부 BEGIN[50-152]), 호출 2개, 외곽 EXCEPTION.**

- **R1 (MERGE INTO RDD01DD_TB, L106-144) — 처리구분별 시간→일 집계 (UNION ALL 3분기, 결정 3개):**
  - 순시류 DATA_PROCESS IN('M','9','C','A') → **24시 시점값 그대로**(VAL, CNT=1). (L113-116)
  - DATA_PROCESS IN('Q','S','D','F') → **`ROUND(DECODE(DATA_PROCESS,'Q',AVG(VAL),SUM(VAL)),4)`** = **Q면 평균(AVG), 그 외(S/D/F)면 합산(SUM)**, 하루치(LIKE WORKDATE||'%') 집계. (L118-130)
  - DATA_PROCESS='P' → **`TRUNC(AVG(VAL),4)` = 단순평균**(활성코드 기준). (L131-137)
- **R2 (동 MERGE, upsert):** 일 테이블에 (LOG_TIME,TAGSN) 있으면 UPDATE(VAL,CNT), 없으면 INSERT.
- **R3 (호출 L154·L157):** 이어서 PRC_INSERT_RDD01DD_TB3, PRC_CALC_RDD01DD_B2EN(용수공급량 재계산) 연쇄 실행.
- **R4 (외곽 EXCEPTION L163-171):** 처리 중 오류 시 ROLLBACK + RDIDEBUG_TB2(SYS_TIME, PROC_ID||오류) 기록. (내부 EXCEPTION L148-151도 rollback+로그.)

**★죽은코드(중요)**: L29-44 주석 DELETE, **L51-103 주석 MERGE** = 무시. 그 죽은 MERGE 안에 **'P' = `LOG(10,AVG(POWER(10,VAL)))` = 대수(로그)평균**이 있으나 **활성코드는 단순평균**(R1 셋째분기). → 규칙에 '대수평균' 등장 시 = 죽은코드 환각(SC-003 위반). 헤더주석 L46 `P(대수 평균)`도 정의일 뿐, 활성 동작은 단순평균.

---

## 5. PRC_INSERT_RDD01DD_TB_WATERSUISUL.sql — 연계 재계산 후 일 데이터·용수공급량 계산
**입력** WORKDATE, V_TAGSN1(적산), V_TAGSN2(적산차). **톱레벨 = MERGE[18-51], SELECT COUNT[55], IF[59-329](6 하위블록), 외곽 EXCEPTION.**

- **R1 (MERGE[18-51]):** 지정 두 태그(RDWMITSPY_TB.TAG1 등록분)의 일 데이터를 시간→일 재계산: 순시(M/9/C/A)=24시값 / Q=평균 / S·D=합산. upsert.
- **R2 (IF[59] 게이트, L59):** TAG_CNT≥1(CALC_RDF01HH_TRNS_TB에 SUB_TAGSN으로 등록된 용수공급량 태그) **且** WORKDATE<TODAY_DD(**과거 일자만** — 금일24-전일24 방식이라) → 아래 R3~R8 수행.
- **R3 (블록1 MERGE[62-104]):** 적산차 = **금일24시 적산 − 전일24시 적산**(TRUNC). 24시값이 0이거나 NULL이면 **23시값 사용**(DECODE). 차이가 **음수(<0)면 그날 시간값 합계**(SUM RDF01HH 01~24)로 대체. 대상 DATA_PROCESS='D'.
- **R4 (블록2 MERGE[119-137]):** 일일 용수공급량 = 구성 SUB태그 **가중합 SUM(VAL×COM_UNIT×부호)**. **부호 COMPUTE '+'→1,'-'→-1, 그외→1**. SUB가 적산차(D)면 **일값(RDD01DD)**, 아니면 **시간합(RDF01HH 01~24)**. 매핑 PROC_GB=2. TRUNC.
- **R5 (블록3 MERGE[158-172]):** 유역별 수종 합계 = 매핑(RDIB2EN_TB) SUB태그의 일값 SUM → 상위 태그.
- **R6 (블록4 MERGE[225-254]):** 광역 합계 = **고정 4태그**(한강10060/금강10070/영섬12858/낙동강10592, BNB_CODE 330/701/702/801 매핑)에 유역합(RDIB2EN, TAG_GUBUN≠'O') SUM.
- **R7 (블록5 MERGE[269-290]):** 수종 합계 = **고정 3태그**(총유입10100 'N'/총생활34162 'L'/총공업34163 'F')에 TAG_GUBUN별 SUM.
- **R8 (블록6 MERGE[305-319]):** 총계 합계 = 태그 **10101**에 전체 유역합(TAG_GUBUN≠'O') SUM.
- **R9 (예외):** 각 하위 블록 독립 EXCEPTION(ROLLBACK + 개별 `n. ERR` 로그), 외곽은 RDIDEBUG_TB2 기록.

**핵심 도메인축**: 용수공급량 집계 파이프라인 = 적산차(24시차·음수보정) → 개별 공급량(부호·계수 가중합) → 유역합 → 광역합 → 수종합 → 총계.
**★죽은코드**: L187-224 주석 MERGE = **전일대비 ±20%(YUL≤90 OR ≥110) 초과 시 전일값(BF_VAL) 치환** 로직. 주석 L184-185 이력대로 **원상복귀(폐기)** → 활성은 그냥 Y.VAL. 규칙에 '±20% 치환' 등장 시 = 죽은코드 환각.

---

## 6. PRC_INSERT_WATERSISUL_MINUTE_2.sql — 적산/적산차 검증 후 시간·일 데이터 재계산(최종 버전)
**입력** STTIME, EDTIME, ACCTAG(적산), ACCTAG2(적산차), PRC_TYPE(1=시간, 그외=시간+일). **톱레벨 = CURSOR C_DATA + LOOP(내부 IF·다수 MERGE).**

- **R1 (CURSOR C_DATA 검증식, L36-39/L107):** 일자별 적산·적산차 정합성 검증 → **'점검'(불일치)** 조건 = SUM(ACCTAG) IS NULL **또는** SUM(ACCTAG2) IS NULL **또는** 시간합(HH24SUM)≠전일차분(NOWMINUS) **또는** 시간합≠적산차합(ACCTAG2). 그 외 '정상'. **'점검' 건만 재계산 대상**(WHERE RESULT='점검').
- **R2 (IF PRC_TYPE=1, L116):** PRC_TYPE=1이면 **시간 데이터부터** 재계산(R3~R5), 아니면 일 데이터만.
- **R3 (MERGE 01-1, L129-156):** **분(RDR01MI) 데이터** 누적으로 시간 적산차 생성. **적산차 산출 로직**: 이전 시각 없으면(LAG 불연속) **0**; 증분이 −1<x<0(미세 음수)면 **0**; 음수(SIGN=−1)면 **직전 FST_VAL 유지**(누적); 정상이면 **증분(VAL−LAG(VAL))**.
- **R4 (MERGE 01-2, L158-186):** **누적된 시간(RDF01HH) 데이터**로 동일 적산차 로직 재계산.
- **R5 (MERGE 02, L188-211):** 적산 태그(ACCTAG)의 시간 적산차를 계산해 **적산차 태그(ACCTAG2)에 기록**.
- **R6 (MERGE 03 일데이터, L233-272):** 일 데이터 = ACCTAG 순시(M/9/C/A) 24시값 / ACCTAG2 = `ROUND(DECODE(DATA_PROCESS,'Q',AVG,SUM),4)`(Q평균/S·D·F합산). upsert.
- **R7 (호출 L290):** 이후 PRC_INSERT_RDD01DD_TB_WATERSUISUL(A1,ACCTAG,ACCTAG2) 연쇄(=파일5의 용수공급량 재계산).
- **잔심부름**: WATERSISUL_LOG_TB 로그 기록(기존값 CHANGE_YN='N' / 변경값 'Y')은 이력이지 도메인 규칙 아님.

**핵심 도메인축**: 적산차 재계산의 핵심 = **음수/이전없음 보정**(누적 유지·0 처리) + 검증 불일치('점검') 건만 처리.

---

## 7. PRC_CALC_RDD01DD_B2EN2_TEST.sql — 개별 태그 용수공급량 일일 재계산
**입력** WORKDATE, IN_TAGSN. 구조는 파일5의 IF 블록과 동일(단일 IN_TAGSN). **톱레벨 = SELECT COUNT[18], IF[25-301](6 하위블록).**

- **R1 (IF[25] 게이트):** TAG_CNT≥1(CALC_RDF01HH_TRNS_TB.SUB_TAGSN=IN_TAGSN = 용수공급량 태그) **且** IN_LOG_TIME<TODAY_DD(과거일만) → 재계산.
- **R2 (블록1):** 적산차 = 금일24−전일24(TRUNC), 24시 0/NULL이면 23시값, 음수면 그날 시간합. (D태그.) [= 파일5 R3.]
- **R3 (블록2 L86-114):** 일일 용수공급량 = 구성태그 가중합(COM_UNIT·COMPUTE 부호), SUB가 D면 일값 else 시간합. **★검보정 반영(2024.05.30 이원경 요청)**: 조건이 `B.SUB_TAGSN=IN_TAGSN` → **`B.TAGSN IN (SELECT TAGSN ... WHERE SUB_TAGSN=IN_TAGSN)`** = IN_TAGSN을 SUB로 갖는 **모든 상위 공급량 태그를 재계산**. (활성 변경분.)
- **R4 (블록3):** 유역별 수종 합계.
- **R5 (블록4):** 광역 합계(본부코드 매핑 4태그). [±20% 치환은 죽은코드.]
- **R6 (블록5):** 수종 합계(N/L/F).
- **R7 (블록6):** 총계 합계(10101).
- **잔심부름/주의**: 예외 블록의 COMMIT·ROLLBACK이 대부분 **주석처리**(`--COMMIT`/`--ROLLBACK`) = TEST 버전이라 커밋 억제. 로그(`n. OK/ERR`)만.

**★죽은코드**: L100-103 주석(옛 `AND B.SUB_TAGSN=IN_TAGSN`) = R3의 변경 전 조건, 무시. L158-195 주석 MERGE(±20% 치환) = 무시.

---

## 8. PRC_4RVR_SUPPLY_CALC_debug.sql — 광역정수장 유입/유출/취수 시간합계
**파라미터 없음.** 대상 태그: 59606(유입합)/59607(유출합)/59637(취수합). **톱레벨 = 3 CURSOR 루프**(취수 C_SW / 유입 C_IN / 유출 C_OUT).

- **공통 게이트 (각 IF `V_?_CNT = C_?_CNT`):** 등록 태그 수(V) = 실제 그 시각 들어온 태그 수(C)일 때 **= 모든 태그값이 도착했을 때만** 합산·기록. 부족하면 계산 생략(→ 간헐 누락 가능, 주석 명시).
- **R1 (취수, L77-124):** TAG_KIND='SW' AND USE_YN='Y' 태그 전부 도착 시 → **ETC IS NULL 태그는 SUM(VAL)**, **ETC='X10' 태그는 SUM(VAL×10)**(배율) → 합쳐 **취수 합계 59637**에 upsert(MERGE).
- **R2 (유입, L138-172):** TAG_KIND='IN' 태그 전부 도착 시 → SUM(VAL) → **유입 합계 59606** upsert.
- **R3 (유출, L185-219):** TAG_KIND='OUT' 태그 전부 도착 시 → SUM(VAL) → **유출 합계 59607** upsert.
- **잔심부름**: V_DEBUG_FLAG 갱신·DEBUGMSG_TB 에러 기록 = 디버그 이력.

**핵심 도메인축**: 광역 합계는 **구성 태그 완전 도착(개수 일치) 조건**에서만 산출 / 취수는 **X10 배율 태그** 존재.

---

## 9. PRC_DATA_ANALYSYS_TEST2.sql — 적산/적산차 검증 후 재계산(MINUTE_2의 이전/테스트 버전)
**입력** STTIME, EDTIME, ACCTAG, ACCTAG2. **톱레벨 = CURSOR C_DATA + LOOP(MERGE 01·02·03).** 파일6의 단순 버전.

- **R1 (CURSOR 검증식, L30-35):** '점검'(불일치) = 시간합(HH24SUM)≠전일차분(NOWMINUS) **또는** 시간합≠적산차합(ACCTAG2). (파일6과 달리 NULL 체크 없음.) '점검' 건만 재계산.
- **R2 (MERGE 01, L138-151):** 적산 시간 적산차 = **현재값 − 직전값(LAG(VAL))** = FST_VAL. (단순 차분, NULL 제외 — 파일6의 음수/이전없음 보정 **없음**.)
- **R3 (MERGE 02, L154-167):** 적산차 태그값 = 적산차의 차분(**FST_VAL − LAG(FST_VAL)**).
- **R4 (MERGE 03 일데이터, L170-213):** 일 데이터 = ACCTAG 순시(M/9/C/A) 24시값 / ACCTAG2 = Q평균·S·D·F합산 / **P=`TRUNC(AVG,4)` 단순평균**. upsert.
- **R5 (호출 L227):** PRC_INSERT_RDD01DD_TB_WATERSUISUL(A1,ACCTAG,ACCTAG2) 연쇄.

**★죽은코드**: L187-189 주석 CASE(Q AVG/else SUM) = R4의 DECODE로 대체됨, 무시. L224-226 주석 호출(TB99/B2EN2) = 무시.

---

## 부록: 파일별 "핵심 결정 개수" 기대치 (과소추출 판정 기준)
개수는 **하한 참고용**(정확 개수 강제 아님 — 원문 결정 커버가 진짜 기준). 톱레벨 문장의 UNION ALL 집계분기·IF 사유분기는 한 문장 안 다중 결정.

| 파일 | 톱레벨 규칙보유 문장 | 핵심 결정(R#) | 비고 |
|---|---|---|---|
| FN_EDIT_YN | 2 (IF, IF) | R1~R4 | 사유 3종은 R3 안에 |
| FN_CALC_RDF01HH_TRNS_TB | 1 (SELECT) | R1~R2 | 부호·계수 가중합 |
| TRG_INS_RDITAG_TB | 3 (INSERT, IF, IF) | R1~R3 | 트리거 |
| PRC_INSERT_RDD01DD_TB2 | 1 MERGE + 2 호출 + 예외 | R1~R4 | R1은 처리구분 3분기 / P=단순평균(죽은코드=대수평균) |
| PRC_INSERT_RDD01DD_TB_WATERSUISUL | MERGE + IF(6블록) | R1~R9 | ±20% 치환=죽은코드 |
| PRC_INSERT_WATERSISUL_MINUTE_2 | CURSOR + LOOP(다수 MERGE) | R1~R7 | 적산차 음수/이전없음 보정이 핵심 |
| PRC_CALC_RDD01DD_B2EN2_TEST | IF(6블록) | R1~R7 | 검보정 조건변경(활성) |
| PRC_4RVR_SUPPLY_CALC_debug | 3 CURSOR 루프 | R1~R3(+완전도착 게이트) | X10 배율 |
| PRC_DATA_ANALYSYS_TEST2 | CURSOR + LOOP(MERGE 3) | R1~R5 | MINUTE_2 단순버전 |

**공통 회귀 감시(SC-004 유지 항목)**: DECODE 값→결과 매핑(Q/S/D/P 집계식·COMPUTE 부호), fetch(SELECT INTO) 조회, INSERT/UPDATE/MERGE op 방향. 이들이 재분석 후에도 규칙에 정확히 반영돼야.
</content>
</invoke>
