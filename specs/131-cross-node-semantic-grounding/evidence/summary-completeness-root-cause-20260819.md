# APAS FUNCTION summary 완전성 결함 원인 — 2026-08-19

## 판정

final run `20260818_230556_56dd57726596`의 summary finding 4건은 Parser가 파라미터를 누락해서가
아니다. 모델은 complete SOURCE를 받았고 두 건에서는 “선언됐지만 사용되지 않음”까지 정확히 판정했다.
그러나 prompt가 unused/output-only/no-op을 완전한 현재 동작으로 종료시키지 않아 불필요한
`missing_context`를 만들었다. 동시에 routine 전체 흐름을 최대 3 slot으로 압축해 봉인 FUNCTION 10건에서
early bypass, fallback, 조건별 반환이 누락되거나 과장됐다.

## source → actual 결과

| 좌표 성질 | source 사실 | actual 결과 | 원인 |
|---|---|---|---|
| unused parameter + 조회 | 파라미터를 읽기/쓰기/전달하지 않고 table 조회 결과를 global에 저장 | 미사용을 정확히 쓴 뒤 사용 여부 확인을 추가 요구 | 충분성 계약 부재 |
| unused parameter + 즉시 반환 | body가 `RC_NRM` 즉시 반환 | 이름에서 예상한 검사 로직을 요구 | no-op 계약 부재 + 이름 추론 |
| output-only parameter | global length를 읽고 오류 때 output field만 씀 | 물리 동작을 맞게 쓴 뒤 구조/호출 문맥을 요구 | output-only를 input 역할로 확장 |
| 긴 routine 10건 | source 전체와 branch index가 실제 prompt에 존재 | 잠정 exact3/omission2/contradiction5 | 3-slot 과압축, path별 결과 의무 부재 |

## 폐기한 방향

- `fn.params`가 있으면 만드는 `INPUT_ROLE`을 통째로 삭제하지 않는다. 실제 interface와 global input까지
  누락시킨다.
- Analyzer regex로 parameter 사용을 다시 파싱하지 않는다.
- Parser에 불완전한 read/write/pass 또는 C alias 의미를 grammar fact처럼 추가하지 않는다. 향후 별도
  data-flow 계약을 도입하려면 C/Java/Python/SQL의 producer/consumer와 alias 한계를 독립 spec으로
  설계·검증해야 한다.
- Rule prose나 Framework 내부 CALL occurrence 의미 owner를 되살리지 않는다.

## 채택 설계와 검증

routine summary 적용 slot을 최대 다섯 target-owned 역할로 나눈다.

1. `RESPONSIBILITY`: 호출자가 관측하는 책임 경계
2. `INPUT_ROLE`: 실제로 읽는 값, unused/output-only interface의 정확한 상태
3. `GUARDS_AND_BRANCHES`: 조기 종료, 주요 조건, fallback과 조건별 경로
4. `PROCESSING_EFFECTS`: 직접 CALL/RW/대입/transaction/외부 상태 변화
5. `RESULT`: 각 명시 반환·출력과 그 조건

결정론 planner는 실제 구조축이 있을 때만 slot을 만들고 최대 5를 넘지 않는다. LLM은 각 칸의 의미와
충분성만 쓴다. legacy summary는 위 순서로만 projection한다. 자동 테스트 후 새 APAS full cold와 같은
사전 봉인 FUNCTION 10건을 직접 재판정하며, 좋아진 문장 몇 개로 전체 의미 합격을 선언하지 않는다.

## 구현 후 자동 반증

- 처음 만든 no-op fixture는 inventory를 생략해 production 경로를 충분히 재현하지 못했다. 실제
  branch inventory를 붙이자 정상 최종 `return RC_NRM`의 `exit` 행만으로
  `GUARDS_AND_BRANCHES`가 잘못 적용되는 red 실패가 재현됐다.
- guard 적용 기준에서 무조건적인 top-level `exit`를 제거했다. 조건 안의 조기 반환은 소유 decision/
  complement가 이미 guard 축을 만들고, 정상 최종 반환은 `RESULT`만 소유한다. no-op은 실제 inventory를
  사용해 `RESPONSIBILITY/INPUT_ROLE/RESULT` 세 역할만 생성되는 것으로 고정했다.
- 분기만 있는 routine과 대입만 있는 routine을 분리해 guard/processing 적용 가능성이 서로 독립임을
  검증했다. 내부 CALL occurrence 70개 fixture도 summary slot을 늘리지 않고 최대 5를 유지한다.
- legacy summary와 Module member projection은 공통 `FUNCTION_SUMMARY_ROLES` 순서 하나를 사용한다.
  Rule slot은 aggregate 입력에 들어가지 않는다.
- 전체 자동 회귀: pytest `1098 passed, 5 skipped, 378 subtests`; 권위 unittest
  `Ran 1042 tests`, `OK (skipped=5)`; prompt ledger `24/24 byte-identical`.

이 결과는 자동 검증층만 닫는다. 실제 APAS 새 출력의 의미 정확도와 calls/tokens/wall은 다음 full cold와
같은 봉인 40 직접 판정 전까지 미검증이다.
