## Rule meaning contract

Analyzer가 `conditions`, `effects`, `rows`의 좌표·극성·소유권·직접 효과를 이미 확정했다.
모델은 구조를 다시 찾거나 Python을 작성하지 않고, response_format이 요구한 각 condition/effect
key에 의미 문자열 하나, 각 row key에 분류 문자열 하나를 정확히 한 번 채운다.

- condition/effect 값에는 해당 atom이 현재 TARGET의 업무 흐름에서 뜻하는 한 가지 의미만 쓴다.
  원문을 자연어로 한 줄씩 번역하거나 key·줄 번호·파일·근거·상태를 복창하지 않는다.
- condition 값은 표현식의 이름을 풀어쓰는 대신, 그 condition이 참이어서 연결된 row effects가
  실제로 실행되는 상태를 쓴다. 같은 row의 원문 주석과 effects를 함께 대조하며, 조건 설명이
  오류·성공·상태 변경 효과의 방향과 모순하면 그 설명을 쓰지 않는다. 입력만으로 모순 없는 의미를
  확정할 수 없으면 빈 문자열로 둔다.
- 의미를 입증할 수 없는 condition/effect 값은 빈 문자열로 둔다. 충분성 상태는 모델이 출력하지
  않고 Analyzer가 빈 값과 구조 사실로 판정한다.
- `business_rule`: 행 전체가 사용자의 업무 정책·판단·업무 상태 변화를 나타낸다.
- `implementation_detail`: 언어·라이브러리·프레임워크 사용법, 포인터 역참조 보호,
  로깅·정리처럼 업무 정책 자체가 아닌 구현 절차다. 업무 입력의 필수 여부나 허용값 검사는
  단순 방어 코드로 취급하지 않는다.
- `insufficient`: 현재 packet만으로 두 범주를 구별할 수 없다.

입력에 없는 업무 코드값 뜻, 이름 모양에서 추측한 정책, callee 내부 절차, 다른 row/effect의 의미를
만들지 않는다. 응답 schema가 배정한 세 map과 문자열 외의 필드를 출력하지 않는다. 행 판정은 이름
모양이나 특정 프로젝트 관례가 아니라 현재 입력의 의미로만 한다.
Rule과 Example의 Python 모양 및 제품 공개 여부는 Analyzer가 이 응답과 구조 사실을 한 번 결합해
결정한다.
