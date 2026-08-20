## [INPUT GUIDE — 각 값의 뜻·권한·허용 판정]

- `TARGET`의 id·signature·file·source_range는 **이번 출력의 소유 범위**다. 같은 prompt 안의
  형제·callee·module 의미를 TARGET 책임으로 옮기지 않는다. `source_status`가 available이
  아니면 SOURCE 부재를 정상 코드처럼 읽지 말고 sufficiency에 반영한다.
- `SOURCE`의 일반 줄은 이 범위에서 실행되는 활성 코드 원문이며 동작·조건·순서 판정의 최우선
  근거다. 줄번호 접두사는 좌표일 뿐 코드가 아니다. 단, 긴 부모 범위에서
  모델 prose로 치환된 자식 범위는 권위 입력으로 사용하지 않는다. 현재 호출에 배정되지 않은
  source/evidence obligation은 다른 canonical fragment가 소유한다.
- `EFFECTIVE_FLOW_EVIDENCE`의 `effective_reads_json`·`effective_writes_json`은 TARGET 전체
  흐름에서 관측되는 테이블 방향이다. summary의 CALL/RW 누락 방지와 stereotype 모순 검사에
  쓰되, 한 하위 문장이 직접 소유한 접근이라고 재귀속하지 않는다. 각 table record의 source
  description/sample은 이미 연결된 table의 역할 보조일 뿐 sample 몇 개로 정책을 만들지 않는다.
- `linked_table_operations_json`은 현재 TARGET id에 직접 연결된 table operation이다. effective
  table 목록의 모든 접근이 현재 줄의 직접 연산이라는 뜻은 아니며, table id가 일치하는 구조
  동사를 확인할 때만 쓴다.
- `direct_data_object_usages_json`의 관계는 `TARGET_ACCESSES_DATA_OBJECT`다. 각 레코드의
  `source_id`가 직접 접근한 객체, READ/WRITE, 절대 좌표, exact/unresolved 해석을 잇는다.
  `resolution=unresolved`인 대상을 exact table이나 runtime identity로 확정하지 않는다.
- `target_referenced_symbol_definitions_json`의 관계는
  `TARGET_REFERENCES_DEFINITION`이다. SOURCE에 쓰인 식별자를 같은 `name`의 선언값·초기화
  출처와 연결할 때만 사용하며, 선언에 없는 업무 뜻은 만들지 않는다.
- `target_linked_external_context_json`의 관계는
  `TARGET_REFER_TO_EXTERNAL_CHUNK`이다. `matched_key`로 실제 연결된 `numbered_text`만 사용한다.
- `CALLEE_INTERFACES`의 관계는 `TARGET_CALLS_CALLEE`다. 현재 TARGET이 무엇을 넘기고 결과를
  어떻게 소비하는지는 SOURCE에서 판정하고, callee summary는 호출 경계의 계약 이해에만 쓴다.
- `SURROUNDING_CONTEXT`는 범위·용어 보조 정보이며 TARGET 동작의 증거가 아니다.
- `[]`·`{}`·`(없음)`은 해당 생산자가 이 TARGET 범위에서 확인한 근거가 없다는 뜻이다.
  SOURCE와 다른 필드가 입증하는 사실까지 없다는 뜻은 아니며, 빈 배열을 상식으로 채우지 않는다.

### 관계를 따라 읽는 순서

1. TARGET 좌표로 출력 소유 범위를 고정한다.
2. SOURCE 일반 줄의 조건·대입·호출·반환을 절대 줄 순서로 읽고, 치환된 자식 범위는
   `CONTROL_FLOW_INDEX`의 정확한 조건·연산자와 CHILD_SUMMARY_CANDIDATE의 효과 후보를 분리해 읽는다.
3. SOURCE의 식별자·호출·테이블 접근을 relation과 `source_id`/좌표가 같은 구조화 근거에 연결한다.
4. effective R/W와 control index로 빠진 흐름이 없는지 대조한다.
5. 그 뒤에만 surrounding context로 이미 입증된 표현을 사람이 읽기 쉽게 정리한다.

## [DECISION — summary + evidence_status + missing_context]

이 절은 `summary`, `evidence_status`, `missing_context` 세 필드에만 적용된다.
최우선 임무는 TARGET SOURCE 전체를 읽고, 호출자가 실제 동작을 이해할 수 있는 자연어 흐름으로
정확히 요약하는 것이다. 목적 제목 한 줄이나 식별자 목록으로 축소하지 않는다.

### 먼저 보는 같은 입력의 대조 예시

입력 SOURCE가 다음 순서라고 하자.

```text
입력이 비어 있으면 INVALID를 반환한다.
기준 행을 조회한다. 행이 없으면 NOT_FOUND, 조회 실패면 ERROR를 반환한다.
수량이 요청량보다 작으면 부족 이력을 기록하고 OUT_OF_STOCK을 반환한다.
그 밖에는 수량을 차감하고 성공 결과를 반환한다.
```

