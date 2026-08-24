# Analyzer 의미 무손실 작업 현재상태

**Updated**: 2026-08-24
**Status**: C-080 호출 결합, C-081 구조식 transport, C-082 discriminated expression IR actual 검증 완료. 다음은 exact slot closure와 결정론 projection이다
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

- Strategy A 실행·증거 baseline commit: `281ab3d885e7f7b84b0f920ce56a91a3708bc021`.
- 현재 Analyzer HEAD/origin은 다음 세션 시작 시 Git에서 한 번 확인한다. 이 문서를 커밋할 때마다 낡는
  동적 HEAD 값을 현재상태 문서에 반복 복사하지 않는다.
- Analyzer 보존 WIP:
  - modified `pipeline/steps/step2_load_ast/evidence_ir.py`: 기존 미완성 IR 1.3 decoder WIP를 보존해
    IR 2.0 strict structural decoder로 교체했다. Analyzer commit 전까지 다른 변경과 섞지 않는다.
  - untracked `scripts/recompute_strategy_a_notation.py`: 출처 미확정. 읽기·실행·stage하지 말고 보존한다.
- Parser HEAD/origin: `da453950f70d6b38831d33a0c06fda230122f2ec`, clean.
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

## 4. 현재 구현: C-080 IR 2.0

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

### 2026-08-23 구현·집중 검증

- Parser는 `c/antlr-c/v1`, lexical `scopePath`, receiver/argument range, recursive typed syntax component만
  IR `2.0.0`으로 발행한다. final binding/type/linkage 필드는 0건이다.
- Parser commit/push: `da453950f70d6b38831d33a0c06fda230122f2ec`; 전체 회귀 211, 실패/오류 0,
  스킵 11. 봉인된 Strategy A actual test는 재실행하지 않았다.
- Analyzer decoder는 기존 `_KINDS` NameError와 version/callable dispatch 누락을 제거하고 IR 1.0~1.2
  역사 wire 및 IR 2.0 구조 wire를 strict validation한다. IR 1.3 Parser semantic wire는 fallback으로 받지 않는다.
- C adapter는 component/token/range만 사용해 lexical visibility, internal/external linkage와 declarator
  compatibility를 판정한다. common core는 `internal|external|dynamic|configuration_dependent|ambiguous|
  unsupported|inactive`를 call ID 순서로 exact-once 회계한다.
- external은 occurrence와 collision-free identity만 보존하며 external stub/CALLS edge를 만들지 않는다.
- actual `cart.c` SHA-256 `5d57a30a68796932fa95496c3a7cfa297b0cbb365b78728b31c6f6887ff1cfec`:
  call 202, callable 9, internal 4, external 198, ledger 202/202, deterministic SHA-256
  `dacb544433f607a6300b23c662d526b6af2789d282f027eb828da92c397cb182`.
- 직접 판정: `15479:27 count_cart_items(member_id)`는 선행 static 선언과 같은 TU static 정의로 internal,
  `2383:44 memset(...)`은 업로드 내 정의가 없어 external이다.
- 집중 자동 회귀: decoder 61, formal resolution 8, step2/step5 260(+subtest 19),
  graph/API/search/execution 181(+subtest 22), 모두 실패 0.

### 남은 순서

2026-08-24 C-080 Analyzer 책임 슬라이스를 commit/push했다.

- commit/remote HEAD: `787a6d5` (`feat(analyzer): resolve C calls from structural IR 2.0`)
- staged index 독립 회귀: 65 tests, 실패 0, skip 1
- worktree 전체 회귀에서 C-080 producer/consumer 실패 0. 실험 prompt 배치 red 18건은 spec 전용
  경로 격리 후 집중 계약 4/4로 닫았다.
- commit은 27파일이며 Strategy B/성능/scan WIP와 금지 스크립트를 포함하지 않는다.

다음 별도 책임 슬라이스는 condition/effect 원문을 Analyzer가 언어별 문자열로 재파싱하지 않도록
Parser structural expression fact 계약을 red로 고정하고, Analyzer language adapter가 이를
language-neutral semantic IR로 변환하게 하는 것이다.

