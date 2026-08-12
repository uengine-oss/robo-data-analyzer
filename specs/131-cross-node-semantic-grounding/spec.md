# Feature Specification: Framework·DBMS 교차 노드 의미 근거 충분성

현재 작업 규칙·검증 사실·미해결·다음 행동의 단일 진입점은 `HANDOFF.md`다. 이 파일은 요구 계약을
소유하고, `HANDOFF.md`는 누적 없이 현재 상태만 교체 갱신한다.

**Status**: In Progress  
**Created**: 2026-08-11

## 목적

B(좌표·근거 전달) 경로에서 Framework와 DBMS의 FUNCTION summary, RULE NL, EXAMPLE,
TABLE, COLUMN 의미가 원문과 실제 전달 근거에 충실한지 판정하고, 입력 부족·prompt 계약·모델·
schema/validator·저장 손실을 서로 분리한다. A는 KEEP하고 Architect GWT 재실행은 Analyzer
완료 뒤 후속으로 미룬다.

현재 최우선은 Analyzer 의미 생성 계약의 완결이다. GPU에 구조 사실을 다시 생산시키거나 업무
의미를 결정론 로직으로 대체하지 않고, 필요한 근거를 노이즈 없이 전달하면서 GPU 출력량과
자율성을 최소화하는 범용 경계를 찾는다. Architect는 Analyzer 완료 뒤 별도 후속으로 재개한다.

## 사용자 가치

레거시 분석 결과를 읽는 사람이 코드 식별자 나열이나 그럴듯한 창작이 아니라, 코드 전체 흐름과
실제 테이블·컬럼 역할을 사람이 이해하기 쉬운 자연어로 확인하고 원문까지 역추적할 수 있다.

## 범위

- Framework `shopmallseq`와 DBMS `rwisverify`의 보존 run/graph/source는 수정 전 기준선으로
  읽기 전용 사용한다. 범용 수리·preflight 뒤에는 별도 DB/output root로 새 재분석을 수행한다.
- FUNCTION(summary 포함)·RULE·EXAMPLE·TABLE·COLUMN 총 30~40개를 사전 봉인한다.
- 종류별 최대 20개, 두 corpus를 모두 포함한다. 모집단이 할당보다 작으면 해당 모집단을 전수한다.
- 실제 system/user/schema, raw/parsed response, graph 저장값, source/AST/context 생산자를 같은 ID로
  대조한다.
- A prompt/code/실험과 Architect Simplified/Detailed 재실행은 범위 밖 KEEP이다.

## 의미 계약

1. SUMMARY는 TARGET SOURCE 전체의 입력→검증→주요 조건별 처리→CALL/RW→반환/상태 변화 흐름을
   사람이 이해하기 쉽게 요약한다.
2. TABLE/COLUMN은 이름만 번역하지 않고 실제 전달된 DDL/comment/code usage/sample에서 확인되는
   역할을 설명한다.
3. RULE/EXAMPLE은 조건 범위·극성·대상·결과·시간 순서를 보존한다.
4. 문자열 완전 일치가 아니라 의미 동등성을 판정한다. 필수 의미 누락·모순·창작은 불합격이다.
5. 입력이 부족하면 `sufficient/partial/insufficient`와 `missing_context`로 드러내며 추측하지 않는다.
6. 결정론 코드는 원문 구조·full node 좌표·소유권·CALL/RW·transaction fact·전달·검증만
   생산한다. 정규식·식별자 이름·고정 번역문으로 SUMMARY/TABLE/COLUMN 업무 의미를 만들지 않는다.
7. EXAMPLE은 검증된 RULE의 non-empty cond/then에서만 생성한다. expected outcome이 빈 EXAMPLE을
   저장하지 않는다.
8. code-node column usage는 exact table/column identity, full node id, 절대 source 좌표, 원문 excerpt,
   access/operation과 함께 final COLUMN 한 경로에 도달해야 한다. multi-table의 미수식 column처럼
   owner를 확정할 수 없는 항목은 임의 귀속하지 않는다.
