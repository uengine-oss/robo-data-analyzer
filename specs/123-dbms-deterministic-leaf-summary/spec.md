# spec 123 — DBMS deterministic leaf summary

## 문제

- 봉인 run의 DBMS 요약 대상 460개 중 111개는 원문 1줄·직접 증거 0·분기 0인데도
  `code_summary_batch` LLM을 호출한다.
- 그중 `COMMIT`, `DBMS_OUTPUT.PUT_LINE`, `OPEN`/`CLOSE`, `EXIT WHEN` 96개는 PL/SQL
  문장 자체가 의미를 완전히 결정한다. 같은 `PUT_LINE` 3개는 참조 심볼이 있지만 출력식을
  원문 그대로 보존하면 역시 LLM 판단이 필요 없다.
- `SELECT <literal AS alias...> FROM DUAL` 14개도 Oracle 문법상 상수 한 행이며, AST가 모든
  투영식을 literal+alias로 증명할 수 있다.
- 증거 0인 나머지 외부 CALL 1개(`PRC_INSERT_RDRTAGSUNSI`)는 prompt-only 제한 replay에서도
  물리명 `INSERT`를 업무 의미로 번역했다. 04 §4.4 종료선에 따라 추가 문구 조정 대신,
  인자·물리명 원문만 보존하는 중립 위임문을 결정론화한다(spec 124).

## 계약

1. DBMS leaf summary는 단일 결정론 해소기가 `BatchSummaryItem | None`으로 판정한다.
2. 해소 성공/LLM 응답은 모두 기존 `_apply_batch_summary`로만 적용한다. 별도 저장·부모 전달·
   캐시 경로를 만들지 않는다.
3. 결정론 대상은 정확히 파싱된 한 줄 문장만 허용한다.
   - `COMMIT`
   - `DBMS_OUTPUT.PUT_LINE(expr)` — `expr` 원문 보존
   - `OPEN cursor`, `CLOSE cursor`
   - `EXIT WHEN condition` — parser-owned `CodeNode.expression`과 source 조건 일치
   - Oracle AST가 `DUAL` 단일 source와 literal+alias 투영만 증명한 `SELECT`
4. 함수·시퀀스·변수 투영 SELECT, 여러 줄/불완전 문장, 파싱 실패는 LLM으로 되돌린다.
   일반 CALL은 정확한 한 줄 source/name이 일치하고 R/W·참조 심볼·외부 근거·API 계약·
   내부 callee가 모두 없을 때만 인자와 물리 호출명을 원문 그대로 보존한 중립 위임문으로
   해소한다. 의미 근거가 하나라도 있으면 LLM으로 되돌린다.
5. 결정론 결과도 기존 ontology와 같은 `Helper`, 질문 빈 배열 계약을 지킨다.
6. 부모는 기존 `ctx.summary` 하나만 읽는다. 결정론 provenance를 위한 두 번째 summary 필드나
   맵을 만들지 않는다.

## 영향 예측

- 봉인 run: 결정론 해소 114/460 node.
- `code_summary_batch`: 265 호출 중 35 호출 완전 제거, 혼합 24 호출에서 추가 47 node 제거.
- 완전 제거 호출 기준: input 156,349 token, output 3,757 token, 합산 latency 412,613ms 제거.
  latency는 병렬 호출 합계이며 벽시계 단축으로 해석하지 않는다.
- spec 122 table-description 35 호출 제거와 합치면 봉인 run 전체 469 호출 중 최소 70 호출
  제거(-14.9%).
- 최종 DBMS run 전까지 실제 호출 수·벽시계·부모 의미 보존은 미검증이다.

## 근거

- Oracle COMMIT: https://docs.oracle.com/en/database/oracle/oracle-database/26/sqlrf/COMMIT.html
- Oracle DBMS_OUTPUT: https://docs.oracle.com/en/database/oracle/oracle-database/19/arpls/DBMS_OUTPUT.html
- Oracle OPEN/CLOSE/EXIT: Oracle Database 26 PL/SQL Language Reference 각 statement 절
- Oracle DUAL: https://docs.oracle.com/en/database/oracle/oracle-database/26/sqlrf/Selecting-from-the-DUAL-Table.html
