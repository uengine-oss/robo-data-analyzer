# Feature Specification: Framework·DBMS 동적 의미 프레임

문서 단일 진입점은 `specs/131-cross-node-semantic-grounding/README.md`다. 영구 규칙은 `ABSOLUTE_RULES.md`, 현재
상태·폐기 결정·다음 행동은 `CURRENT_STATUS.md`가 소유하며, `HANDOFF.md`는 상세 역사다.

**Status**: In Progress — 2026-08-19 APAS summary completeness actual revalidation
**Created**: 2026-08-11
**Reframed**: 2026-08-12
**Reopened**: 2026-08-18

## 2026-08-18 재개 요구

2026-08-12의 frame 전환은 보존하되, 실제 APAS 전체 재분석에서 Framework 내부 CALL occurrence를
독립 의미 slot으로 만든 소유권 오류와 고정 8-slot 분할의 성능 퇴행이 확인됐다. 다음 계약이 과거
`병렬 처리/priority/batch scheduling 비범위` 문구를 대체한다.

- Framework의 LLM 의미 소유자는 routine(`FUNCTION|PROCEDURE|METHOD|TRIGGER`)뿐이다. 내부
  CALL/CONTROL/STATEMENT/RETURN은 함수 summary와 Rule을 판단하는 구조 근거이며 독립 summary
  대상이 아니다.
- DBMS는 skip-transparent effective AST에 남은 각 CodeNode가 자기 summary 의미를 소유한다.
  summary는 유효 형제 batch, Rule은 routine 분기표에서 node range로 배분한 unit/gate batch,
  table insight는 direct R/W 소유 node batch를 사용한다.
- TABLE/COLUMN, RULE, EXAMPLE, MODULE/COMPONENT는 공통 graph 산출물이다. EXAMPLE은 accepted
  Rule의 cond/then에서 결정론으로 합성하며 LLM이 별도 생성하지 않는다.
- 고정된 작은 batch는 해법이 아니다. 의미가 닫히는 소유권 경계를 먼저 세우고, 실제 prompt/schema
  output과 server queue/cache 지표로 concern 통합·분리·다중 target·공정 scheduling을 비교한다.
- deadline/repetition의 recursive split은 정상 경로가 아니라 서로 다른 schema/evidence로 실패 slot을
  격리하는 최후 경로다. 동일 request content retry는 계속 0회다.
- 실제 APAS cold wall은 10분 이내를 우선 목표로 하되, 정확도·근거·의무 회계를 맞바꾸지 않는다.
  10분을 못 맞추면 실제 병목과 측정값을 남기며 임시 생략·특수 경로로 숫자를 맞추지 않는다.

## 목적

Analyzer의 모든 자연어 의미를 큰 자유문장이나 물리 사실 재생산이 아니라, 실제 AST 구조와
evidence에 따라 선택된 얕고 제한된 의미 slot에 생성·검증·저장한다. Framework와 DBMS는 같은
공통 semantic-frame 계약을 사용하고 전략별 parser adapter만 다르게 한다. Architect는 이 결과를
ID 기준으로 중복 제거해 소비하며 정상 생성 경로에서 레거시 원문을 다시 분석하지 않는다.

## 범위

- SUMMARY, RULE, EXAMPLE, CALL meaning, TABLE, COLUMN, MODULE, logical name, sufficiency/missing context
- source→AST→context/frame→actual prompt→raw/parsed→validator→storage→graph/API/MCP 전 사슬
- Analyzer→Architect read-only MCP projection과 Simplified/Detailed GWT evidence refs
- Framework/DBMS 공통 계약과 전략별 structural adapter

범위 밖:

- A Python 관계식 변경·재실험
- `accept/apply`
- 특정 corpus 정답의 하드코딩

## 요구 계약

1. 원문은 Analyzer 내부 의미 분석과 감사의 권위 근거다. Architect 정상 generation packet에는
   complete source나 새로 자른 excerpt를 넣지 않는다.
2. 결정론 코드는 node type, children, ownership, order, coordinates, condition, CALL/RW,
   TABLE/COLUMN identity, transaction, applicable slot만 만든다. 업무 의미는 만들지 않는다.
3. LLM은 preassigned slot의 자연어 의미와 `sufficient|partial|insufficient`, `missing_context`만 채운다.
4. frame은 실제 구조 의무에 따라 동적으로 선택된 shallow/finite family다. 하나의 자유 summary와
   거대한 universal recursive schema를 모두 금지한다. finite는 schema 형태와 실행 단위이지 의미
   문자열·사실 의무·결과 수 상한이 아니다.
5. 모든 의미 값은 slot type, target/evidence refs, status에 연결된다. 필수 slot 누락, 알 수 없는 ID,
   다른 owner 근거, 구조 사실 변조는 fail-closed 또는 quality finding이다.