9. 모든 의미 prompt는 모델이 첫 화면에서 다음 다섯 질문에 답할 수 있는 단일 읽기 순서를 가진다.
   ① 무슨 결과를 왜 만드는가 ② 각 입력 필드는 무엇이며 어느 생산자가 소유하는가 ③ 그 필드로
   무엇을 판정할 수 있고 무엇은 판정하면 안 되는가 ④ 사실끼리 어떤 ID·좌표·관계로 연결되는가
   ⑤ 어떤 순서로 판정해 정확히 어떤 schema를 출력하는가. 이 계약은 SUMMARY·RULE·TABLE usage·
   최종 TABLE/COLUMN 전체에 적용한다.
10. 입력은 단순 필드 나열이 아니라 관계가 드러나는 evidence packet이어야 한다. TARGET→SOURCE,
    TARGET→symbol definition, TARGET→callee interface, TARGET→data object, condition→direct effect,
    table→column usage의 방향·소유권·좌표를 명시한다. 결정론 코드는 이 구조 관계만 만들며 업무
    의미를 만들지 않는다.
11. `[]`, `{}`, 빈 문자열, `(없음)`은 누락된 직렬화와 구별한다. prompt의 입력 사전은 각 빈 값이
    "해당 범위에서 확인된 직접 근거 없음"인지 "생산자 미지원/누락"인지 설명하며, 후자는 분석
    전에 fail-closed한다.
12. 최대 효율은 예시·판정 규칙을 삭제하는 최소 토큰화가 아니다. 동일 규칙의 중복 서술과 concern에
    불필요한 입력을 제거하되, 실제 나쁨/좋음 예시·우선순위·충돌 처리·needs_context 대체 행동·
    최종 자기점검은 보존한다.

## 원인 분류

- source에는 있으나 actual prompt에 없음: context 생산/전달 결함
- actual prompt가 필요한 의미를 빼라고 지시: prompt 계약 결함
- 근거와 지시가 충분하지만 응답이 틀림: 모델 한계
- 응답 이후 값이 달라짐/사라짐: schema·normalizer·저장 결함
- 근거 없는 의미가 accepted: validator 결함
- source 자체에 업무 뜻 없음: needs_context, 제품 오류 아님

## 최초 오좌표와 확정 원인

- DBMS RULE 표본 `PRC_DATA_ANALYSYS_TEST2.sql:215-219`의 원문에는 오류 출력 뒤
  `ROLLBACK`이 있으나 저장 RULE의 결정론 `then`에는 출력 호출만 남았다. 실제 모델 NL은
  rollback까지 맞게 설명했으므로 모델 결함이 아니다.
- Oracle parser listener가 `COMMIT`만 AST로 내고 `ROLLBACK`을 누락하며, Analyzer의 statement
  effect 계약도 target 없는 transaction control을 표현하지 못한다. 이 생산자 손실을 prompt로
  감추지 않고 parser AST → ontology → Rule effect의 한 경로에서 수리한다.
- Framework/DBMS summary 표본 8건은 실제 SOURCE가 충분했지만 공용 prompt가 목적 헤더만 쓰고
  분기·호출·R/W·반환 흐름을 생략하라고 지시했다. schema도 입력 부족 정상 종료를 표현하지
  못했다. 따라서 summary 결함의 공통 원인은 prompt+schema+consumer 계약이다.
- DBMS final COLUMN 122개는 모두 silent blank이며, `COMPUTE`/`TAGSN`의 충분한 source usage도
  step7의 column 출력 금지와 step8의 `insight["columns"]` 의존 사이에서 사라졌다.
- DBMS SUMMARY 463개 중 118개는 GPU가 아니라 `deterministic_summary.py`가 정규식과 AST label로
  한국어 summary/stereotype을 만든다. 이는 구조 투영이 아니라 자연어 의미의 이중 생산자다.
- 같은 file/start/end를 가진 한 줄 nested SELECT 3그룹/6노드는 range-only prompt key와 동일
  SOURCE로 구별되지 않았다. 의미 좌표의 identity는 full node id이고 range는 locator다.
- graph EXAMPLE 131개는 given이 있지만 then이 비었다. control-only RULE은 허용할 수 있어도
  empty-outcome EXAMPLE 생성은 허용하지 않는다.