## 5. 다음 세션 시작 절차

1. 루트 `AGENTS.md`와 필수 메모리 3개를 읽는다.
2. spec 131 `README.md`의 순서대로 계약 원문·이 파일·`04-검증방법.md`를 읽는다.
3. Neo4j 인증/`SHOW DATABASES`를 확인하고 실패하면 중단한다.
4. Analyzer와 Parser의 HEAD/status만 확인하고 위 WIP를 보존한다.
5. Strategy A를 다시 돌리지 않고 C-080의 기존 red·계약·WIP 차이부터 읽는다.
6. 한 책임 슬라이스의 계약→red→구현→집중 회귀→전체 회귀→3층 증거를 묶어 commit/push한다.

## 6. 토큰·시간 절약 규칙

- 원인이 확정되지 않은 상태에서는 추론·추정으로 제품을 수정하지 않는다. 가설은 다음 판별 검사를
  고르는 용도일 뿐이며, source→실제 값→출력의 인과 고리를 닫아야 구현 단계로 넘어간다.
- 처음부터 모든 경로를 깊게 파지 않는다. 봉인된 ledger/hash와 기존 actual 확인부터 시작하고 원인이
  남을 때만 영향 좌표→집중 계측→최소 재현·역실험→Git history/bisect→외부 1차 근거 순으로 깊어진다.
- 필요한 경우 rollback 가능한 진단용 계측·실험 수정이나 관련 경로 전수조사로 원인을 찾는다. 제품
  동작 수정은 인과 고리를 닫은 뒤 한다. 원인 불명을 이유로 임의 제한·fallback·우회를 넣지 않는다.
  외부 정보 없이는 인과 고리를 닫을 수 없을 때만 사용자에게 확인된 사실·배제된 가설·필요한 deep
  research 질문을 전달한다.
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
- 검증됨: C-080 C IR 2.0 Parser producer, Analyzer strict decoder/C adapter/common core,
  0/1/N·cross-file·partial upload·ambiguity·dynamic/configuration 반례, 작은 actual 202/202.
- 검증됨: C-080 Analyzer C adapter/common core staged snapshot과 commit/push `787a6d5`.
- 미검증: Strategy A 의미 정확도, Java/Python/SQL IR 2.0 adapter, structural expression fact,
  다른 데이터 일반화와 최종 4.4만 줄 성능 판정.
- 과거 상세 원문: `history/handovers/12-20260821-current-status-snapshot-pointer.md`.

## 8. 2026-08-24 C-080 성능 red와 작업 전환

### 봉인된 SKT actual 범위

- 대상: `분석대상모음/framework/c/apascrt00010t02_20260406`, C 23파일, 44,143행.
- source SHA-256: `cc65fd07be37abad7a1c496439aad56a4674050d312a7de3353180b258a0e2fc`.
- DDL SHA-256: `d13663ee966823b219869b95dc493d61d610c6ebecaaddc93b20efc3f4f4cfa9`.
- Parser actual: source/analysis/diagnostics 각 23, recovered/partial/unresolved/failed 모두 0.
- 정확도·원인 조사는 작은 실제 파일/노드만 사용하고, 최종 속도 판정만 위 23파일 전체를 한 번 사용한다.

### 확정된 성능 원인과 보존 진단

- 중단한 23파일 run은 약 37분 시점에도 미완료였다. 이는 단일 모델 호출 시간이 아니라 Analyzer가
  request-count 32개를 동시에 제출해 실제 SGLang token pool을 과부하시킨 end-to-end 상태다.
- 실제 serving 정보: `Qwen/Qwen3.8-27B-FP8`, DP 1, TP 4, `max_running_requests=16`,
  `max_total_num_tokens=270000`, `max_prefill_tokens=16384`.
- SKT 1파일 `apascrtu0010t02.c` 진단은 1,048.8초, 모델 호출 78건, input 2,465,940,
  output 117,105 tokens였다. 102 summary attempt 중 deadline 28건, output-length 후 schema로
  오분류된 실패 4건을 확인했다.
