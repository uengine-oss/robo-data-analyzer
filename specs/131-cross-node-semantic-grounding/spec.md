# Feature Specification: Framework·DBMS 동적 의미 프레임

현재 상태·폐기 결정·다음 행동의 단일 진입점은 `HANDOFF.md`다.

**Status**: Complete
**Created**: 2026-08-11
**Reframed**: 2026-08-12

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
- 병렬 처리, priority, round planning, batch scheduling, semaphore, concurrency 재설계
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
10. 공통 prefix byte identity와 기존 병렬 엔진을 보존한다. schema 폭주는 의미 삭제가 아니라
    flattening/frame-family partition/batch 조정으로 해결한다.

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
