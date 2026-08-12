# spec 131 — 동적 의미 프레임 전환 현재 작업 정본

> 이 문서는 누적 일지가 아니라 새 세션이 현재 규칙·확정 사실·폐기 결정·미해결·다음 행동을
> 복구하는 단일 진실이다. 상태가 바뀌면 기존 현재 상태를 교체한다. 과거 실행 이력과 수치는
> canonical 문서와 `evidence/`가 소유하며, 현재 코드·actual logs·실행 결과가 문서보다 우선한다.

**Status**: Complete — 구현, 실제 Analyzer/Architect 검증, 회귀 완료
**Updated**: 2026-08-12

## 완료 결과

- 공통 `semantic-frame/v1` 계약과 bounded structural planner를 Framework/DBMS 전체 자연어 생산 경로에
  적용했다. legacy summary/description/nl은 frame projection이며 별도 의미 생산자가 아니다.
- grounding/completeness/ownership/population 검증, graph 저장, API/MCP source-free projection과
  Architect dedupe/evidence 계약을 구현했다.
- 격리 실제 실행은 Framework `semframefwfix20260812`, DBMS `semframedbfinal20260812`, Architect
  `semframearch20260812`를 사용했다. Simplified `PRO-002`와 Detailed `PRO-003` 모두 성공했고
  `accept/apply`는 0회다.
- 최종 회귀는 Analyzer `1064 passed, 5 skipped, 378 subtests`, Architect
  `1375 passed, 2 skipped, 3 warnings`다.
- 직접 의미 판정과 재현 위치는 `evidence/actual-validation-20260812.md`가 소유한다.
- 아래 0~12절의 설계·시작 snapshot은 결정 근거로 보존한다. 현재 미완료 구현 단계는 없다.

## 0. 새 세션 복구 관문

모든 Python 명령 전에 `$env:PYTHONIOENCODING='utf-8'`을 설정하고 다음 원문을 순서대로 전부 읽는다.

1. `C:\Users\roede\.codex\memories\principles.md`
2. `C:\Users\roede\.codex\memories\manual.md`
3. `C:\Users\roede\.codex\memories\speckit.md`
4. `D:\work\robo\project\robo-data-analyzer\.specify\memory\constitution.md`
5. `D:\work\robo\project\robo-data-analyzer\docs\handovers\analysis-performance-validation-canonical\04-검증방법.md`
6. 같은 폴더의 `11-핵심압축-인계정본.md` 전체
7. `08-단일인계-정본.md` §12
8. 이 spec의 `HANDOFF.md`, `spec.md`, `plan.md`, `tasks.md`,
   `evidence/semantic-review-40.md`
9. `D:\work\robo\project\robo-architect\specs\055-grounded-gwt-context\spec.md`,
   `plan.md`, `tasks.md`
10. 필요할 때 canonical 10의 수치와 이력

문서 계보는 `11 > 10 > 09 > 08`이다. 그러나 현재 규칙·정정·미해결·다음 행동은 이 문서가
소유한다. 과거 문서의 `full SOURCE`는 **Analyzer가 의미를 생성할 때 내부에서 보는 원문**을
뜻한다. 이를 Architect 정상 입력에 전체 원문을 싣는 근거로 해석하지 않는다.

새 세션에 붙여 넣을 대사는 `NEW-SESSION-PROMPT.md`에 있다.

### 0.1 원칙 원문 집행 게이트

위 문서는 배경자료가 아니라 모든 계획·수정·검증에 강제되는 계약이다. 새 세션은 읽었다는 말로
통과하지 않고, 첫 작업 전에 현재 계획·dirty diff·실행 환경을 다음 항목으로 감사한다.

- 사용자 설명도 요구이자 가설로 구분하고 source·현재 코드·actual logs·실행 결과로 독립 검증한다.
- 구현 전에 spec/plan/tasks의 입력·출력·정상/실패/경계·생산자/소비자·검증 기준을 고정한다.
- 지목된 prompt나 함수만 보지 않고 parser→context→wire→normalizer→storage→API/MCP→Architect의
  모든 생산자와 소비자를 전수 조사한다.