## 합격 기준

- 봉인 표본 30~40개의 source/prompt/output/stored join 누락 0.
- 각 표본에 evidence sufficiency와 exact/partial/contradiction/fabrication/omission/needs_context 판정.
- 공용 prompt/schema/validator 수정은 특정 고객 식별자 0, 다른 이름·순서·중첩·언어 fixture 통과.
- 수정 후 전체 회귀와 보존-record 제한 replay, 별도 DB/output root의 Framework·DBMS 격리
  재분석을 통과한다. 새 Analyzer run은 원인 일괄수정과 검사기 확정 전 실행하지 않는다.
- 판정 결과는 표본과 측정 축에만 보고한다.
- 의미 직접 판정은 봉인40에서 약90% 이상을 목표로 하며 fabrication/contradiction은 평균과 별도
  0건 목표로 공개한다. GPU 의미 100%를 제품 구조가 보장한다고 주장하지 않는다.
- 저장 semantic 모집단의 silent blank/partial/insufficient/missing_context/finding을 inventory하고,
  근거 충분 빈값과 근거 부족 창작이 각각 0인지 별도 회계한다.
- actual `llm-prompts/*.jsonl`의 대표 SUMMARY·RULE·TABLE usage·최종 TABLE/COLUMN마다 독립 검토자가
  prompt만 읽고 task/field authority/allowed judgment/relation/decision/output 다섯 질문에 모호함 없이
  답할 수 있어야 한다. 템플릿 문자열이 아니라 저장된 system/user/schema를 검사한다.
- SUMMARY와 TABLE usage는 같은 generic context dump를 공유하지 않는다. SUMMARY는 effective flow
  근거, TABLE usage는 exact direct access 근거만 받으며 이름에서 직접/effective 범위를 추측하게
  하지 않는다.

### 현재 합격 판정 (2026-08-12)

- 동일 봉인40 직접 판정: `37 exact + 2 needs_context + 1 omission = 39/40(97.5%)`.
- fabrication 0, contradiction 0.
- Framework/DBMS 격리 재분석 finding 0, 구조 preflight diff 0.
- 전체 회귀 `1062 passed, 5 skipped, 388 subtests`, prompt byte 원장 `28/28 OK`.
- TABLE/COLUMN description·evidence status·missing context의 graph→API/MCP 실제 전달 통과.
- 이 판정은 봉인 표본과 실행한 두 corpus에 한정하며 전체 언어·DBMS 정확도로 일반화하지 않는다.

## Prompt·KV 불변

- canonical 10 §17.1의 프롬프트 템플릿을 따른다: 상세 지침, 앞쪽 실물 나쁨/좋음 대조,
  긍정형 대체 행동, 필드별 단일 작업, 끝 자기점검, 가변 evidence는 맨 뒤.
- 예시는 특정 corpus 식별자를 쓰지 않는 입력→잘못된 출력/올바른 출력 실물이어야 한다.
- 공통 system/static prefix는 같은 concern의 모든 호출에서 byte-identical이어야 한다. node ID,
  source, evidence, dynamic required keys는 user variable suffix에만 둔다.
- prompt hash 원장과 actual prompt prefix diff를 모두 검사한다. 논리 input token을 KV cache hit로
  오인하지 않고, 서버 KV 재사용은 관측 수단이 없으면 미검증으로 보고한다.
- 범용 수정 1회 replay 후 같은 실패가 남으면 문구 반복 대신 needs_context/model limit로 종료한다.

## 입력 충분성 상태 계약

- `sufficient`: 전체 흐름 summary와 역할을 현재 evidence로 확정, `missing_context=[]`.
- `partial`: 근거로 확정한 부분만 summary에 쓰고 빠진 전체 흐름 근거를 `missing_context`에 기록.
- `insufficient`: summary/stereotype을 비워 창작을 막고 필요한 원문·callee 계약·metadata·runtime
  사실을 `missing_context`에 기록한다.
- partial/insufficient는 graph와 finding에 명시하며 상위 compact/callee 소비자가 완전한
  canonical summary로 오인하지 않게 상태와 누락을 함께 전달한다.