- 서로 다른 실제 prompt 8개씩의 A/B: concurrency 1은 59.969초, 4는 42.156초, 8은
  41.140초였다. 4→8 처리량 이득은 거의 없고 개별 지연만 증가했다. 임의 고정 상한의 근거로
  승격하지 않고 token-aware admission과 입력/출력 계약 축소 설계의 원인 증거로만 쓴다.
- `finish_reason=length`를 `schema`가 아닌 `length`로 보존하는 runner 수정은 집중 회귀 49 passed,
  subtest 6, 실패 0이다. 아직 Analyzer 책임 슬라이스로 commit하지 않았다.
- 1파일 `test` DB에는 완료 generation `20260823_151648_92ac87d98195`가 남아 있다.
  `framework` DB에는 중단 run의 부분 generation이 있어 다음 공식 전체 run 전에 기존 DB만
  명시적으로 정리해야 한다. 새 DB는 만들지 않는다.
- embedding 입력의 2,000자 truncation 경고도 별도 의미 누락 위험으로 발견했다. 원인 확정 전
  상한 증가·fallback·우회를 넣지 않는다.

### 지금 우선할 Strategy B

- 2026-08-24 사용자 확정 명칭이 현재 정본이다.
  - **Strategy A**: Analyzer는 의미를 만들지 않고 정확한 source/AST 좌표만 산출한다. 그 뒤
    Architect가 좌표를 받아 GWT를 만드는 경로다. 이번 Analyzer 작업은 Architect를 읽거나
    실행·수정하지 않으며 A도 재실험하지 않는다.
  - **Strategy B**: Analyzer가 구조 근거로 의미 대상을 식별하고 제한된 Python 표기로 직접
    산출하는 경로다. 현재 우선순위는 B의 정확도·완전성·동일성을 보존하면서 입력/출력과 호출
    구조를 줄여 속도를 개선하는 것이다.
- 위 현재 명칭에서 과거 `Strategy A Python 표기 비교` 절은 당시 실험 이름을 보존한 역사
  기록일 뿐이다. 현재 A/B 책임 정의로 재해석하거나 다시 실행하지 않는다.
- 사용자가 C-080 성능 구현보다 Strategy B의 의미-파이썬 표기 프롬프트 검증을 먼저 하도록
  우선순위를 바꿨다. Strategy A 312대312 봉인 실험은 재실행·재분석하지 않는다.
- 소수의 실제 SKT 노드만 선정해 원본 구조 근거, Rule, Example과 의미-파이썬 관계식을 비교한다.
- 판정축은 서로 분리한다: Python parse 가능성, 원본과 Rule/Example의 의미 정확성, canonical
  정규화 후 동일성, 동일 입력 반복 결정론. 어느 한 축의 성공을 다른 축이나 제품 합격으로
  승격하지 않는다.
- 먼저 기존 Strategy B 계약·gold·actual의 producer→consumer를 확인하고 입력/hash를 한 번
  봉인한다. baseline 첫 실패 좌표를 얻은 뒤 원인이 프롬프트 지침/출력 빵틀이면 범용 프롬프트만
  수정해 같은 작은 표본으로 역실험한다.
- 파이썬 표기 파싱 성공은 의미 정확도가 아니다. 모델 한계가 확인되면 실패율을 숨기거나 임의
  fallback으로 성공 처리하지 않고 실제 정확도를 그대로 남긴다.

## 9. 2026-08-24 Strategy B 표기 후보 GPU 비교 결론

### 같은 봉인 입력과 실제 GPU 결과

- 첨부 조사 보고서 SHA-256:
  `5211576e934d26ab86a14f9ee085212e66250573abf2c2b6f3add428d7c5f966`.
- actual sealed input SHA-256:
  `95bf1b1d84074d27995aef33733f164ee1507399250c4e2ca9057d35e83c5e82`.
