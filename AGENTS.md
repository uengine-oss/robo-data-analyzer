# Analyzer 작업 재개 관문

루트 `D:\work\robo\AGENTS.md`가 지정한 공통 memory 세 파일을 읽은 뒤, Analyzer 작업은 다음
현재 정본만 순서대로 읽는다.

1. `specs/001-analyzer-redesign/README.md`
2. `.specify/memory/constitution.md`
3. `specs/001-analyzer-redesign/spec.md`
4. `specs/001-analyzer-redesign/CURRENT_STATUS.md`

`plan.md`, `tasks.md`, `contracts/`는 현재 작업 좌표에 필요한 부분만 읽는다. 과거 작업 문서는
현재 계약을 대신하지 않는다. `_runs/`는 마지막 봉인 검증에 필요한 로컬 실행 자료이며 spec이 아니다.

Architect는 1차 작업 범위 밖이며, Analyzer와 다른 관련 서비스 동기화가 완료된 뒤 별도 2차 작업으로
처리한다. goal-verify는 이 작업 범위 밖이다.
