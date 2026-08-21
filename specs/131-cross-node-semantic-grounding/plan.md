# Implementation Plan: Dynamic Semantic Frame

현재 상태는 `CURRENT_STATUS.md`, 영구 규칙은 `ABSOLUTE_RULES.md`, 요구 계약은 `spec.md`, 실행
체크포인트는 `tasks.md`가 소유한다. 전체 진입점은 `specs/131-cross-node-semantic-grounding/README.md`다.

> **2026-08-20 실행 재개:** 사용자가 전체 목표와 방향을 승인해 Analyzer 구현·검증을 진행한다.
> Architect 단계만 사용자가 다시 열기 전까지 보류한다.

1. 모든 Framework/DBMS 자연어 의미 생산자, 현재 response schema, normalizer, validator, 저장 필드,
   graph/API/MCP projection, Architect 소비자를 한 번 전수 inventory해 재사용 가능한 단일 영향표로 만든다.
2. 전략별 AST를 공통 structural role과 stable evidence ID로 바꾸는 `StructuralProfile`을 설계한다.
3. 실제 target type과 존재하는 children/evidence만 보고 shallow/bounded slot을 만드는 frame planner와
   canonical `semantic-frame/v1` wire를 고정한다.
4. prompt를 stable instruction prefix + dynamic structural/frame suffix로 구성하고, LLM은 의미/status/
   missing context만 채우게 한다.
5. SUMMARY, RULE, EXAMPLE, CALL meaning, TABLE/COLUMN, MODULE/logical name 생산자를 frame-first로
   전환하고 기존 문자열은 projection으로만 만든다.
6. schema/normalizer/grounding/completeness validator와 population inventory를 전환한다.
7. graph/storage/API/MCP를 frame 정본과 deduplicated Architect projection으로 전환한다.
8. 폐기된 complete-source GWT WIP를 제거·적응하고 Architect Simplified/Detailed 소비자를 같은 계약으로
   동기화한다.
9. Framework/DBMS 반대 fixture와 schema 크기·출력량·반복·prefix는 변경 묶음별 한 번 검증하고,
   전체 생산자·소비자 정합 후 전체 자동 회귀를 한 번 수행한다.
10. preflight 뒤 재사용 DB `framework|dbms`와 이 spec의 `_runs/<strategy>`에서 최소 actual Analyzer 실행과 봉인
    직접 판정을 수행한다. 검증마다 새 DB/output 이름을 만들지 않는다.
11. **보류:** 사용자가 Architect 범위를 다시 연 뒤에만 실제 두 경로를 최소 실행해 GWT/evidence
    refs를 원문과 대조한다.
12. HANDOFF·작업보고를 결과로 교체하고 불필요한 임시 실행물만 정리한 뒤 요청 범위만 commit/push한다.

## Constitution Check

- I/VI: 구조와 applicable slot은 결정론, 업무 의미는 LLM이며 frame은 shallow/bounded다.
- III: semantic frame이 의미 단일 진실이고 legacy 문자열은 projection이다.
- IV: 누락/grounding 실패는 finding으로 보존하고 조용한 빈 성공을 금지한다.
- V/VII: byte-identical prefix와 검증된 structured channel을 유지한다.
- IX/X: 3층 검증과 source부터 시작하는 원인 추적을 수행한다.
- II/Cost: Framework/DBMS strategy 선언은 보존하되 각 전략의 실제 AST owner에 맞는 analyzer를 쓴다.
- I/VI: occurrence 수를 prose slot 수로 복제하지 않고 구조 사실은 evidence로 보존한다.
- IV/IX: starvation, deadline singleton, partial finding과 미검증 성능축을 조용히 숨기지 않는다.

2026-08-12의 12단계 결과는 `evidence/actual-validation-20260812.md`가 소유한다. 2026-08-18
재개 작업은 아래 순서로 수행하며 그 과거 결과를 새 APAS 전체 검증으로 일반화하지 않는다.

13. 현재 source→AST→profile/slot→summary/Rule/table→Example→graph/API/MCP→Architect 배선을
    FW/DBMS별 owner와 호출 단위까지 파일:행으로 다시 고정한다.
14. 잘못된 Framework occurrence-owned CALL slot과 고정 8-slot microbatch를 제거하고, owner당
    finite taxonomy와 obligation-derived 무손실 분할 불변식을 producer 계약 테스트로 고정한다.
15. Rule의 parser-owned role/outcome/action prose와 `RULE.BUSINESS_MEANING`을 production에서 제거한다.
    모든 required row를 LLM 0의 condition/effects Rule로 물질화하고, graph/MCP/Architect에는 모든
    row의 condition/effects/Example/좌표와 연결 semantic claims를 손실 없이 유지한다.
15a. 다행 Rule prompt의 별도 slot-assignment↔row 조인을 제거하고 각 row가 자기 output wire를 직접
     소유하게 한다. 모델 packet은 semantic row의 focus condition/direct/source만 담고 full inherited
     path와 nested child 구조는 결정론 정본에만 둔다. singleton 증설 없이 같은 batch에서 exact
     row/wire 정렬을 강제한다.
15b. Rule target 전체에 붙던 symbol/callee/RW/external/API 목록을 제거하고, direct CALL·DATA_OBJECT
     좌표와 exact matched key로 연결된 보조 근거만 해당 row에 결속한다. 의미 근거 없는 identifier와
     sentinel 비교는 확장 번역하지 않는 provider 계약과 반대 fixture를 고정한다.
