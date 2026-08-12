# spec 124 — summary delegation grounding

## 문제

- framework 기준 run `20260810_030259_7fd4488113cc`의 summary 416건 중 137건이
  callee 문맥을 받았다.
- `code:apascrtu0010t02.c:c003_call_zapamcom10150`는 입력 구조체 변환·호출·
  결과 전달 래퍼인데 callee의 업무 요약을 글자 그대로 복사하고
  `ApplicationService`로 분류했다.
- DBMS의 의미 근거 없는 외부 `PRC_INSERT_RDRTAGSUNSI` 호출은 물리명의
  `INSERT`를 업무 의미로 번역했다.

## 시도 결과 — prompt-only 기각

- 위임형 대체 행동과 근거 없는 외부 호출의 나쁨/좋음 대조쌍을 공통 prompt에
  1회 추가했다.
- 제한 replay에서 framework 문제 좌표는 고쳐졌지만 DBMS 좌표는 물리명을
  계속 번역했다. DBMS는 spec 123의 기존 단일 결정론 해소기로 전환했다.
- 최종 framework run `20260810_033939_e4dc8b21a5c4`에서 원래 문제 좌표는
  Adapter 책임으로 개선됐지만, 기준 대비 summary 408/416·stereotype 118/416·
  questions 294/416이 변경됐다. 질문은 163→406건으로 늘었다.
- 봉인 표본에서 역할 오류 4건과 의미 확정에 불필요한 질문 증가가 확인됐고,
  direct callee exact-copy 2건도 남았다.
- 04 §4.4의 범용 prompt 개선 1회 종료선에 따라 prompt-only 방향을 기각했다.
  증설분을 제거해 기준 hash `8174f9b4…acde`로 복귀했다.

## 채택 계약

1. callee summary는 prompt 수신자 `ctx.callees`의 direct callee만, 의존이 풀린 후
   canonical summary 하나로 전달한다(spec 120 계약 유지).
2. model summary가 공백·대소문자 정규화 후 direct callee canonical summary와 정확히
   같으면 현재 대상의 책임 소유 실패로 증명한다.
3. 위반 후보는 finding에 원문·callee ID를 보존하되 `summary`, `stereotype`,
   `questions`, `ctx.summary`에 반영하지 않는다. 표시·검색·상위 caller 입력에
   잘못된 의미가 전파되지 않는다.
4. 판정은 `summary_quality.py`의 단일 검출기를 framework/DBMS 적용 경계가 공유한다.
   전략별 후처리나 두 번째 summary 경로를 만들지 않는다.
5. paraphrase·containment는 결정론 증거가 아니므로 자동 거절하지 않고 04 §4 직접
   의미 판정으로 남겨 과잉 격리를 막는다.

## 영향과 검증 결과

- 최종 run 원본 416건에 공용 validator를 적용하면 exact-copy 2건만 격리되고
  414건은 변경되지 않는다.
- 최종 run 2층은 실행 무결성·CALL/RETURN 구조·그래프 계약을 통과했지만,
  실험 prompt의 3층은 역할·질문 퇴행으로 **미통과**다.
- 서버 `get_server_info` 실측은 Qwen3.6-35B-A3B-FP8, TP=4,
  `enable_deterministic_inference=false`다. prompt 재시도를 정확도 수리로 사용하지 않는다.
- 최종 실행 이후 채택한 validator는 테스트·저장 run 영향 재현으로 검증하며,
  framework 냉간 상한이 0이므로 추가 실행 검증은 미검증으로 남긴다.
