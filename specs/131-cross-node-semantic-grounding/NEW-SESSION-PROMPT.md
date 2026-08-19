# 새 세션 복붙용 대사

아래 코드 블록 전체를 새 세션의 첫 메시지로 붙여 넣는다.

```text
robo Analyzer 동적 의미 프레임 작업을 이어서 진행합니다. 이전 채팅 요약이나 기억에 의존하지 말고
아래 원문 정본과 현재 코드·실행 상태로 복구하세요.

먼저 `$env:PYTHONIOENCODING='utf-8'`을 설정하고 다음 문서를 순서대로 전부 읽으세요.

1. C:\Users\roede\.codex\memories\principles.md
2. C:\Users\roede\.codex\memories\manual.md
3. C:\Users\roede\.codex\memories\speckit.md
4. D:\work\robo\project\robo-data-analyzer\.specify\memory\constitution.md
5. D:\work\robo\project\robo-data-analyzer\docs\handovers\analysis-performance-validation-canonical\04-검증방법.md
6. 같은 폴더의 11-핵심압축-인계정본.md 전체
7. 08-단일인계-정본.md §12
8. D:\work\robo\project\robo-data-analyzer\specs\131-cross-node-semantic-grounding\HANDOFF.md 전체
9. 같은 spec의 spec.md, plan.md, tasks.md, evidence\semantic-review-40.md
10. D:\work\robo\project\robo-architect\specs\055-grounded-gwt-context\spec.md, plan.md, tasks.md
11. 필요할 때 canonical 10의 수치와 이력

문서 계보는 11 > 10 > 09 > 08입니다. 다만 현재 규칙·확정 사실·폐기 결정·미해결·다음 행동의
단일 진실은 spec131 HANDOFF입니다. 문서보다 현재 코드·actual logs·실행 결과가 우선합니다.

위 원칙 원문은 참고자료가 아니라 필수 집행 계약입니다. 읽었다는 말만 하지 말고 구현 전에 현재
계획·dirty diff·실행 환경이 원칙을 위반하는지 먼저 감사하세요. 사용자 설명도 요구이자 가설로
구분해 source·현재 코드·actual logs·실행 결과로 독립 검증하고, SpecKit으로 입력·출력·정상·실패·
경계·생산자·소비자·검증 기준을 고정한 뒤 구현하세요. 지목된 함수나 prompt만 보지 말고 전체
생산자와 소비자를 조사하며, 한 사례용 문구·예외·alias·우회가 아니라 공통 원인을 근본 해결하세요.
템플릿과 mock 통과를 actual delivery로 간주하지 말고 실제 prompt/raw/parsed/DB/API/MCP를 확인하세요.
분석 정확도는 자동 테스트, 결정론 정답지·구조 대조, 주 에이전트 직접 의미 판정의 3층을 모두 실행한
뒤에만 검증됐다고 말하고 실행하지 않은 축은 미검증으로 남기세요.

구조화 출력, constrained decoding, KV/cache, grammar/repetition 설계를 확정하기 전에 현재 배포와
관련된 공식 문서·논문·프로젝트 문서 같은 1차 자료를 조사하세요. 관측하지 않은 성능이나 cache hit를
추측하지 마세요. 원칙·현재 spec과 충돌하는 구현을 발견하면 편의를 위해 묵인하지 말고 HANDOFF와
tasks에 반영하세요. 정확도 맞교환, 광범위 계약 변경, 복구 어려운 삭제는 실행 전에 보고하세요.

정확도를 희생하지 않는 범위에서 최대한 효율적으로 작업하세요. producer/consumer inventory와 영향표는
처음 한 번 정확히 만들어 계속 재사용하고, 같은 사실을 여러 문서·packet·prompt에 복제하지 마세요.
보존 DB와 저장된 actual prompt/raw/parsed, 봉인 evidence는 읽기 전용 기준선으로 재사용하며 같은 사실을
얻으려고 Analyzer·Architect·LLM을 반복 실행하지 마세요. 가능한 read-only 검사는 병렬로 수행하되 상태를
바꾸는 DB/run은 격리 순서를 지키세요. 파일 하나·task 하나마다 전체 build/test/run을 반복하지 마세요.
작업 중에는 syntax/schema/import/diff와 직접 관련된 소수 계약 테스트 같은 값싼 국소 확인만 하고,
contract→producer→normalizer/validator→storage→API/MCP→consumer를 일관된 변경 묶음으로 완성한 뒤
그 묶음의 정적 분석·관련 테스트를 한 번 수행하세요. 모든 묶음이 정합해진 뒤 전체 회귀를 한 번만
수행하세요. 국소 확인이 실패한 상태를 알면서 다음 변경으로 덮지는 마세요. schema/token/prefix/
fixture로 잡을 문제를 GPU로 찾지 말고 actual LLM은 모든 preflight 뒤 Framework/DBMS 대표 최소 횟수만
사용하세요. Architect는 Analyzer 합격 전 실행하지 말고 저장된 성공 provenance와 ID를 재사용해 같은
node/detail을 반복 조회하지 마세요. 줄일 것은 중복·무관 payload·동일 source/claim 재전달·불필요한 실행과
임시 산출물이지, 의미 지시·예시·근거·검증이 아닙니다. 폭주나 예상 밖 지연이 생기면 무작정 원복·반복
실행하지 말고 schema/frame/batch 원인을 수치로 분리해 한 번 수정하세요.

현재 최우선은 Architect 실행이 아니라 Analyzer의 모든 자연어 의미를 bounded dynamic semantic
frame으로 전환하는 일입니다. Framework와 DBMS가 같은 공통 계약을 사용해야 합니다. A인 Python
관계식과 기존 병렬 처리·priority·round planning·batch scheduling·semaphore·concurrency는 건드리지
마세요.

Analyzer 내부에서는 실제 원문을 충분히 읽고 입력→검증→조건별 처리→CALL/RW→반환·외부 상태
변화→transaction/exception 전체 흐름을 이해해야 합니다. 1,500토큰 배치 묶음 상한을 개별 source
의미 상한으로 재사용해 원문을 버리지 마세요. 그러나 Architect 정상 생성 packet에 complete source나
새로 쪼갠 source excerpt를 넣어 다시 분석시키지도 마세요. 원문은 Analyzer 내부 분석과 사람의 감사·
결함 추적용 권위 근거입니다.

parser와 결정론 코드는 node type, children, ownership, order, coordinates, condition, literal/symbol,
CALL/argument/result, READ/WRITE, TABLE/COLUMN identity, transaction, applicable slot만 만듭니다.
이름·패턴·정규식·고정 번역문으로 업무 의미를 만들지 않습니다. LLM은 실제 구조에 따라 미리 만들어진
얕고 제한된 의미 slot의 자연어 의미와 sufficient/partial/insufficient, missing_context만 채웁니다.

하나의 큰 자유 summary 문장과 모든 경우를 담은 거대한 재귀 schema를 모두 피하세요. 함수, decision,
loop, CALL, SELECT, UPDATE, exception, transaction 등 실제 node type과 존재하는 자식/evidence에 따라
필요한 의미 칸만 동적으로 만드세요. 모든 자연어 값은 slot type, target/evidence refs, status에 연결되어야
합니다. 기존 summary/description/nl은 semantic frame의 표시·검색 projection일 수 있지만 별도 의미
생산자가 되어서는 안 됩니다.

프롬프트는 모델이 내가 무엇을 왜 해야 하는지, 각 입력값이 무엇이고 누가 소유하는지, ID·좌표·relation이
어떻게 연결되는지, 무엇을 판단할 수 있고 무엇은 금지되는지, 어떤 순서로 어느 slot을 채우는지,
부족하면 무엇을 요구하는지 모호함 없이 알게 해야 합니다. TASK/GOAL → OUTPUT → INPUT AUTHORITY →
RELATIONS → DECISION PROCEDURE → 나쁨/좋음 EXAMPLES → SELF CHECK → VARIABLE INPUT의 한 흐름으로
중복을 제거하되 지시·예시·금지·needs_context 행동을 토큰 절약 때문에 삭제하지 마세요.

TABLE/COLUMN은 물리 이름을 번역하지 말고 DDL/comment/code usage/sample로 입증된 역할만 설명합니다.
COMPUTE와 TAGSN은 근거 충분 omission이었고 ADBA_VAL과 ETC만 현재 needs_context입니다. source 근거가
prompt까지 전달되지 않아 빈 값이면 제품 결함이고, source 자체에 뜻이 없으면 needs_context가 정답입니다.

Architect 도구는 semantic frame에서 target/claims/callees/data objects를 ID로 중복 제거해 전달하고
GWT scenario가 사용한 RULE/CALL/SYMBOL/TABLE evidence refs를 남기게 해야 합니다. Given은 입력·선행
상태, When은 Command/처리, Then은 반환/Event/상태 변화에 근거해야 합니다. Analyzer 결과가 부족하면
Architect가 같은 원문을 다시 읽어 메우지 말고 부족한 evidence kind를 명시해야 합니다.

2026-08-12의 gwt-evidence/v2 WIP에서 complete source/hash를 Architect 기본 packet에 싣고 source를
최상위 authority로 강제한 방향은 폐기됐습니다. 안정적인 evidence ID, 구조·좌표, RULE condition/effects,
symbol definition, outbound callee, direct TABLE/COLUMN, scenario evidenceRefs, 운영자 지정 DB 전달과
session-limit 분류는 새 계약에 맞게 살릴 수 있습니다. 폐기 WIP의 자동 테스트 통과를 새 frame의 검증
결과로 오인하지 마세요.

기존 봉인40 39/40=97.5%, fabrication 0, contradiction 0과 이전 Architect GWT 21/21은 과거 계약의
기준선일 뿐 새 semantic-frame 결과가 아닙니다. 새 정확도는 자동 테스트, 결정론 구조 대조, 원문 직접
의미 판정의 3층을 모두 수행한 뒤에만 주장하세요. 실제 LLM 실행은 구조 계약·validator·fixture·preflight·
재사용 DB `framework|dbms`와 `_runs/<strategy>`를 확정한 뒤 Framework/DBMS에 필요한 최소 횟수만 수행하세요. Architect 실제
실행은 Analyzer가 닫힌 뒤 Simplified/Detailed 실제 경로로 하며 ROBO_CLUSTER_MCP_URL=http://127.0.0.1:15502/robo/mcp를
사용하고 accept/apply는 호출하지 마세요.

틀린 결과는 같은 full node/evidence ID로 source → parser AST → Analyzer context/frame 조립 → 실제
저장된 system/user/schema → raw/parsed → normalizer/validator/finding → graph/API/MCP → Architect 순서로
추적하세요. 템플릿만 보지 말고 실제 llm-prompts/*.jsonl을 여세요. 제품보다 checker·gold·좌표계·집계
단위를 먼저 반증하고, 한 사례가 아니라 생산자와 모든 소비자를 넓게 감사하세요.

기존 dirty WIP, 보존 DB, logs는 사용자 자산입니다. reset/checkout/clean, 범위 밖 되돌리기, 기본 DB
초기화, 기존 격리 DB/output 덮어쓰기를 금지합니다. 현재 HEAD/status/diff, Analyzer .env, 서비스 포트,
corpus와 보존 logs를 다시 실측한 뒤 HANDOFF §12와 tasks.md의 첫 미완료 T131-19부터 승인 대기 없이
자율적으로 진행하세요. 상태가 바뀌면 HANDOFF의 기존 현재 상태를 교체 갱신하고 누적 일지로 만들지
마세요. 진행 보고는 막힘·파괴적 결정·예상과 다른 대규모 패턴·최종 결과만 짧게 하세요.
```