6. 기존 summary/description/nl은 canonical frame의 표시·검색 projection일 뿐 별도 의미 생산자가 아니다.
7. prompt는 task/goal, output, input authority, relations, decision procedure, examples, self-check,
   variable input을 중복 없는 한 흐름으로 제공한다.
8. TABLE/COLUMN은 DDL/comment/code usage/sample로 입증된 역할만 설명한다. 이름 번역은 금지한다.
9. Analyzer MCP는 target/claims/callees/data objects를 ID로 한 번씩 제공하고 scenario는 evidence refs로
   연결한다. 부족한 Analyzer 의미를 Architect의 원문 재독해로 메우지 않는다.
10. 공통 prefix byte identity는 보존한다. 병렬 엔진은 실제 APAS 비교에서 정확도·처리량·공정성을
    함께 통과한 단일 경로만 남기며, schema 폭주는 의미 삭제가 아니라 flattening, 의미가 닫히는
    owner 경계, 입력·출력 비용 기반 batch/admission으로 해결한다.
11. Framework/DBMS의 의미 owner와 호출 batch는 위 재개 요구의 서로 다른 계약을 따른다. 공통
    semantic frame을 같은 호출 단위로 오해하지 않는다.
12. routine의 5개 target-owned flow role은 taxonomy이며 사실 개수 상한이 아니다. non-routine은
    direct action role을 갖는다. 적용 role은 실제 구조축으로 결정하고, 한 role이 큰 경우 의무별
    fragment로 나눠 exact-once merge한다. evidence occurrence를 의미 없는 중복 prose slot로 복제하는
    것과 서로 다른 필수 사실을 보존하는 것을 구분한다.
13. summary가 dependency critical이어도 Rule/table을 무기한 굶기지 않는 work-conserving scheduling을
    사용한다. 대기 중 다른 concern이 있는데 한 concern만 전체 wall 동안 실행되는 상태는 결함이다.
14. 성능 후보는 동일 source/prompt/schema hash의 cold/warm replay에서 calls, input/output/cache tokens,
    TTFT/E2E p95, wall, schema/repetition/deadline, 의미 판정을 함께 측정한다.
15. branch inventory의 모든 required row는 parser-owned condition/effects/type/timing/complement/
    nesting/fallthrough/handler/continuation을 가진 **결정론 Rule**로 물질화한다. Rule별 자연어
    `BUSINESS_MEANING`은 만들지 않는다. 실제 APAS 고정 replay에서 code-only row의 prose가 조건
    극성을 바꾸고 opaque identifier를 업무 용어로 창작했으며, 후단 소비자는 이 prose를 요구하지
    않고 exact RULE evidence와 condition/effects/Example을 사용한다. Rule의 `nl`은 빈 표시용
    호환 필드이고, 검색/화면이 문장을 필요로 하면 동일 cond/then을 해석 없이 구조적으로 렌더한다.
    EXAMPLE은 실제 outcome이 있을 때만 accepted Rule에서 결정론 합성한다.
16. provider response의 request-local private key가 meaning/missing_context에 나타나면 문자열 치환으로
    숨기지 않는다. 해당 slot만 신뢰 대상에서 격리하고 좌표 있는 finding으로 남기며, 다른 slot과
    결정론 Rule/Example은 보존한다.
17. Rule provider wire·assignment·row packet·Rule 전용 LLM batch는 production 경로에 존재하지
    않아야 한다. Rule 구조를 이미 가진 상태에서 같은 행을 prose로 다시 생성하는 두 번째 의미
    생산자와 그 schema/normalizer/retry/split 경로를 남기지 않는다.
18. Rule의 CALL/DATA_OBJECT/SYMBOL/EXTERNAL 연결은 canonical profile의 exact evidence refs로만
    보존한다. callee/table의 의미는 각 원래 semantic slot을 ID로 참조하며 Rule 문장에 복사하지 않는다.
19. library return/sentinel, opaque identifier, target 이름을 Rule 문장으로 번역하지 않는다. 의미가
    필요한 최종 소비자는 함수 summary와 callee/table/column claim을 RULE 구조 evidence와 함께 사용한다.
20. parser가 생산한 active named `GOTO`는 같은 routine의 active `LABEL` exactly one과 해소돼야 한다.
    누락·중복·빈 target은 stale/불완전 AST라는 결정론 계약 실패이며 module/summary 등 첫 GPU 호출 전에
    파일·routine·GOTO 절대행·target을 포함해 종료한다. Analyzer source 정규식으로 LABEL을 복원하지 않는다.