- 잘못된 출력: `기준 행을 조회해 수량을 차감한다.`
  이유: 입력 검증, 없음/조회 실패의 구분, 부족 조건과 이력, 반환 흐름이 사라졌다.
- 올바른 출력: `빈 입력은 INVALID로 종료한다. 기준 행 조회 결과가 없으면 NOT_FOUND, 조회가
  실패하면 ERROR를 반환한다. 재고가 요청량보다 부족하면 부족 이력을 남기고 OUT_OF_STOCK으로
  종료하며, 충분하면 수량을 차감하고 성공 결과를 반환한다.`
  이유: 결과를 바꾸는 조건·대상·효과·순서가 모두 보존됐다.

입력 SOURCE에서 제거 작업이 주석 안에만 있고, 활성 코드는 기존 행을 갱신한다고 하자.

- 잘못된 출력: `기존 행을 삭제한 뒤 새 데이터를 저장한다.`
  이유: 주석·이력 문구를 활성 동작으로 승격했다.
- 올바른 출력: `활성 코드가 계산한 값으로 기존 행을 갱신한다.`
  이유: 실행되는 문장만 보존했다.

### 1. 입력 충분성을 먼저 판정

1. SOURCE, 직접 CALL/RW, callee summary, 상수·API·테이블 근거만으로 입력→검증→주요 분기→
   처리→반환/외부 상태 변화의 전체 흐름을 확정할 수 있으면 `evidence_status="sufficient"`,
   `missing_context=[]`다.
2. 일부 동작만 확정되고 결과를 바꾸는 나머지 의미가 현재 입력 밖에 있으면
   `evidence_status="partial"`이다. `summary`에는 확정한 부분만 쓰고 `missing_context`에는
   빠진 근거를 구체적으로 쓴다.
3. 현재 입력만으로 대상 책임 자체를 확정할 수 없으면 `evidence_status="insufficient"`다.
   이때 `summary=""`, `stereotype=""`로 두고 `missing_context`에 필요한 근거를 쓴다.
4. `missing_context`는 막연한 질문이 아니라 필요한 원문·callee 계약·상수 정의·테이블 metadata·
   runtime 사실을 구체적으로 적는다. 동적 SQL/dispatch/DB link 실제 대상은 입력으로 입증되지
   않으면 확정하지 않는다.

현재 TARGET의 범위가 `COMMIT`, 커서 OPEN/CLOSE, `EXIT WHEN`, 정적 CALL, 로그 출력처럼
한 문장짜리 구조 동작이면 **그 문장 자체가 TARGET의 전체 책임**이다. 부모 루틴의 업무 목적이나
커서 SELECT 전체를 모른다는 이유로 `partial`/`insufficient`로 낮추지 않는다. SOURCE가 그 직접
동작의 대상·조건·효과를 모두 보여 주면 `sufficient`이며, 부모 문맥의 업무 뜻을 덧붙이지 않는다.
이때 식별자나 API 이름의 역할을 입력이 설명하지 않으면 확장 번역하지 않는다.
`EXIT WHEN`의 조건식은 현재 SOURCE의 식별자까지 글자 그대로 보존하고 `%NOTFOUND`를 `EOF`나
`다음 행 없음`으로 바꾸지 않는다. `DBMS_OUTPUT.PUT_LINE`도 호출명을 보존하며 `표준 출력`처럼
입력에 없는 채널이나 업무 오류 정책을 덧붙이지 않는다.

현재 TARGET이 입력을 검사·기본값 처리한 뒤 출력 버퍼나 반환값을 원문 형식으로 직접 조립하면,
그 조립 흐름 자체가 확정 가능한 전체 책임이다. 생성 결과의 호출 위치·후속 사용처·업무상 이름을
모른다는 이유로 `partial`로 낮추거나 질문을 만들지 않는다. SOURCE가 보여 주는 입력 조건,
기본값, 포맷 폭·구분자, 출력 대상만 보존해 `sufficient`로 쓴다.

`ast_type=EXIT`이면 summary 문자열 안에 현재 입력의 `ast_expression` 전체를 **한 글자도 바꾸지
않은 연속 부분문자열로 반드시 포함**해 “조건식이 참이면 현재 루프를 종료한다”로,
`ast_type=CALL`이면 SOURCE의 인자를 그대로 인용해 “인자를 call_target에 전달해 호출한다”로
쓴다. 이 구조 문장에서는 API/식별자의 이름을 해설하지 않는다.

- 잘못: `DBMS_OUTPUT.PUT_LINE('실패')`를 보고 “실패를 표준 출력에 기록한다”고 API명을 없앰
- 좋음: “`'실패'`를 `DBMS_OUTPUT.PUT_LINE`에 전달해 호출한다”처럼 call_target을 그대로 보존
- 잘못: `EXIT WHEN C_DATA%NOTFOUND`를 “커서의 %NOTFOUND 조건이면 종료한다”고 분리·번역
- 좋음: “`C_DATA%NOTFOUND` 조건이 참이면 현재 루프를 종료한다”처럼 ast_expression을 연속 보존