- 직접 의미 정답지 SHA-256:
  `56514d9eadd1cbd571d084cb95319fd62117706d0e66be6ea01264d77b158c57`.
- 모델은 `frentis-ai-model`, response cache off다. 실제 SKT `e700` 4행, `c005` 8행,
  `c010` 26행을 사용했다.
- 2026-08-24 Neo4j 인증과 `SHOW DATABASES`를 다시 확인했다. `dbms/framework/neo4j/system/test`
  5개만 존재하고 모두 online이다. 새 DB를 만들지 않았고 notation 호출은 DB를 사용하지 않았다.

### 후보 제거 결과

- 무예시 whole-document 3회에서 SMT-LIB 0/3, Dhall 0/3, Soufflé Datalog 0/3이다.
  WAT은 parser 3/3이지만 C/E/R 15/17, occurrence 0/9라 탈락했다.
- Python과 CUE는 작은 whole-document에서 parser 3/3으로 생존했다. 확대 whole-document에서 CUE는
  `c005/c010` 모두 3/3과 exact ID를 보존했고 Python은 `c010` 3/3이 12,000 output-token 경계에서
  명시적으로 실패했다. 그러나 실제 제품은 문서 전체를 표기 언어로 재출력하지 않으므로 이 결과를
  Python 표기 자체의 제품 실패로 승격하지 않는다.
- 실제 제품 빵틀인 exact-key function-calling JSON의 C/E `meaning` 한 칸만 바꾼 공정 비교에서
  CUE는 `e700` 3회 모두 0/13 parse였다. Python은 무예시 10/13, statement 금지 보강 뒤
  10/13·11/13·11/13이었다. 첫 고정 실패는 `effects.E10` bare return value이며 `E7` bare assignment
  value가 뒤따랐다.
- 표기 언어 문법만으로 assignment/return/bounded-copy 같은 software semantic ontology가 자동으로
  정해지지 않는다. 추가 prompt 반복으로 이를 숨기지 않고 표기법 경쟁을 종료한다.

### 현재 채택 결정과 다음 producer→consumer

- Python보다 나은 원자 meaning 표기법은 이번 실제 GPU 실험에서 발견되지 않았다. CUE·SMT-LIB·
  Dhall·WAT·Datalog로 교체하지 않는다.
- Python을 모델 자유작성 문자열로 확정하지도 않는다. 다음 책임 슬라이스는
  `Parser/Analyzer structural facts → Analyzer-owned discriminated semantic IR → deterministic Python/Rule/Example projection`이다.
- AST로 이미 확정된 condition operator/operands, effect target/value/ordered arguments, occurrence ID와
  row ownership은 모델에게 재생성시키지 않는다. source는 한 번만 주고 row별 중복 source excerpt를
  제거하되 range/hash/C/E/R ledger를 그대로 보존한다.
- 모델은 AST만으로 닫히지 않는 의미 슬롯과 구조화 sufficiency만 exact schema로 채운다. 임의 상한,
  timeout skip, partial 성공 승격, 의미 fallback, unresolved graph node/label은 사용하지 않는다.
- 상세 작업보고:
  `D:\work\robo\docs\작업보고내용\2026-08-24-구조화-의미표기-GPU-비교.md`
  (SHA-256 `c2ade9c5c2c7f8c8adadcf8946d9cb01c65cdec76549ae847625b4619ef3eb10`).

## 10. 2026-08-24 C-081 구조식 IR 실제 검증

- Parser formal red는 `structural_expression` 0건으로 시작했다. strict IR 2.0 fact는
  condition/case/return/assignment/initializer/update의 grammar role, exact range, scope path,
  recursive ANTLR rule/token만 생산한다. binding/type/linkage/meaning은 금지한다.
- 첫 SKT Analyzer actual red는 legacy 623 대 canonical 533이었다. 첫 실패
  `apascrtu0010t02.c:232`는 `long rc = RC_NRM;` 지역 initializer였고,
  `emitDeclarator`가 legacy `ASSIGNMENT`만 만들고 구조 fact를 만들지 않았다. 45 initializer ×
  target/value 2필드가 90건 차이를 정확히 설명했다.
