# plan 121 — nested-control RULE 의미

1. 구조 red: 부모와 child가 분리돼도 부모 evidence에 중첩 제어 존재가 표시돼야 한다.
2. `BranchInventory`가 선택된 branch의 control child 존재 여부를 소유한다.
3. `rule_evidence`가 boolean만 직렬화한다. 자식 cond/then은 복사하지 않는다.
4. `rule_leaf_contract.md`가 direct-empty leaf와 nested controller를 구분한다.
5. 표적·전체 회귀와 최종 run 3층으로 검증한다.

헌법 I/III/VIII: AST 구조 boolean은 결정론이며 단일 evidence 경로를 쓴다.
헌법 IX/X: RULE 39 전수의 틀린 5좌표에서 출발했고 1층+실제 prompt+3층을 분리한다.