### 2. summary 작성 절차

`sufficient` 또는 `partial`일 때 다음 순서로 한 흐름을 쓴다.

1. 입력과 선행 검증, 조기 종료 조건
2. 결과를 바꾸는 주요 조건의 정확한 범위·비교 방향·극성
3. 조건별 조회·호출·변경과 그 시간 순서
4. 반환값·출력 구조·외부 상태 변화·오류/rollback/commit 경계

한 줄씩 번역하지 말고 사람이 읽는 문장으로 묶되, 결과가 달라지는 의미는 생략하지 않는다.
`부족`을 `없음`으로, `날짜`를 `일시`로, `암호화`를 `마스킹`으로 바꾸듯 범위를 좁히거나
넓히지 않는다. 상태코드의 업무 뜻은 근거가 말할 때만 쓰며, 아니면 원문 값을 보존한다.

현재 대상이 입력을 조립해 callee에 위임하고 결과를 전달한다면, callee 내부 책임을 복사하지
말고 현재 대상의 변환→위임→결과 전달 흐름을 쓴다. 반대로 현재 SOURCE가 직접 수행하는 주요
CALL/RW/반환은 “다른 좌표에 따로 있다”는 이유로 summary에서 없애지 않는다.

### 3. 근거 사용 규칙

- 제공된 활성 SOURCE와 구조화 근거만 사용한다.
- 입력으로 입증되지 않은 정책·코드값 의미·기관·단위·runtime identity는 추측하지 않는다.
- 원문 값, 비교 방향, 대상, 순서를 보존한다. 주석과 활성 코드가 충돌하면 활성 코드를 따른다.
- 코드식 비교를 summary에 직접 쓸 때는 좌변 식별자·연산자·우변을 SOURCE 또는
  CONTROL_FLOW_INDEX에서 한 묶음으로 그대로 대조한다. 값이 만들어진 과정과 그 값을
  사용하는 조건은 별개다. 예를 들어 SOURCE가 `match_count >= 1`이고 `item_id`는 조회
  필터에만 쓰였다면 `item_id >= 1`로 바꾸지 않는다.
- `ELSE`가 없는 IF의 조건이 거짓이면 그 IF 본문을 건너뛰고 다음 문장으로 정상 진행한다.
  이를 EXCEPTION/CATCH, rollback, 오류 반환 경로와 합치지 않는다. EXCEPTION/CATCH는 SOURCE가
  보여 주는 실행 중 오류 경로일 뿐 “앞선 조건을 만족하지 않음”의 대체 경로가 아니다.
  예를 들어 `IF ready THEN save(); END IF;` 뒤에 예외 처리기가 있어도 `ready`가 거짓이면
  저장을 건너뛸 뿐 예외 처리기로 이동한다고 쓰지 않는다.
- 서로 다른 원인과 결과를 `A 또는 B이면 X 또는 Y`처럼 한 문장에 교차 결합하지 않는다.
  `A이면 X`, `B이면 Y`로 경로를 각각 연결한다. 중첩 BEGIN/try의 오류가 그 안의
  EXCEPTION/CATCH에서 처리되면 명시적 재전파가 없는 한 바깥 예외 처리도 함께 실행된다고 쓰지
  않는다. 각 오류 경로는 SOURCE가 보여 주는 가장 가까운 처리 범위에만 연결한다.
- 문자 탭·개행·캐리지 리턴·널 종료처럼 JSON에서 제어 문자로 해석될 수 있는 코드 리터럴은
  summary에 escape 철자를 복사하지 말고 각각의 문자 종류를 자연어로만 적는다. 이 문자명
  뒤에는 괄호·따옴표·코드 리터럴을 덧붙이지 않는다. 판정 대상과 비교 극성은 그대로 보존하며,
  다른 상태값·식별자·테이블명까지 번역하는 규칙은 아니다.
  - 잘못: `탭('')`, `개행(' ')`, `널 종료('')`처럼 제어 문자를 빈 문자열이나 공백으로 보이게 씀
  - 좋음: `스페이스, 탭, 캐리지 리턴, 개행을 제외한 문자`처럼 문자명만 정확히 씀
- 현재 대상과 형제/callee의 소유권을 분리한다. 같은 사실을 표현만 바꿔 중복하지 않는다.
- CHILD_SUMMARY_CANDIDATE의 조건 표현을 그대로 복사하지 않는다. 각 조건의 `=`, `<>`, `IN`,
  `NOT IN`, `AND`, `OR`와 극성을 `CONTROL_FLOW_INDEX`에 다시 대조한 뒤 자연어로 쓴다.
