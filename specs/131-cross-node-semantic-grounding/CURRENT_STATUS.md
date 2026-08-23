# Analyzer 의미 무손실 작업 현재상태

**Updated**: 2026-08-23
**Status**: Strategy A 표기 비교 봉인 완료. 다음 구현은 C-080 IR 2.0
**Role**: 현재 HEAD·환경·보존 WIP·미해결·바로 다음 행동의 유일한 정본

상세 조사 원장은 `history/handovers/12-20260821-current-status-snapshot-pointer.md`로 정확히
복원한다. 다음 세션은 이 파일을 먼저 읽고, 필요한 evidence만 찾아 읽는다.

## 1. 목표와 고정 원칙

- Analyzer의 source→AST→actual prompt→raw/parsed→validator→Neo4j→API/MCP 전체에서 의미를
  조용히 잃지 않게 한다. 숫자 증설·해피패스·증상 패치는 금지한다.
- 원문 요약을 다음 LLM의 권위 입력으로 쓰지 않는다. 큰 입력은 전체 의미 의무를 먼저 확정하고,
  exact source/evidence closure로 나눈 뒤 canonical ID로 exact-once 병합한다.
- ANTLR Parser는 언어별 구문·구조 사실만 생산한다. Analyzer가 내부 language semantic adapter와
  language-neutral resolution core를 소유한다. Analyzer의 언어별 정규식 재파싱·추측은 금지한다.
- Framework 의미 owner는 routine이다. 작은 routine 내부를 별도 의미 단위로 쪼개지 않는다.
- DBMS 의미 owner는 effective AST CodeNode다. Rule/Example은 LLM 0회 결정론 결과다.
- 일부 파일만 업로드되는 상황은 정상이다. 분석 범위 밖 정의 사용은 external 경계다.
  unresolved를 별도 graph node/label로 만들지 않고, 필요한 경우 해당 구조 사실의 내부 진단 속성으로만 둔다.
- scanning agent는 패턴 후보 제안용이다. 의미·관계의 최종 권위가 아니다.
- Neo4j 인증 또는 `SHOW DATABASES` 실패 시 즉시 중단한다. 임시 DB를 만들지 않으며 허용 DB는
  `neo4j|test|framework|dbms|system`뿐이다. 실행물은 `_runs/framework|dbms`만 사용한다.
- Architect는 사용자 지시로 보류다. 코드·서비스·DB·WIP를 읽거나 수정하거나 stage하지 않는다.
- 자동 회귀, 결정론 전수 회계, 봉인 직접 의미 판정의 3층을 모두 통과하기 전 정확도를 주장하지 않는다.
- 기존 틀 변경은 근거·rollback·red를 먼저 고정하고 수행한다. 기존 파이프라인 유지 자체는 목표가 아니다.

## 2. 현재 환경과 보존 WIP

- Analyzer HEAD/origin: `281ab3d885e7f7b84b0f920ce56a91a3708bc021`.
- Analyzer 보존 WIP:
  - modified `pipeline/steps/step2_load_ast/evidence_ir.py`: 미완성 IR 1.3 decoder 시도.
    잃거나 IR 1.3 consumer로 완성하지 말고 IR 2.0 방향으로 교체한다.
  - untracked `scripts/recompute_strategy_a_notation.py`: 출처 미확정. 읽기·실행·stage하지 말고 보존한다.
- Parser 마지막 확인 HEAD/origin: `5dd64106540674a4aebfdb37ad897a375950d335`, clean.
- Catalog·Fabric·Architect에는 기존 사용자 WIP가 있었다. 이번 인계 준비에서 재검사·수정·stage하지 않았다.
- 2026-08-23 Neo4j 인증과 `SHOW DATABASES` 성공. 허용된 5개 DB만 있고 모두 online이다.
  이번 인계 준비에서 DB 쓰기·서비스 실행은 0회다.

다음 세션은 작업 시작 전에 Neo4j gate와 Analyzer/Parser status만 다시 확인한다. Catalog·Fabric·Architect는
현재 작업이 직접 필요로 하지 않는 한 검사 범위에도 넣지 않는다.

## 3. 완료되어 다시 하지 않을 일

### 전수조사와 확정 결함

- C-001~C-079 상한·절단·Top-N·sampling·skip/fallback·중간 LLM·summary-of-summary·
  timeout/retry/cache/batch/concurrency 생산자·소비자 조사는 완료했다.
- F-001 `semantic_frame.py` 의미 leaf 600자 상한은 숫자 증설 없이 obligation-derived 분할과
  exact-once merge 계약으로 해결됐다.
- 상세 배선·유입 commit·판정·검증은 2026-08-21 상세 원장과 `evidence/`에 있다. 같은 검색을 반복하지 않는다.

### Strategy A Python 표기 비교

동일 4함수·104행·3회, 조건당 312행으로 정식 비교를 완료했다.

| 조건 | Python 표기 성공 | 반복 Python AST 동일 | 보수적 정규화 후 동일 |
|---|---:|---:|---:|
| 이전 모델 + 역사 지시문 | 284/312 (91.03%) | 59/104 | 59/104 |
| 새 모델 + 역사 지시문 | 275/312 (88.14%) | 79/104 | 79/104 |
| 새 모델 + 개선 지시문 | **303/312 (97.12%)** | **91/104** | **100/104** |

- formal run: `_runs/framework/strategy-a/strategy-a-stage1-current-comparable-20260821-r1`
- workers 16, current variant 각 45호출, content retry 0, HTTP/finish/JSON/row closure 45/45.
- evidence: `evidence/strategy-a-notation-comparison-20260821.md`.
- 화면공유 보고: `D:\work\robo\docs\작업보고내용\2026-08-21-Strategy-A-모델변경-비교.md`.
- 이 결과는 Python 표기와 반복 동일성만 판정한다. 의미 정확도나 제품 승격을 뜻하지 않는다.
- 이미 원자료와 hash가 봉인됐으므로 재실행·재분석하지 않는다.

