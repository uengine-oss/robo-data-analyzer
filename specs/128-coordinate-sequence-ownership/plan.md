# Implementation Plan

1. BranchInventory가 routine/branch의 직접 효과를 child control 기준 선형 구간으로 분할한다.
2. 첫 구간은 기존 entry/branch가 소유하고 이후 구간은 continuation 좌표가 소유한다.
3. 공통 condition materializer에 `after_control` 좌표 leaf를 추가한다.
4. prompt·grader·branch table·batch·회계 소비자를 같은 계약으로 갱신한다.
5. targeted/전체 회귀와 preflight 뒤 새 AST·격리 DB 재분석 및 3층 검증을 수행한다.

## Constitution Check

- I: 좌표·순차 구간은 LLM이 아니라 AST 구조로 결정한다.
- III: condition materializer와 effect owner는 각각 단일 정의처만 둔다.
- IV: 증명 불가능한 jump 도달성은 finding으로 표면화한다.
- IX/X: 첫 오좌표에서 source→AST→assembly→actual prompt 순으로 검증한다.