21. 결정론 Rule 물질화의 단일 검증 경계는 rejected/unavailable과 assigned-row 회계 잔차를 fatal로,
    `unverified` 의미 품질 finding을 좌표별 격리로 분류한다. unverified row는 trusted Rule/Example에
    들어가지 않지만 다른 row와 다른 node 분석은 계속하며 DBMS 적용 단계에서 같은 Rule을 재채점하지 않는다.
22. Rule 영구 감사는 routine 전체 obligation뿐 아니라 각 row의 실제 graph owner를 보존한다. 상설
    `judge_run.py`는 `obligation = trusted + rejected/unverified + unavailable`, 중복·잔차 0을 전수 판정하고,
    graph 구조 기대값에서는 finding row를 제외한다. routine owner와 DBMS child owner를 혼동하지 않는다.
23. EXAMPLE은 accepted Rule의 결정론 `cond/then`에서만 합성한다. 모델 출력 폭주를 막기 위해 존재했던
    leaf 길이 상한을 parser-owned Rule truth에 적용해 조건을 자르거나 예시를 조용히 누락하지 않는다.
    긴 opaque source predicate는 Analyzer가 언어별 연산자로 재파싱하지 않고 Rule과 동일한 leaf를 무손실
    보존한다. 조건 또는 직접 결과가 없는 Rule만 EXAMPLE 비생성의 정상 사유다.
24. routine summary는 `RESPONSIBILITY/INPUT_ROLE/RESULT` 세 칸에 전체 흐름을 압축하지 않는다.
    live source와 결정론 inventory에 실제로 존재하는 축만 다섯 역할 taxonomy
    (`RESPONSIBILITY`, `INPUT_ROLE`, `GUARDS_AND_BRANCHES`, `PROCESSING_EFFECTS`, `RESULT`)으로
    미리 배정한다. 각 칸은 서로 다른 질문에 답하며 legacy summary는 이 순서의 frame projection이다.
25. 선언됐지만 사용되지 않는 파라미터, write-only/output-only 파라미터, 즉시 상수 반환 no-op routine은
    현재 source만으로 완전히 확정되는 동작이다. 이를 의도된 업무 로직이나 호출자 문맥이 없다는 이유로
    `partial|insufficient`로 낮추지 않는다. 반대로 이름만 보고 입력 역할·검사 목적을 만들지 않는다.
    parser는 grammar fact를 계속 소유하며 alias/data-flow 의미를 불완전한 새 AST 속성으로 추측하지 않는다.
26. 원문 source bytes/hash는 불변 권위다. 장문 입력은 AST owner와 의미 obligation으로 분해하되 exact
    span의 합집합·순서·dependency closure가 원문 의무와 같아야 하고 누락·중복·미분류가 0이어야 한다.
    child/기존 summary를 원문 대신 다음 LLM의 권위 입력으로 사용하는 summary-of-summary를 금지한다.
27. context 한계를 넘는 경우 내용 절단·Top-N·oversize skip·LLM 요약 대체로 성공시키지 않는다.
    obligation별 exact source/evidence 요청으로 분할하고 canonical ID로 병합한다. 경계 의미가 닫히지
    않으면 필요한 좌표를 남긴 `partial|needs_context`이며 `sufficient`가 아니다.
28. C type-name syntax authority는 translation-unit별 configured preprocessing과 ANTLR grammar의 declaration
    point/lexical scope/order다. Parser가 project source regex, suffix, POSIX/Windows/header 이름 목록으로
    semantic predicate를 선결정하지 않는다. 미해소 lookup은 exact source coordinate와 grammar decision을
    common evidence에 남기고 Analyzer가 graph mutation 전에 strict validation한다. syntax-dependent AST/owner가
    닫히지 않으면 해당 분석 단위를 좌표가 있는 run finding으로 격리하거나 fail-closed한다. ANTLR
    grammar/listener는 유지하며, external compiler frontend는 configuration-bound trace
    provider일 뿐 모든 toolchain의 무조건적 대체 truth가 아니다.
    같은 token의 반복 predicate 평가는 한 fact로 합치되, recovery 중 서로 다른 token에 실제로 수행된
    decision은 source coordinate별로 보존하고 partial parse/symbol completeness 밖의 canonical meaning으로
    승격하지 않는다.
    grammar 대안 비교는 현재 candidate identifier 한 좌표만 type-name으로 시험하며 뒤 source의 다른 unknown
    identifier를 같은 결정으로 오염시키지 않는다. 두 대안이 모두 성립하면 어느 쪽도 exact truth로 만들지 않는다.
    부분 업로드에서 grammar role이 확정된 사용의 definition 부재는 제품 external 경계다. typedef-sensitive
    ambiguity는 target 분류가 아니라 Parser AST JSON 진단이다. 이 symbol 진단을 Neo4j node/label/relationship,
    기존 owner property ledger, API/MCP 객체로 만들지 않는다. LLM은 AST/type/linkage/CALL/REFERENCE를
    생성·변경하지 않고 별도 natural-language semantic frame만 채운다.