- Parser는 `statementOrigin=declaration_initializer`와 실제 `initializer_value` grammar tree 한 건을
  생산한다. Analyzer는 source 문자열을 재파싱하지 않고 ordered exact-slice projection 뒤 C grammar를
  language-neutral form/token category로 변환한다. 모르는 grammar rule은 fail-closed다.
- 봉인 SKT 입력 SHA-256은
  `edd512c6478cb31f7a0f396f92ec850659f2720d25c553b8a4f4e42ba330d126`이다.
  Parser artifact SHA-256은
  `94c5bbe98bfbf51b6dabc564c73361c417fb53d32b4308689a8988ab68b6a831`이다.
- 최종 exact-once ledger는 Parser 578 / Analyzer structural 578 / neutral semantic 578,
  unresolved·누락·중복 0이다. neutral ledger 결정론 SHA-256은
  `0071beb86434181140348d6a34b28554131b2bc504c466b437722f4ca3f1df46`이다.
- focused regression: Parser 관련 3계약 pass, Analyzer step2 130 passed/2 skipped,
  sealed SKT replay 1 passed. 기존 `structural_expression` 행 없는 IR 2.0 봉인물도 계속 유효하다.
- 이 결과는 구조 transport/정규화 검증이다. 의미 정확도나 제품 합격으로 승격하지 않는다.
  다음 책임 슬라이스는 neutral IR을 Analyzer-owned discriminated semantic schema가 소비하고
  Python/Rule/Example을 결정론적으로 렌더링하는 것이다.
- 상세 evidence: `evidence/c081-structural-expression-ir-20260824.md`.

## 11. 2026-08-24 C-082 discriminated expression IR 실제 검증

- C-081의 `semantic_expression_facts`는 Step 2 state까지 생산됐지만 downstream consumer가 0이었다.
  또한 `binary/postfix/unary` grammar-normalized tree는 구조를 보존할 뿐 call/member/operator 의미
  종류를 직접 구분하지 않았다. 이 두 사실을 다음 책임 슬라이스의 시작점으로 고정했다.
- Analyzer language-neutral core가 structural token/range만 사용해 expression을
  `reference/literal/group/member/subscript/call/unary/binary/conditional/assignment/cast/sequence`
  variant로 분류한다. C source 정규식 재파싱, binding/type/linkage 추측, 의미 fallback은 없다.
  지원하지 않는 구조·연산자는 `ExpressionSemanticError`로 fail-closed다.
- 봉인 SKT 578건은 모두 기존 CodeNode에 귀속되고 부모 체인으로 46개 routine에 578/578 배정된다.
  orphan은 0이며 별도 unresolved graph node/label은 만들지 않았다.
- 동일 Parser artifact actual 결과는 structural 578 / grammar-neutral 578 / discriminated 578이다.
  top-level variant는 reference 212, binary 173, member 127, call 44, group 10, unary 6,
  literal 4, cast 2이고 누락·중복·첫 실패는 0이다.
- discriminated ledger 결정론 SHA-256은
  `589e09e7cd60b454ecd48f1ac2cf37e393428a6cfead577e8c1f2f4f989ef58f`이다.
  focused Step 2 regression은 131 passed/2 skipped, sealed SKT replay는 1 passed다.
- 이는 구조식의 Analyzer 내부 분류와 exact-once ledger 검증이다. 아직 LLM 의미 입력이나
  Python/Rule/Example 출력에 승격하지 않았고 의미 정확도·제품 합격도 주장하지 않는다.
- 다음 책임 슬라이스는 46개 routine 소유권을 formal contract로 고정하고, 각 semantic slot이
  참조하는 expression fact만 evidence closure에 넣은 뒤 Python/Rule/Example을 결정론적으로
  projection하는 것이다. 전체 함수의 verbose grammar tree를 prompt에 복제하지 않는다.
- 상세 evidence: `evidence/c082-discriminated-expression-ir-20260824.md`.