- 한 사례용 문구·예외·alias·우회가 아니라 같은 원인의 구조를 한 번에 제거한다.
- 템플릿이나 mock 통과를 actual delivery로 간주하지 않는다. 실제 저장 prompt/raw/parsed와 실제
  서비스·DB·API/MCP를 대조한다.
- 분석 정확도는 자동 테스트, 결정론 정답지/구조 대조, 주 에이전트의 직접 의미 판정 3층이 모두
  실행돼야 `검증됨`이라고 말한다. 실행하지 않은 축은 `미검증`으로 남긴다.
- 구조화 출력, constrained decoding, KV/cache, grammar/repetition 설계를 확정하기 전에 현재 배포와
  관련된 공식 문서·논문·프로젝트 문서 같은 1차 자료를 조사하고, 관측하지 않은 성능을 추측하지 않는다.
- 원칙·현재 spec과 충돌하는 구현을 발견하면 진행 편의를 위해 묵인하지 않고 HANDOFF와 tasks에
  반영한다. 정확도 맞교환, 광범위 계약 변경, 복구 어려운 삭제는 실행 전에 사용자에게 보고한다.
- 사용자 WIP·보존 DB/log를 보호하며 reset/checkout/clean, 범위 밖 되돌리기, 기본 DB 초기화를 하지 않는다.

## 1. 한 문장 목표

Analyzer가 원문 전체 흐름을 이해하되, parser가 실제 구문에 맞는 얕은 의미 틀과 검증 가능한
구조 사실을 만들고 LLM은 근거가 있는 의미 칸만 채우며, 그 결과를 중복 없는 의미 정본으로
저장해 Architect가 원문을 재분석하지 않고 정확하고 추적 가능한 GWT를 조립하게 만든다.

## 2. 범위와 고정 결정

- Framework와 DBMS를 같은 공통 계약으로 처리한다. 전략별 parser adapter만 다를 수 있다.
- A인 Python 관계식은 KEEP이다. 수정하거나 재실험하지 않는다.
- SUMMARY·RULE·EXAMPLE·CALL 의미·TABLE·COLUMN·MODULE·logical name·missing context 등
  Analyzer가 만드는 모든 자연어 의미가 감사·전환 범위다.
- 기존 병렬 처리, round planning, priority, batch scheduling, semaphore, concurrency는 건드리지 않는다.
- KV prefix는 안정적으로 유지하고 가변 target/evidence/frame은 user suffix에 둔다.
- 특정 corpus·함수·테이블·언어·샘플에 맞춘 이름·패턴·정규식 의미 생성은 금지한다.
- 봉인40 목표는 약 90% 이상이며 fabrication/contradiction은 평균과 별도로 각각 0건이다.
- 기존 표본 결과를 RULE 1,609개 전체나 모든 언어·DBMS로 일반화하지 않는다.

## 3. 권위와 책임 경계

### 3.1 Analyzer 내부 분석

원문은 Analyzer 내부 의미 분석의 권위 근거다. Analyzer LLM은 입력→선행 검증→조건별 처리→
CALL/RW→반환·외부 상태 변화→transaction/exception의 전체 흐름을 이해해야 한다. 여러 노드를
묶는 `1,500 tokens` 배치 상한을 개별 source 의미 상한으로 재사용해 원문을 버리지 않는다.
현재 분리된 개별 원문 상한은 `12,000 tokens`이며, 이를 넘는 경우에도 이름 기반 의미 요약으로
대체하지 않고 구조적 자식 후보와 명시적 충분성 상태를 사용한다. 이 수치의 최종 유지 여부는
새 frame 성능 측정으로 다시 검증한다.

### 3.2 parser·결정론 코드

결정론 계층은 다음 사실만 소유한다.

- target/node type, owner, child 관계, 실행 순서
- full node/evidence ID, 파일·절대행 좌표
- condition/operator/literal/상수·변수 정의
- CALL/callee/argument/result 연결
- READ/WRITE, TABLE/COLUMN identity, transaction/exception 구조
- 적용할 frame family와 실제 근거가 요구하는 slot 목록
- 조립·전달·schema/grounding/completeness 검증

