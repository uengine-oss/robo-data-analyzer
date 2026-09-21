# 현재 상태 - 2026-09-08

## 완료 결과

- Java import는 선택된 source의 CLASS/INTERFACE/ENUM/RECORD 모듈 closure에 결합한다. Java receiver 호출은 Parser가 제공한 필드·지역변수 타입과 callable parameter 구조를 exact import/같은 package 타입에 결합하며, expression receiver·상속·타입 변환이 필요한 overload는 추측하지 않고 명시적으로 미확정한다.
- 함수 의미 LLM은 Summary와 Rule만 호출한다. 사용되지 않던 Function Table Usage 호출·prompt·schema·임시 저장은 제거했다.
- 직접 Table Usage는 Parser·Analyzer가 확정한 `READS`·`WRITES`와 operation에서만 투영한다.
- Summary 입력은 줄 번호가 붙은 함수 전체 원문, 간결한 구문 트리, 원문 밖 최소 근거만 담는다. 저장 ID·해시·byte 위치·중복 사실은 보내지 않는다.
- Rule 입력은 조건·효과·행의 소유 구조와 정확한 관련 source만 담고, 완전한 행 단위로만 나눈다.
- Rule 분할 상한은 model/runtime 설정이 소유한다. Qwen 프로필은 128 effects이고 다른 배포는 인위적 상한이 없다.
- 바깥 `else`가 내부 `if`의 부정 조건을 상속하던 구조 오류를 일반 규칙으로 수정했다. complement branch는 Parser의 `complement_of`·`negated_arms`만 따른다.
- graph commit 뒤 receipt·성능 artifact·terminal manifest가 모두 봉인돼야 `complete`를 알린다. 이후 실패는 `published_unsealed`로 드러낸다.
- 정상 중단은 명시적인 `RunStopped`만 허용하며 동시에 발생한 실제 제품 오류를 숨기지 않는다.
- 실행 의존성과 검증 의존성을 분리해 exact lock으로 고정했다. Torch는 CPU build를 lock에서 직접 고정하고 seal에 Python·installer·lock hash를 포함한다.
- 의미 요청, 응답 검증, Rule evidence, graph publication을 각각 별도 책임으로 분리했다.

## 성능 판단

- 2026-09-07의 44,143 LOC 1회 실측은 20분 27.4초, LLM 934회, 입력 3,590,875, 출력 498,706 token이었다.
- 제거된 Function Table Usage는 그 실행에서 78회, 입력 41,084, 출력 2,626 token이었다. 현재 코드 기준 예상 요청 수는 856회지만 전체 GPU를 다시 실행하지 않았으므로 새 전체 시간·총 token은 실측값으로 부르지 않는다.
- 최대 Summary 입력 21,733 token은 대부분 44,944자 함수 원문이었다. 최대 Rule 입력 19,763 token은 127 effects와 그 동적 응답 schema가 주원인이었다.
- 전체 원문과 완전한 Rule 행은 정확도 근거이므로 더 줄이지 않았다. skip·truncate·retry·특정 함수 예외는 추가하지 않았다.
- 요청별 queue, first chunk, generation, validation, 입력 section 크기를 기록하므로 요청 총량과 특정 대형 요청 병목을 구분할 수 있다.

## 검증

- exact test lock을 새 임시 가상환경에 설치한 전체 비DB 회귀: `1315 passed, 4 skipped, 291 subtests passed`.
- 의미 계획 집중 회귀: `85 passed, 45 subtests passed`.
- 공용 Analyzer skill 감사: 누락·오래된 지침 0건, Claude 정본과 Codex Junction 공유 확인.
- 관련 서비스 검색: 제거된 Function Table Usage, source API, 검색 우회 노드·관계 소비자 0건.
- 봉인된 최종 GPU 결과의 Function·Rule·Table·Column 41개 표본을 원문·Parser 사실과 직접 비교했다.
- Neo4j 확인은 명시한 `playground`만 읽기 전용으로 수행했다. `test`는 사용하지 않았다.

## 보호 경계

- Architect와 goal-verify는 이번 변경 대상이 아니다.
- api-gateway, data-fabric, frontend의 사용자 WIP는 변경하지 않았다.
- 보호 파일 `scripts/recompute_strategy_a_notation.py`는 읽기·실행·stage하지 않았다.