29. C `external linkage`와 Analyzer external target을 구분한다. call-site에 보이는 compatible declaration과
    corpus 안의 unique external definition이 결합되면 파일이 달라도 내부 사용자 정의 CALL이다. header는
    declaration 전달 수단일 뿐 필수가 아니다. static/internal linkage, typedef(no linkage), incompatible/
    duplicate definition, declaration 부재는 이름 일치만으로 결합하지 않는다.
30. 모든 언어의 call binding은 Parser language adapter가 versioned callable declaration/definition fact와
    call-site binding 판정으로 생산한다. Analyzer는 adapter-owned opaque binding/type/configuration key의
    exact join만 수행하고 언어, 확장자, 대문자, stdlib 이름 목록, framework glob, 같은 파일 우선, 정렬상
    첫 ID로 target을 만들거나 제외하지 않는다. 증명된 exact definition 하나만 internal CALLS이며 target이
    없으면 product external, 동적/모호/미지원은 call occurrence 진단이다. 어떤 상태도 별도 Unresolved
    graph entity나 owner property ledger가 아니며, collision-free external identity가 없으면 aggregate
    external target node도 만들지 않는다.
    같은 typedef/tag 철자는 translation unit 경계를 넘는 type compatibility 증거가 아니므로 adapter는
    compatibility proof scope를 source/configuration/corpus로 명시하고 Analyzer는 그 scope까지 exact join한다.
    C non-static inline처럼 dialect·declaration·attribute에 따라 definition eligibility가 달라지면 configured
    trace 없이 exact target으로 승격하지 않는다. token spelling equality를 canonical C type compatibility로
    가장하지 않으며, grammar AST로 완전히 정규화한 지원 subset만 exact이고 나머지는 unavailable이다.
    call-site에 직접 보이는 exact definition은 별도 compatibility 검색 없이 그 definition fact를 가리키고,
    declaration-only binding만 exact compatibility로 definition을 찾는다. inactive callable은 visibility 후보에서
    제외하며 conditional/unknown callable 영향은 configuration-dependent occurrence로 전수 보존한다.
    visible declaration은 있으나 type/dispatch compatibility를 증명하지 못한 `unsupported` 판정도 원인이 된
    callable fact ID를 버리지 않고 occurrence의 candidate evidence로 보존한다.

## 폐기된 요구

`gwt-evidence/v2` complete source/hash를 Architect 기본 packet에 넣고 source를 최상위 authority로
검증하게 하는 요구는 폐기한다. evidence ID, 구조·좌표, RULE condition/effects, symbol/callee/data
object evidence와 scenario refs는 semantic-frame에 맞게 재사용할 수 있다.

## 합격 기준

- 모든 자연어 생산자/소비자 inventory와 소유권 표가 완성된다.
- Framework/DBMS 대표 모든 structural role에서 적용 slot과 evidence 관계가 결정론적으로 동일하다.
- actual prompt만 읽어 task, input meaning/authority, allowed judgment, relation, output slot을 설명할 수 있다.
- 필수 의미 의무 누락, 구조 사실 변조, unknown/cross-owner evidence, 근거 충분 blank, 근거 부족 창작이
  validator와 population inventory에 드러난다.
- prompt/schema bytes, input/output tokens, 반복/실패, latency, 가능한 cache 관측을 전후 비교한다.
- 전체 자동 회귀, 결정론 구조 전수 대조, 봉인 표본 직접 의미 판정의 3층을 통과한다.
- 봉인 의미의 이상적 목표는 100%다. exact/partial/omission/needs_context 실측을 그대로 공개하며
  90%나 그보다 낮은 결과를 숨기지 않는다. 어떤 수치도 나머지 의무를 버려도 되는 허용 손실률로
  쓰지 않고 fabrication/contradiction은 각각 별도 보고한다.
- 재사용 DB `framework|dbms`와 이 spec의 `_runs/<strategy>`를 사용한 Framework·DBMS 최소 actual
  실행 뒤에만 새 frame 정확도를 주장한다. 실행 전 해당 전략 DB를 비우되 `neo4j|test`와 다른 전략
  DB는 건드리지 않는다.
- Architect 검증은 현재 보류한다. 사용자가 범위를 다시 열면 Analyzer 합격 뒤 Simplified/Detailed
  실제 경로에서 GWT와 evidence refs를 원문에 직접 대조한다.

## 보존 사실

기존 봉인40의 `39/40(97.5%)`, fabrication 0, contradiction 0과 이전 Architect 21/21은 과거 계약의
기준선이다. 새 semantic-frame 구현의 검증 결과가 아니다.