결정론 코드는 업무 의미를 작성하지 않는다. 식별자 이름·패턴·정규식·고정 번역문으로 summary나
TABLE/COLUMN 역할을 만들지 않는다.

### 3.3 LLM

LLM은 parser가 미리 부여한 slot ID에 짧은 자연어 의미와 충분성 상태만 채운다. 좌표·조건식·
callee·물리 이름을 다시 생성하거나 구조 사실을 선택하지 않는다. 근거가 부족하면 추측 대신
`partial|insufficient`와 `missing_context`를 반환한다.

## 4. 목표 출력 계약: bounded dynamic semantic frame

저장 의미의 단일 진실은 `semantic-frame/v1`이다. 실제 필드명은 구현 inventory 뒤 계약 테스트로
고정하되 의미 구조는 다음을 따른다.

```json
{
  "schema": "semantic-frame/v1",
  "target": {"node_id": "...", "node_type": "FUNCTION"},
  "profile": {
    "child_refs": ["RULE:...", "CALL:..."],
    "ordered_flow": ["RULE:...", "CALL:...", "RETURN:..."]
  },
  "slots": {
    "FUNCTION:RESPONSIBILITY": {
      "evidence_refs": ["FUNCTION:...", "RULE:..."],
      "meaning": "...",
      "status": "sufficient",
      "missing_context": []
    },
    "RULE:311:TRUE_OUTCOME": {
      "evidence_refs": ["RULE:311", "RETURN:311"],
      "meaning": "...",
      "status": "sufficient",
      "missing_context": []
    }
  }
}
```

고정 원칙은 다음과 같다.

- 하나의 거대한 자유 summary 문장으로 모든 의무를 숨기지 않는다.
- 모든 대상에 거대한 공통 schema를 강요하지 않는다.
- 실제 node type과 존재하는 자식/evidence에 맞는 얕고 제한된 slot만 생성한다.
- 함수는 responsibility/input role/result/ordered flow, decision은 decision role/true·false outcome,
  loop는 iteration target/per-item action/termination, CALL은 purpose/argument role/result usage/
  side effect, SELECT는 selection purpose/filter/result usage, UPDATE는 change target/condition/
  transaction effect, exception은 failure/handling/propagation 같은 family를 사용한다.
- 자연어 값마다 slot type, target/evidence refs, status가 반드시 붙는다.
- 기존 `summary`, `description`, `rule.nl`은 검색·표시용 projection이 될 수 있으나 별도 의미
  생산자가 되면 안 된다.
- EXAMPLE은 accepted RULE의 구조 조건·효과로부터만 만들며 빈 outcome을 저장하지 않는다.

## 5. prompt와 입력 계약

모델은 실제 저장된 prompt 첫 읽기에서 다음에 답할 수 있어야 한다.

1. 내가 무엇을 왜 만들어야 하는가.
2. 각 입력값은 무엇이고 어느 생산자가 소유하는가.
3. ID·좌표·relation으로 어떤 사실끼리 연결되는가.
4. 그 사실로 무엇을 판정할 수 있고 무엇은 금지되는가.
5. 어떤 순서로 판정해 어느 slot에 어떤 결과를 넣는가.
6. 부족할 때 무엇을 `missing_context`로 요구하는가.
7. 어떤 나쁨/좋음 예시와 자기점검을 따라야 하는가.

프롬프트는 `TASK/GOAL → OUTPUT CONTRACT → INPUT GUIDE/AUTHORITY → RELATIONS → DECISION
PROCEDURE → EXAMPLES → SELF CHECK → VARIABLE INPUT`의 한 흐름을 갖는다. 같은 규칙을 반복하지
않되 예시·금지·충돌 우선순위·needs_context 대체 행동을 토큰 절약 명목으로 삭제하지 않는다.
최대 효율은 최소 문장이 아니라 중복 없이 가장 이해하기 쉬운 계약이다.

