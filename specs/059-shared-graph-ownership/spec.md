# Feature Specification: Analyzer 공유 그래프 소유권·안전 초기화

**Created**: 2026-07-17  
**Status**: Implementing

## Requirements

- Analyzer가 쓰는 모든 노드에는 `graph_owner='analyzer'`를 단일 저장 경계에서 기록한다.
- run 시작 초기화는 Analyzer owner 노드와 명확한 legacy UPPER_SNAKE 분석 노드만 삭제한다.
- `DataSource` 및 Architect session/설계 노드는 어떤 분석 초기화에서도 삭제하지 않는다.
- TABLE처럼 공용 upsert 예외인 writer도 동일 owner 계약을 지킨다.
- 관계는 양 끝 owner node 삭제에 따라 정리하며 다른 owner 양 끝 관계는 보존한다.
- 혼합 owner fixture와 실제 재분석 before/after count로 격리를 증명한다.
- `CALLS` 자기연결은 무조건 삭제하지 않고 실제 caller/callee 식별자가 같을 때
  `recursive=true`로 명시한다. 일반 호출은 false이며 저장 그래프와 라이브 스트림이 동일해야 한다.
