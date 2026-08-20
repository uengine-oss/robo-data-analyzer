# Analyzer 작업 재개 관문

루트 `D:\work\robo\AGENTS.md`의 공통 memory 세 파일을 모두 읽은 뒤, Analyzer를 계획·검토·수정·실행하기 전에 다음 파일을 순서대로 전부 읽는다.

1. `specs/131-cross-node-semantic-grounding/README.md`
2. `.specify/memory/constitution.md`
3. `specs/131-cross-node-semantic-grounding/ABSOLUTE_RULES.md`
4. `specs/131-cross-node-semantic-grounding/CURRENT_STATUS.md`
5. `specs/131-cross-node-semantic-grounding/history/handovers/04-검증방법.md`

자동 컨텍스트 요약, 새 세션, 작업 재개, 목표 변경이 발생하면 위 순서를 다시 수행한다. 요약문은 길찾기용일 뿐 사실 정본이 아니다.

`specs/131-cross-node-semantic-grounding/context/DOCUMENT_MAP.md`는 기존 handoff·spec·evidence의 위치와 상태만 분류한다.
`ABSOLUTE_RULES.md`는 현재 사용자 지침을 반영한 작업 불변식이고, `CURRENT_STATUS.md`는 현재
HEAD·발견·다음 행동만 소유한다. 과거 수치·전략 A/B 상세·실험 이력은 기존 canonical handoff와
evidence가 소유하며 삭제하거나 현재 사실로 자동 승격하지 않는다.
