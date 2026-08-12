# Feature Specification: 좌표 기반 순차 효과 소유권

**Status**: In progress  
**Created**: 2026-08-11

## 목적

B(RULE·좌표·CALL/RW·TABLE·sample)를 production 기준으로 유지하면서, 한 entry/parent
행에 서로 다른 실행 시점의 효과가 합쳐지는 결함을 범용으로 제거한다. A(Python 관계식)는
같은 좌표 evidence를 소비하는 실험 자산으로 KEEP하며 production truth 경로로 승격하지 않는다.

## 계약

1. `entry`는 첫 root control 전의 선형 효과 구간만 소유한다.
2. control 뒤의 선형 효과는 첫 효과 좌표에 별도 `continuation` 의무 행을 만든다.
3. parent control 행은 첫 child control 전 효과만 소유한다. child 뒤 효과는 별도
   `continuation` 행이 소유한다.
4. continuation 조건은 업무 조건을 추측하지 않고 `after_control("TYPE@line")` 좌표 사실과
   이미 확정된 외곽 조건만 사용한다.
5. 비구조 jump 때문에 도달을 증명할 수 없으면 trusted Rule로 승격하지 않고 좌표 있는
   quality finding으로 격리한다.
6. 한 효과는 entry/decision/continuation 중 정확히 한 행만 소유한다.
7. 수정 후 새 parser AST를 만들고 격리 DB에서 재분석한다. 기존 기준 DB와 dirty WIP는 보존한다.

## 합격선

- `R803/R352/R996/R191`의 뒤쪽 효과가 entry에서 제거되고 continuation 좌표로 분리된다.
- `R143/R761`의 child 뒤 효과가 parent 행에서 제거되고 continuation 좌표가 소유한다.
- 의무 회계 잔차 0, 효과 중복 소유 0, 창작 좌표 0.
- 전체 회귀·prompt byte identity·재분석·결정론 gold·봉인 의미 표본을 다시 통과한다.

