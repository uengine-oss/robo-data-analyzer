# Feature Specification: Residual Submit Resilience

**Feature Branch**: `main`
**Created**: 2026-07-17
**Status**: Active
**Input**: 최종 리팩터 D-0 요구 — pinned Analyzer의 residual 복원력을 본진 구조에 맞게 재판정하고, 문자열 예외 판별 없이 근본 계약으로 이관한다.

## User Scenarios & Testing

### User Story 1 - 선택적 residual 보강 실패가 완료된 결정론 결과를 파괴하지 않는다 (Priority: P1)

결정론적 stage1 링킹이 끝난 뒤 residual Agent가 탐색은 수행했지만 최종 submit 도구를 호출하지 않은 경우, 운영자는 이미 확보한 엣지와 미해결 케이스를 잃지 않고 분석을 계속할 수 있어야 한다.

**Why this priority**: residual은 결정론 결과 뒤의 선택적 보강이다. 새 검증 패턴을 제출하지 못했다는 이유로 기존 결과까지 파괴하면 단계 책임과 실패 영향도가 뒤집힌다.

**Independent Test**: residual Agent 경계가 전용 submit 누락 예외를 내도록 만들고 루프를 실행해, 신규 엣지 0·기존 residual 전량 보존·경고 1건을 확인한다.

**Acceptance Scenarios**:

1. **Given** 결정론 링킹 뒤 미해결 케이스가 있고 residual Agent가 submit을 누락함, **When** residual 루프가 전용 누락 예외를 받음, **Then** 새 패턴 없이 종료하고 기존 미해결을 그대로 반환한다.
2. **Given** residual 실행 중 submit 누락이 아닌 `ValueError`가 발생함, **When** 루프가 오류를 받음, **Then** 오류를 숨기지 않고 그대로 전파한다.
3. **Given** Scan Agent 등 다른 구조화 Agent가 submit을 누락함, **When** 공용 Agent 경계가 종료됨, **Then** 전용 누락 예외로 fail-closed 한다.

### Edge Cases

- submit 도구가 호출됐지만 Pydantic 검증이 실패하면 submit 누락으로 오인하지 않고 원래 오류를 전파한다.
- Agent 실행이 취소되면 residual 보존 분기로 바꾸지 않고 `CancelledError`를 전파한다.
- submit 누락 뒤에도 residual 목록은 빈 성공으로 바뀌지 않으며 기존 순서와 내용을 보존한다.
- 오류 메시지 문구나 지역화가 바뀌어도 예외 타입 계약으로 동작한다.

## Requirements

### Functional Requirements

- **FR-001**: 공용 구조화 Agent 경계는 최종 submit 결과가 없을 때 일반 `ValueError`가 아닌 전용 예외 타입을 발생시켜야 한다.
- **FR-002**: 공용 구조화 Agent 경계는 submit 누락을 모든 호출자에게 정상 결과로 바꾸면 안 된다.
- **FR-003**: residual 루프만 전용 submit 누락 예외를 잡아 신규 패턴 0건으로 종료하고 기존 residual을 보존해야 한다.
- **FR-004**: residual 루프는 submit 누락을 구조화 경고로 기록해야 하며 조용한 빈 결과를 만들면 안 된다.
- **FR-005**: submit 누락 이외의 `ValueError`, 스키마 오류, 도구 오류, 취소는 기존처럼 전파해야 한다.
- **FR-006**: 오류 종류 판정에 예외 메시지 substring을 사용하면 안 된다.
- **FR-007**: 기존 spec 007의 검증된 패턴만 채택, 진전 집합 판정, 최종 unresolved 환원 계약은 바뀌면 안 된다.

## Success Criteria

### Measurable Outcomes

- **SC-001**: 공용 Agent 누락 계약, residual 보존, 다른 오류 전파의 반대 사례 테스트가 모두 통과한다.
- **SC-002**: submit 누락 시 입력 residual과 출력 residual이 정확히 같고 신규 엣지·패턴은 0이다.
- **SC-003**: 제품 코드에 `"submit_residual" in str(error)` 형태의 문자열 예외 판정이 0건이다.
- **SC-004**: 관련 국소 테스트와 Analyzer 전체 권위 unittest, prompt byte-identical, compileall, diff check가 통과한다.

## Assumptions

- residual Agent는 Framework stage1 뒤의 선택적 보강이며 결정론 결과가 이미 존재한다.
- Scan Agent의 submit 누락은 분석 입력 자체가 불완전하므로 계속 치명적이다.
- 공개 HTTP/MCP/Neo4j 계약은 변경하지 않는다.
