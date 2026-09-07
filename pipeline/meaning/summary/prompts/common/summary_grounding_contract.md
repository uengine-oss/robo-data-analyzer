## [SUMMARY GROUNDING CONTRACT]

`STRUCTURE`는 Parser가 확정한 부모·자식 구조를 `줄 범위 : 구문 타입`으로만 보여준다. SOURCE의
조건식·호출식·대입식은 여기에 반복하지 않는다. 선택 입력은 실제 값이 있을 때만 제공된다.
`DIRECT TABLE ACCESS`는 현재 TARGET의 직접 R/W, `EXTERNAL SYMBOLS`는 SOURCE 밖에서만 알 수 있는
타입·상수값, `DIRECT CALLEE EFFECTS`는 호출자가 관측할 수 있는 직접 결과만 담는다.
`LINKED PROJECT CONTRACTS`는 현재 TARGET에 결속된 외부 원문만 담는다.

전체 SOURCE와 위 최소 구조·문맥을 함께 읽고 입력·선행 검증·결과를 바꾸는 조건·직접 CALL/RW·반환·외부 상태
변화를 실행 순서대로 연결한다. 조건 극성, 비교 방향, literal, 대상과 원인→결과 짝을 보존한다.
이름 모양·일반 상식·주석 처리 코드로 정책을 만들거나 근거 없는 일반 문장으로 빈칸을 메우지 않는다.
짧은 동작도 원문에 보이는 그대로 완전할 수 있다.

조건에서 호출된 opaque 함수의 반환 근거가 `DIRECT CALLEE EFFECTS`에 없으면 함수 이름을 보고
null/empty/valid/success 같은 상태로 번역하지 않는다. 이때는 SOURCE의 호출식·비교 연산자·literal을
그대로 한 번 보존해 그 조건에서 실제로 실행되는 효과와 반환만 설명한다. 다시 언급할 때는 `그 조건이
참이면|거짓이면`이라고 지칭한다. `f(x) == Y`를 `x == Y` 또는 x의 자연어 상태로 바꾸는 operand
substitution은 금지한다. 출력 locale의 자연어에 다른 언어의 문자나 단어를 섞지 않되, SOURCE의 물리
식별자와 literal은 원문 그대로 둘 수 있다.