## 4. 바로 다음 미해결: C-080 IR 2.0

### 확정 원인

- Parser `CAstListener`가 구조 사실을 넘어 C의 final call binding/type/linkage를 직접 결정한다.
- IR 1.3 r14의 bound 57건은 전부 same translation unit이며 cross-TU positive는 0이다.
- 이를 Analyzer consumer에 맞추면 Parser 의미 소유권과 C 특화 결정을 고착한다.

### 목표 구조

`Parser structural evidence → Analyzer internal language semantic adapter → language-neutral resolution core`

- Parser IR 2.0: language/frontend identity, call/callee/receiver/argument range, callable 선언·정의,
  lexical scope/order, typed specifier/declarator/parameter/attribute의 구문과 presence만 생산한다.
- Analyzer language adapter: C/Java/Python/SQL의 scope·binding·type/linkage 의미를 해석한다.
- Analyzer common core: target 0/1/N, source/configuration 격리, canonical key, exact-once ledger를 검증한다.
- product external은 정상 경계다. unresolved graph entity는 만들지 않는다.
- `ontology_roles.yaml`은 graph materialization 분류로 유지하고 call binding authority로 쓰지 않는다.

### 구현 전 순서

1. 현재 formal red와 `evidence_ir.py` WIP를 IR 1.3 소유권 위반 기준으로 감사한다.
2. Parser structural IR 2.0 ↔ Analyzer adapter/core 계약을 spec/contracts에 먼저 고정한다.
3. C 한 언어의 해피패스가 아니라 0/1/N, cross-file, partial upload, ambiguity, dynamic/configuration,
   source isolation을 포함한 language-neutral red를 만든다.
4. Parser와 Analyzer 책임 슬라이스를 분리 구현하고 각 슬라이스 회귀를 한 번에 실행한다.
5. 실제 작은 데이터 영역에서 3층 검증 후에만 영역을 늘린다.

프레임워크 전체 재분석, Strategy A 재실행, 새 모델 호출, 임시 DB, Architect 검사는 이 단계의 선행 작업이 아니다.

## 5. 다음 세션 시작 절차

1. 루트 `AGENTS.md`와 필수 메모리 3개를 읽는다.
2. spec 131 `README.md`의 순서대로 계약 원문·이 파일·`04-검증방법.md`를 읽는다.
3. Neo4j 인증/`SHOW DATABASES`를 확인하고 실패하면 중단한다.
4. Analyzer와 Parser의 HEAD/status만 확인하고 위 WIP를 보존한다.
5. Strategy A를 다시 돌리지 않고 C-080의 기존 red·계약·WIP 차이부터 읽는다.
6. 한 책임 슬라이스의 계약→red→구현→집중 회귀→전체 회귀→3층 증거를 묶어 commit/push한다.

## 6. 토큰·시간 절약 규칙

- 입력·설정·모델·prompt·정답지·계약의 hash를 시작할 때 한 번 봉인한다. dependency hash가 그대로면
  같은 원문과 분석물을 다시 읽거나 만들지 않고 재사용한다.
- 각 수정은 원인, 바뀐 producer/consumer, 영향 좌표, 기대 변화, 미영향 범위를 짧은 change ledger에 남긴다.
- 관련 수정은 한 책임 슬라이스로 모은다. 수정마다 전체 재분석하지 않고 formal red·정적 검사·영향받은
  집중 테스트만 실행해 원인 혼합을 막는다.
- 분석 대상이 크면 작은 실제 데이터 영역에서 첫 실패와 파생 영향을 먼저 닫는다. 표본 gate를 통과한 뒤
  중간 영역으로 넓히고, 모든 관련 수정이 끝난 마지막에 전체 재분석·전수 회계를 한 번 실행한다.
- 재검증은 전체 결과를 다시 눈으로 읽는 방식이 아니다. sealed baseline과 새 결과의 diff, exact-once
  completeness ledger, 새 실패 좌표를 우선 보고 사람이 직접 볼 범위를 결정론적으로 제한한다.
- 큰 문서·raw 로그를 대화에 전부 출력하지 않는다. 파일 경로·hash·건수·첫 실패 좌표만 보고한다.
- 같은 hash의 원문을 세션 안에서 다시 읽지 않는다. `rg`/AST 검색을 묶고 결과를 evidence에 즉시 기록한다.
- 테스트는 변경 중 집중 테스트, 슬라이스 종료 시 전체 회귀로 모은다. 같은 suite를 습관적으로 반복하지 않는다.
- 리서치는 현재 결정을 바꿀 불확실성이 있을 때만 공식 문서·논문 등 1차 출처로 수행한다.
  출처를 그대로 권위로 삼지 않고 현재 계약·실측·반례와 교차검증한다.
- 막힌 문제에 임의 제한·fallback을 넣지 않는다. 필요한 외부 deep research가 있으면 사용자에게 구체적
  질문과 현재 한계를 전달한다.
- 별도 병렬 에이전트·추가 handoff 문서·새 상태 원장을 만들지 않는다.

## 7. 현재 검증/미검증 경계

- 검증됨: 312대312 Python 표기 회계와 반복 동일성, run/hash 봉인, Neo4j 2026-08-23 gate.
- 미검증: Strategy A 의미 정확도, 다른 데이터/언어 일반화, C-080 IR 2.0 제품 구현과 실제 cross-file 결과.
- 과거 상세 원문: `history/handovers/12-20260821-current-status-snapshot-pointer.md`.
