## [SUMMARY GROUNDING CONTRACT]

`structural_profile_json.target`이 출력 소유 범위다. `facts`는 source order의 조건·직접 효과·
CALL/RW 등 확정 구조만 가지며 `line|lines`는 활성 `SOURCE`와 맞추는 좌표다. fact ID·occurrence ID·
hash·path·grammar reference는 내부 추적용이다. request-local 결합 key까지 출력에 복사하지 않는다.

`linked_callee_outcomes_json`은 caller가 관측하는 검증된 직접 결과, `linked_external_sources_json`은
현재 TARGET에 결속된 외부 원문만 담는다. callee 내부 절차나 입력 밖 의미를 TARGET 책임으로 만들지 않는다.

전체 SOURCE와 facts를 함께 읽고 입력·선행 검증·결과를 바꾸는 조건·직접 CALL/RW·반환·외부 상태
변화를 실행 순서대로 연결한다. 조건 극성, 비교 방향, literal, 대상과 원인→결과 짝을 보존한다.
이름 모양·일반 상식·주석 처리 코드로 정책을 만들거나 근거 없는 일반 문장으로 빈칸을 메우지 않는다.
짧은 동작도 원문에 보이는 그대로 완전할 수 있다.

조건에서 호출된 opaque 함수의 반환 의미가 `linked_callee_outcomes_json`에 없으면 함수 이름을 보고
null/empty/valid/success 같은 상태로 번역하지 않는다. 이때는 SOURCE의 호출식·비교 연산자·literal을
그대로 한 번 보존해 그 조건에서 실제로 실행되는 효과와 반환만 설명한다. 다시 언급할 때는 `그 조건이
참이면|거짓이면`이라고 지칭한다. `f(x) == Y`를 `x == Y` 또는 x의 자연어 상태로 바꾸는 operand
substitution은 금지한다. 출력 locale의 자연어에 다른 언어의 문자나 단어를 섞지 않되, SOURCE의 물리
식별자와 literal은 원문 그대로 둘 수 있다.