입력은 JSON 덤프가 아니라 TARGET→SOURCE, owner→child, condition→effect, CALL→argument/result,
TABLE→COLUMN usage 관계가 ID와 좌표로 드러나는 packet이어야 한다. 다만 claim마다 원문을 새로
잘라 복사하거나 새 좌표계를 만들지 않는다. 기존 parser node/RULE/CALL/evidence ID를 사용한다.

## 6. TABLE/COLUMN과 충분성

- 물리 이름을 번역해 업무 의미처럼 쓰지 않는다.
- 실제 DDL/comment/code usage/sample로 입증되는 역할만 설명한다.
- `COMPUTE`, `TAGSN`은 근거가 충분했는데 과거 전달 경로에서 빠진 omission 사례다.
- `ADBA_VAL`, `ETC`는 현재 근거 부족으로 `needs_context`가 정답이다.
- source 근거가 있는데 prompt까지 전달되지 않아 빈 값이면 제품 결함이다.
- source 자체에 업무 의미 근거가 없으면 `needs_context`이며 제품 오류가 아니다.

## 7. Analyzer → Architect 계약

Architect 정상 생성 경로에는 레거시 complete source나 새로 자른 source excerpt를 넣지 않는다.
Analyzer의 분석 의미를 다시 분석시키면 Analyzer의 가치가 사라지고 중복·비용·혼동이 증가한다.

Architect MCP projection은 다음을 ID 기준으로 한 번씩만 제공한다.

- target responsibility, input/result roles, ordered flow
- 구조 사실과 연결된 semantic slots/claims
- outbound callee 계약, direct/effective R/W 범위
- TABLE/COLUMN semantic role과 sufficiency
- evidence ID, owner, source coordinate, missing context

같은 callee/data object/claim은 한 번만 싣고 scenario가 ID로 참조한다. Given은 입력·선행 상태,
When은 Command/호출/처리, Then은 반환/Event/상태 변화에 연결되어야 하며 각 GWT는 실제 사용한
RULE/CALL/SYMBOL/TABLE evidence refs를 남긴다. Analyzer 결과가 부족하면 Architect가 정상 경로에서
같은 원문을 다시 읽어 보완하지 않고 부족한 evidence kind를 명시한다. 원문 조회는 사람의 감사와
결함 추적용 별도 경로다.

## 8. 명시적으로 폐기된 방향

2026-08-12의 `gwt-evidence/v2` WIP 중 **함수 complete source/hash를 Architect 기본 packet에 싣고
source를 최상위 authority로 강제한 방향은 폐기**한다. 자동 테스트 통과 여부와 관계없이 목표 계약과
반대이므로 구현 완료로 간주하지 않는다.

살려서 새 계약에 맞게 적응할 후보:

- 안정적인 evidence ID와 정확한 좌표
- RULE condition/effects/flow와 symbol definition
- outbound callee만 전달하는 경계
- direct TABLE/COLUMN evidence와 scenario `evidenceRefs`
- 운영자 지정 Analyzer DB 전달/reset, session-limit 오류 분류

제거·교체할 후보:

- Analyzer `node_detail(view=gwt)`의 complete source body/hash/authority
- Architect가 complete source를 필수 검증하거나 prompt 최상위 권위로 넣는 코드·테스트·skill 문구
- 전체 함수 원문을 scenario의 포괄 근거로 사용하는 validator/coverage 계약

과거 evidence와 역사 spec은 삭제하거나 현재화하지 않는다. 다만 현재 spec/HANDOFF/skill에서
폐기 방향을 활성 계약처럼 쓰는 문구는 제거한다.

## 9. 검증 사실과 미검증 경계

기존 계약에서 확인된 사실:

- 봉인40: `37 exact + 2 needs_context + 1 omission = 39/40(97.5%)`
- fabrication 0, contradiction 0
- 기존 Analyzer 회귀 `1062 passed, 5 skipped, 388 subtests`, prompt `28/28 OK`
- TABLE/COLUMN description·status·missing context의 graph→API/MCP 전달 통과
- 이전 Architect Simplified/Detailed 표본 GWT 21/21이 원문과 의미 정합

