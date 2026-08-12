# Implementation Plan

현재 상세 계약과 종료 상태는 `HANDOFF.md`가 소유한다. 아래 구현·검증 단계는 모두 완료됐다.

1. SUMMARY·RULE·EXAMPLE·TABLE·COLUMN의 필수/선택/노이즈 입력과 actual prompt,
   validator, graph/API/MCP 소비자를 inventory한다.
2. empty/partial/insufficient/missing_context/finding 모집단을 전수 회계하고 봉인40을
   source→AST→actual prompt→raw/parsed→validator→graph로 join한다.
3. 배치 상한과 source 의미 상한을 분리하고 full node ID를 의미 identity로 고정한다.
4. 결정론 구조 사실과 GPU 업무 의미를 분리하며 자연어 의미 이중 생산자를 제거한다.
5. SUMMARY effective evidence, RULE 구조·direct effect, TABLE direct usage, COLUMN별 usage를
   concern별 projection으로 전달한다.
6. Task/Input authority/relation/Decision/Output/Examples/Self Check 순서로 prompt를 재구성하고
   실제 저장 packet과 byte hash로 검증한다.
7. 공통 schema/normalizer/validator/storage/consumer 결함을 범용 수정하고 반대 fixture로 반증한다.
8. preflight 뒤 Framework·DBMS를 새 격리 DB와 별도 output root에 최소 재분석한다.
9. 동일 봉인40을 직접 재판정하고 전체 회귀, prompt byte, graph/API/MCP 전달을 검증한다.
10. 현재 정본·tasks·evidence를 결과로 교체하고 Analyzer를 닫는다.

## Constitution Check

- I: 구조 사실은 결정론, 업무 의미만 GPU가 소유한다.
- IV: silent blank와 정상 needs_context를 구별하고 좌표·raw evidence를 보존한다.
- V/VI/VII: byte-identical prefix, 최소 schema, proven structured channel을 유지한다.
- IX/X: source→actual prompt→checker→model 순서와 3층 검증을 끝까지 집행한다.
- Cost & Safety: 사용자 WIP·기본 DB·보존 logs를 건드리지 않고 새 격리 DB/output root만 쓴다.

## 완료 결과

- 봉인40: `39/40=97.5%`, fabrication 0, contradiction 0.
- 전체 회귀: `1062 passed, 5 skipped, 388 subtests`.
- prompt hash: `28/28 OK`.
- Framework/DBMS finding 0, 구조 preflight diff 0.
- TABLE/COLUMN semantic description과 evidence status가 graph→API/MCP까지 실제 전달됨.

다음 단계는 이 plan의 연장이 아니라 spec125/Architect spec055의 별도 검증이다.
