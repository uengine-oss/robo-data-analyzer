## Table usage meaning contract

Analyzer가 직접 테이블 접근을 `(table, access_mode)`별 `FT`로 확정했다. 모델은 구조를 다시
찾거나 고치지 않고 각 접근의 현재 코드상 업무 목적만 문자열 하나로 쓴다.

- `table`: 번역·교체하지 않는 물리 테이블 이름
- `access_mode`: 확정된 READ 또는 WRITE
- `occurrences`: 발생 line과, mode와 다를 때의 operation 및 실제 사용 column
- `table_description`: 이미 확정돼 있을 때만 있는 보조 의미
- 번호 붙은 원문: 조건·계산·결과 사용을 확인할 정확한 현재 분석 단위

## [DECISION]

테이블 이름·접근 방향·좌표·상태·근거를 복창하거나 다른 table·callee 효과를 넣지 않는다.
이름 모양이나 일반 지식만으로 목적을 추측하지 않는다. 입증할 수 없으면 빈 문자열이다.
response_format의 모든 `FT` key를 정확히 한 번 채우고 다른 key를 만들지 않는다.

잘못: “T 테이블을 READ한다.” 좋음: “승인 전 요청의 현재 상태를 확인한다.”

## [SELF CHECK]

각 값이 직접 사용 목적만 담고 모든 FT key가 한 번씩 있는지 확인한다.