위 수치는 새 `semantic-frame/v1` 계약의 검증 결과가 아니다. 새 frame schema, 모든 의미 생산자의
slot 전환, 저장/graph/API/MCP, Architect projection/GWT는 아직 구현·실행 미검증이다. 최근 폐기 WIP의
자동 회귀 통과도 새 방향의 합격 증거로 사용하지 않는다.

## 10. 원인 추적과 검증 순서

틀린 좌표 하나를 같은 full node/evidence ID로 다음 순서에 연결한다.

```text
source → parser AST → Analyzer context/결정론 frame 조립
→ 실제 저장된 system/user/schema → raw/parsed
→ normalizer/validator/finding → graph/API/MCP → Architect
```

- prompt template만 보고 전달됐다고 판정하지 않고 실제 `llm-prompts/*.jsonl`을 연다.
- 결과가 이상하면 제품보다 checker·gold·좌표계·집계 단위를 먼저 반증한다.
- 한 사례에 땜질하지 않고 생산자와 모든 소비자를 감사한다.
- 자동 테스트 / 결정론 정답지·구조 전수 대조 / 주 에이전트 직접 의미 판정을 구분한다.
- 새 LLM 실행은 schema·validator·fixture·preflight·격리 DB/output root가 확정된 뒤 Framework와
  DBMS에 필요한 최소 횟수만 수행한다.
- Architect 실제 실행은 Analyzer가 닫힌 뒤 Simplified/Detailed 실제 경로로만 수행한다.
  `ROBO_CLUSTER_MCP_URL=http://127.0.0.1:15502/robo/mcp`, `accept/apply` 금지다.

## 11. 성능·안전 불변

- 기존 병렬 엔진과 A를 변경하지 않는다.
- dynamic schema는 얕고 bounded해야 하며 실제 근거가 요구하는 slot만 포함한다.
- grammar/repetition 폭주를 token cap이나 의미 삭제로 숨기지 않는다. frame family 분할, schema 깊이,
  batch 크기, 중복 필드를 진단해 해결한다.
- KV hit는 추측하지 않고 실제 prefix diff와 가능한 사용량 로그로 검증한다.
- dirty WIP, 보존 DB/log는 사용자 자산이다. `reset/checkout/clean`, 범위 밖 되돌리기, 기본 DB
  초기화, 기존 격리 DB/output 덮어쓰기를 금지한다.
- 보존 DB: `spec131fw1520260812`, `spec131db920260812`, Architect 과거 DB
  `spec055b20260812`. 새 실험은 별도 이름을 사용한다.
- 임시 실행물은 `_runs/` 아래에 두고 최종에는 최소 증거와 작업보고 문서만 남긴다.

### 11.1 정확도를 희생하지 않는 최대 작업 효율

- 같은 사실을 여러 문서·packet·prompt에 복제하지 않는다. HANDOFF는 현재 상태, spec은 요구,
  plan은 순서, tasks는 체크포인트, evidence는 실행 증거만 소유한다.
- 처음 한 번의 producer/consumer inventory를 재사용하고, 매 수정마다 저장소 전체를 무작정 다시
  읽지 않는다. 계약 이름·필드·경계가 바뀔 때만 영향 검색을 갱신한다.
- 가능한 read-only 실측과 독립 검사는 병렬로 수행하되, 공유 상태를 바꾸는 분석 run과 DB 작업은
  명시된 순서와 격리 경계를 지킨다.
- 보존 DB, 저장된 actual prompt/raw/parsed, 기존 봉인 evidence를 읽기 전용 기준선으로 재사용한다.
  같은 사실을 얻기 위해 LLM·Analyzer·Architect를 다시 실행하지 않는다.
- 파일 하나·task 하나가 끝날 때마다 전체 build/test/run을 반복하지 않는다. 작업 중에는 syntax,
  schema, import, diff와 직접 관련된 소수 계약 테스트처럼 값싼 국소 확인만 수행하고, 실패가 있으면
  그 자리에서 해결한 뒤 진행한다.
