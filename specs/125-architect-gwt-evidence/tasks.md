# tasks 125

> Analyzer 의미 정확도는 `../131-cross-node-semantic-grounding/HANDOFF.md`의 2026-08-12
> 종료 판정으로 닫혔다. 아래 T001~T010은 완료된 역사 증거이며, 이제 T011이 다음 별도 작업이다.

- [x] T001 shopmall 구조/gold, RULE finding 64행, MCP 3질의 크기 기준선 측정
- [x] T002 NL-only RULE wire와 결정론 cond/then 물질화 테스트·구현
- [x] T003 복합대입 operator 보존 테스트·구현
- [x] T004 TABLE sample snapshot 저장·부재 계약 테스트·구현
- [x] T005 MCP RULE 현행 계약 및 `node_detail(view="gwt")` 테스트·구현
- [x] T006 analyzer 단위/회귀/gold/직접 의미/크기 검증
- [x] T007 Architect 실제 스킬 E2E 결과와 한계 기록
- [x] T008 GWT-view provenance 축약 손실과 Simplified plan 근거 누락을 fail-closed로 수리
- [x] T009 별도 20함수 gold로 A v3 3회·B 저장 actual prompt 의미 검증
- [x] T010 B 소비 입력의 교차 노드 의미축 40건 봉인 판정과 summary/table/column 충분성 계약 검증

T007의 표본 판정은 Architect spec055 `verification.md`와 canonical 11 §7.6에 기록했다.
세 함수 표본 결과이며 RULE 1,609개 전수 의미로 일반화하지 않는다.
20함수 후속 결과도 별도 표본이며 `specs/127-cross-strategy-evidence-validation`에 격리했다.
T010은 `specs/131-cross-node-semantic-grounding/evidence/semantic-review-40.md`가 소유한다.
Architect A와 기존 네 GWT 실행은 KEEP했으며 accept/apply나 Architect 재실행은 하지 않았다.

- [ ] T011 완료된 Analyzer spec131 결과를 사용해 동일 요구사항·세 함수·모델·skill·gold로 B 기준
  Simplified/Detailed를 다시 실행해 함수 누락, GWT 의미, RULE/CALL/RW/TABLE/COLUMN/sample/
  legacyRefs 보존, bytes/tokens/time을 직접 재판정한다. `accept/apply`는 계속 호출하지 않는다.