15c. 15a~15b의 최종 고정 replay에서도 조건 극성 모순과 무근거 이름 확장이 남았고 후단 validator가
     이를 잡지 못함을 source→actual response→grade 사슬로 확정한다. Rule prose의 소비자 0을 감사한 뒤
     Rule 전용 provider/schema/normalizer/batch를 제거하고 exact cond/then/Example projection 하나로
     정리한다. 검색 표시가 필요하면 같은 구조 facts를 해석 없이 렌더한다.
16. 현재 concern 분리, 함수 concern 통합, 다중 target, work-conserving fair scheduling 후보를 실제
    APAS 봉인 slice에서 비교한다. 과거 반증된 고정 5/6행, input-length-only split, 의미 축소 prompt는
    재실험하지 않는다.
17. 채택 구조만 남기고 옛 경로·실험 flag 없이 producer/scheduler/validator를 한 경로로 정리한다.
18. 전체 자동 회귀와 prompt hash, 실제 APAS cold 전체 run, 3층 검증, Stage 8 delta를 수행한다.
19. **보류:** 사용자가 Architect 범위를 다시 열고 Analyzer가 합격한 뒤에만 작은 실제 MCP 질의를
    Architect Opus 5 high/no-Fable 경로에 전달해 legacy 있음/없음 GWT를 검증한다.
20. APAS cold 실패의 첫 좌표에서 source GOTO→저장 AST→branch inventory→grader를 역추적하고,
    모든 routine의 active named jump target exact 해소를 module pre-analysis 전 공통 계약으로 승격한다.
21. 공통 Rule 물질화기가 fatal deterministic defect와 quarantined unverified finding을 한 번만 분류하게
    하고, Framework/DBMS 적용 경계는 그 결과를 재채점 없이 소비하며 exact row accounting만 확인한다.
22. deterministic Rule audit에 routine row→graph owner를 기록하고 `judge_run.py`가 finding 좌표를
    빼서 trusted graph를 대조하도록 고친 뒤, parser 회귀→APAS 23 AST 재생성→old/new vocabulary·좌표·
    jump 해소 delta→preflight 순으로 GPU 이전 게이트를 닫는다.
23. final APAS summary finding 4건과 봉인 FUNCTION 10건을 source→actual slot→response→finding으로
    다시 판정한다. raw parameter 문자열을 지우거나 Parser에 불완전한 alias/data-flow 추론을 넣지 않고,
    target-owned summary를 5개 역할 taxonomy로 분류해 no-op/unused/output-only와 주요
    guard/call/state/result 흐름의 적용 가능성을 결정론으로 고정한다. 역할 안의 사실 개수는 자르지 않는다.
24. 새 역할의 population·projection 순서·taxonomy 최대치·사실 obligation 잔차·빈/unused/분기/호출/
    반환 반대 fixture를 먼저 통과시킨
    뒤 전체 회귀와 prompt byte 원장을 실행한다. 실제 APAS full cold에서 같은 봉인 FUNCTION 10을
    재판정해 omission/contradiction이 남으면 prompt 문구 반복 대신 첫 오좌표의 입력·slot 경계를 다시 연다.
25. C-067~C-072의 call target 배선을 source→Parser callable/call binding→Analyzer join→CALLS/occurrence→
    semantic context/order→Neo4j→REST/MCP/search로 고정한다. Java JLS overload/dispatch, Python lexical/
    runtime binding, C linkage/type compatibility를 각 공식 명세와 현재 listener capability에 대조하되,
    특정 compiler/frontend를 모든 configuration의 truth로 승격하지 않는다.
26. Common Evidence IR 1.3에 `callable` fact와 call별 binding 판정, 별도 `call_binding` completeness를
    추가한다. C adapter부터 visible declaration/linkage/type/configuration을 생산하고 Java/Python은 같은
    wire에서 증명된 capability와 `unsupported|dynamic` 경계를 명시한다. Analyzer name-only fallback은 없다.
    type compatibility key는 proof scope까지 포함하며 같은 typedef/tag 철자만으로 translation unit을 넘지
    않는다. non-static inline definition eligibility처럼 dialect/configuration이 필요한 축은 exact target이
    아니라 callable/call-binding completeness의 명시적 partial로 남긴다. token sequence는 canonical type이
    아니므로 grammar AST로 완전히 정규화 가능한 builtin subset만 exact로 봉인하고 named/unspecified/complex
    type은 unavailable로 둔다. visible exact definition은 fact-direct, declaration-only는 compatibility join으로
    분리하며 inactive/conditional callable presence를 binding 후보와 completeness에 반영한다. exact
    compatibility를 지원하지 못해 `unsupported`가 된 visible declaration도 판정 근거 fact ID를 occurrence에서
    잃지 않도록 evidence closure를 닫는다.
27. Parser red는 direct declaration+cross-TU definition, same-TU static, declaration 부재, incompatible/
    duplicate/configuration 분리, supplied-header definition, macro/conditional 경계를 고정한다. Analyzer red는
    opaque-key exact join, target 0/1/N, 언어·이름 독립성, external occurrence와 unresolved graph entity 0,
    stdlib/framework name-only exclusion 0을 고정한다.
28. Parser producer를 전체/actual 전수 회계와 봉인 직접 판정 후 commit/push하고, Analyzer strict consumer와
    occurrence/generation/graph/API/MCP를 별도 검증 슬라이스로 commit/push한다. 마지막에 framework rollback
    publish와 자동 회귀·결정론 전수 회계·봉인 직접 의미 판정 3층을 수행한다.