- contract→producer→normalizer/validator→storage→API/MCP→consumer를 하나의 일관된 변경 묶음으로
  완성한 뒤 정적 분석/관련 묶음 테스트를 한 번 수행하고, 모든 묶음이 합쳐진 뒤 전체 회귀를 한 번
  수행한다. 실제 서비스·GPU·격리 DB 검증은 최종 preflight 뒤 최소 횟수만 수행한다.
- schema/token/prefix/fixture 검사로 잡을 수 있는 문제를 실제 GPU run으로 찾지 않는다. 실제 LLM은
  모든 결정론·자동 preflight가 통과한 뒤 Framework/DBMS 대표 최소 횟수만 사용한다.
- Architect는 Analyzer 합격 전 실행하지 않고, 후속 stage는 이미 저장된 성공 provenance를 재사용해
  동일 node/detail을 반복 조회하지 않는다.
- 간결화를 이유로 지시·예시·근거·검증을 삭제하지 않는다. 제거 대상은 중복, 무관 payload,
  동일 source/claim 재전달, 불필요한 실행과 임시 산출물이다.
- 예상보다 큰 반복·토큰·지연 패턴이 나오면 무작정 원복하거나 실행을 반복하지 않고 즉시 중단해
  schema/frame/batch 원인을 수치로 분리한 뒤 한 번 수정한다.
- 진행 보고는 막힘, 파괴적 결정, 예상과 다른 대규모 패턴, 최종 결과만 짧게 한다.

## 12. 구현 시작 당시 snapshot (완료 후 역사 기록)

2026-08-12 실측:

- Analyzer `main` HEAD `507ea7f9358e95f501546c624313d14521516183`, 작업 시작 기준 기존
  dirty WIP 10개(9 modified + 1 untracked), 현재 inventory 문서 포함 11개
- Architect `main` HEAD `30e7dd645d27a7d9eb1e41c5e84ea1dd508cff10`, dirty WIP 26개
- Analyzer `.env` 기본 DB는 `neo4j`
- Neo4j 7687, Analyzer 15502, Architect 8501 listener가 있었고 workspace 상태 파일 일부는
  stale이었다. 새 세션은 health와 listener를 다시 실측한다.
- 현재 dirty WIP는 사용자 자산이며 위 8절 분류에 따라 `apply_patch`로 적응·제거한다.

실행 순서:

1. ~~전체 자연어 의미 생산자·schema·normalizer·storage·graph/API/MCP·Architect 소비자를 전수
   inventory한다.~~ 완료: `evidence/nl-producer-consumer-inventory.md`
2. Framework/DBMS AST를 공통 structural role과 evidence ID로 투영하는 `StructuralProfile` 계약을 고정한다.
3. 실제 node type/children에 따라 bounded slot을 만드는 frame planner와 completeness/grounding validator를 설계한다.
4. SUMMARY부터 RULE·CALL·TABLE/COLUMN·MODULE 등 모든 생산자를 frame-first로 전환한다.
5. legacy 문자열은 frame projection으로만 만들고 자연어 이중 생산자와 옛 full-source GWT 경로를 제거한다.
6. Architect MCP packet과 Simplified/Detailed prompt를 deduplicated semantic-frame 소비로 전환한다.
7. Framework/DBMS 반대 fixture와 schema 크기·출력량·반복·prefix를 변경 묶음 단위로 검증하고,
   모든 생산자·소비자가 정합해진 뒤 전체 회귀를 한 번 수행한다.
8. preflight 뒤 새 격리 DB/output root에서 최소 actual Analyzer 실행 및 봉인 직접 판정을 한다.
9. Analyzer 합격 뒤 Architect 두 실제 경로를 최소 실행해 GWT와 evidence refs를 원문에 대조한다.
10. 작업보고의 실제 전후 데이터·테스트 결과를 교체하고 임시 실행물을 정리한 뒤 요청 범위만 commit/push한다.

첫 미완료 작업은 2번 `StructuralProfile`과 `semantic-frame/v1` 계약 고정이다. inventory의
생산자·소비자·WIP 판정을 구현 범위의 단일 영향표로 재사용한다.
