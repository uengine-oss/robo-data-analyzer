## [SUMMARY GROUNDING CONTRACT]

- `structural_profile_json.target`은 출력 소유 범위다.
- `facts`는 source order의 구조 사실 배열이다. `kind`, `line|lines`, 조건, 직접 효과, 호출과
  table 접근처럼 원문만으로 복원하기 어려운 확정 관계만 들어 있다.
- fact ID, occurrence ID, hash, path, grammar reference와 request-local 결합 key는 입력에 없다.
- `SOURCE`는 활성 원문이다. line 좌표로 구조 사실과 원문을 맞추되 좌표를 출력에 복사하지 않는다.
- `linked_callee_outcomes_json`은 caller가 관측하는 검증된 직접 callee 결과만 담는다. callee 내부 절차를
  TARGET의 동작으로 복사하지 않는다.
- `linked_external_sources_json`은 현재 TARGET에 결속된 외부 원문만 담는다. 입력에 없는 의미를 만들지 않는다.

### 판정 순서

1. 전체 SOURCE와 source-order facts를 함께 읽는다.
2. 입력·선행 검증·결과를 바꾸는 조건·직접 CALL/RW·반환과 외부 상태 변화를 소유 범위대로 연결한다.
3. 조건 극성, 비교 방향, literal, 대상과 실행 순서를 원문대로 보존한다.
4. 이름 모양, 일반 상식, 주석 처리된 옛 코드로 정책·기관·단위·runtime 대상을 만들지 않는다.
5. 확인된 동작만 한 흐름으로 요약하고 근거가 없으면 일반 문장으로 메우지 않는다.

서로 다른 원인과 결과를 교차 결합하지 않는다. `A→X`, `B→Y`는 각각 보존한다. 짧은 구조 동작도
보이는 그대로 완전할 수 있고, 상위 업무 문맥이 없다는 이유만으로 생략하지 않는다.
