# Feature Specification: Framework·DBMS 동적 의미 프레임

현재 상태·폐기 결정·다음 행동의 단일 진입점은 `HANDOFF.md`다.

**Status**: In Progress — 2026-08-18 actual APAS ownership/performance revalidation
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
4. frame은 실제 구조 의무에 따라 동적으로 선택된 shallow/bounded family다. 하나의 자유 summary와
   거대한 universal recursive schema를 모두 금지한다.
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
12. 의미 owner당 summary slot은 target-owned responsibility/input/result 또는 target direct action만
    갖는다. evidence occurrence 수에 비례해 summary slot 수가 늘어나면 producer invariant 실패다.
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
- 봉인 의미 목표 약 90% 이상, fabrication/contradiction 각각 0건을 별도 보고한다.
- 새 격리 DB/output root의 Framework·DBMS 최소 actual 실행 뒤에만 새 frame 정확도를 주장한다.
- Analyzer 합격 뒤 Architect Simplified/Detailed 실제 경로에서 GWT와 evidence refs를 원문에 직접 대조한다.

## 보존 사실

기존 봉인40의 `39/40(97.5%)`, fabrication 0, contradiction 0과 이전 Architect 21/21은 과거 계약의
기준선이다. 새 semantic-frame 구현의 검증 결과가 아니다.
