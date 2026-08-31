# Analyzer 의미 무손실 작업 현재상태

**Updated**: 2026-08-31
**Status**: Analyzer 전면 리팩터링을 진행 중이다. Summary 책임 경계와 runtime wrapper 12건의 Parser→Analyzer binding은 검증 완료했다. 기존 `framework` DB의 1,503줄 generation은 그대로 유지하며, 다음 첫 좌표는 import 25건이다. 이후 LLM 입출력을 실제 의미 기여도와 오결합 위험 대비 토큰 비용으로 필드별 감사해 불필요한 ID·경로·좌표·해시를 제거하고, 후처리·graph·저장소/SDD를 순서대로 닫는다. 모든 관련 수정 뒤에만 OpenAI 소규모 실제 검증과 GPU 복구 후 약 5만 LOC 최종 성능·전수 회계를 수행한다.
**Role**: 현재 HEAD·환경·보존 WIP·미해결·바로 다음 행동의 유일한 정본

이 파일의 번호 절은 원인과 검증의 시간순 기록이다. 과거 절의 당시 계획·수치·미검증 표시는 삭제하거나
현재화하지 않으며, 맨 위 Status와 가장 마지막 번호 절이 현재 인도 지점과 다음 행동을 소유한다. 삭제된
archive나 과거 실행물을 현재 정본으로 참조하지 않는다.

## 1. 목표와 고정 원칙

- Analyzer의 source→AST→actual prompt→raw/parsed→validator→Neo4j→API/MCP 전체에서 의미를
  조용히 잃지 않게 한다. 숫자 증설·해피패스·증상 패치는 금지한다.
- 원문 요약을 다음 LLM의 권위 입력으로 쓰지 않는다. 큰 입력은 전체 의미 의무를 먼저 확정하고,
  exact source/evidence closure로 나눈 뒤 canonical ID로 exact-once 병합한다.
- ANTLR Parser는 언어별 구문·구조 사실만 생산한다. Analyzer가 내부 language semantic adapter와
  language-neutral resolution core를 소유한다. Analyzer의 언어별 정규식 재파싱·추측은 금지한다.
- Framework 의미 owner는 routine이다. 작은 routine 내부를 별도 의미 단위로 쪼개지 않는다.
- DBMS 의미 owner는 effective AST CodeNode다. Rule 의미는 Strategy B가 대상별 LLM 1회로 생산하고,
  Example은 승인된 Rule에서 LLM 0회로 결정론 투영한다.
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
- 2026-08-28 읽기 전용 gate에서 Neo4j 인증과 `SHOW DATABASES`를 다시 확인했다.
  `neo4j|test|framework|dbms|system`만 존재하고 모두 online이며, DB 쓰기·새 DB 생성은 0회였다.

다음 세션은 작업 시작 전에 Neo4j gate와 Analyzer/Parser status만 다시 확인한다. Catalog·Fabric·Architect는
현재 작업이 직접 필요로 하지 않는 한 검사 범위에도 넣지 않는다.

## 3. 완료되어 다시 하지 않을 일

### 전수조사와 확정 결함

- C-001~C-079 상한·절단·Top-N·sampling·skip/fallback·중간 LLM·summary-of-summary·
  timeout/retry/cache/batch/concurrency 생산자·소비자 조사는 완료했다.
- F-001 `semantic_frame.py` 의미 leaf 600자 상한은 숫자 증설 없이 obligation-derived 분할과
  exact-once merge 계약으로 해결됐다.
- 상세 배선·유입 commit·판정·검증은 2026-08-21 archive와 `docs/audit/semantic-grounding/evidence/`에 있다. 같은 검색을 반복하지 않는다.

### Strategy A Python 표기 비교

동일 4함수·104행·3회, 조건당 312행으로 정식 비교를 완료했다.

| 조건 | Python 표기 성공 | 반복 Python AST 동일 | 보수적 정규화 후 동일 |
|---|---:|---:|---:|
| 이전 모델 + 역사 지시문 | 284/312 (91.03%) | 59/104 | 59/104 |
| 새 모델 + 역사 지시문 | 275/312 (88.14%) | 79/104 | 79/104 |
| 새 모델 + 개선 지시문 | **303/312 (97.12%)** | **91/104** | **100/104** |

- formal run: `_runs/framework/strategy-a/strategy-a-stage1-current-comparable-20260821-r1`
- workers 16, current variant 각 45호출, content retry 0, HTTP/finish/JSON/row closure 45/45.
- evidence: `docs/audit/semantic-grounding/evidence/strategy-a-notation-comparison-20260821.md`.
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
- 과거 상세 원문: `docs/archive/semantic-grounding/history/handovers/12-20260821-current-status-snapshot-pointer.md`.

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
- 상세 evidence: `docs/audit/semantic-grounding/evidence/c081-structural-expression-ir-20260824.md`.

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
- 상세 evidence: `docs/audit/semantic-grounding/evidence/c082-discriminated-expression-ir-20260824.md`.

## 12. 2026-08-24 C-083 routine ownership와 exact expression slot closure

- C-082까지의 578개 discriminated expression은 Step 2 state에 있었지만 LLM semantic slot의
  근거 closure에는 연결되지 않았다. nearest routine parent chain을 정본으로 46개 routine에
  578/578을 exact-once 배정하고, orphan·중복·parent cycle·범위 이탈은 fail-closed로 고정했다.
- 첫 실제 slot 회계는 578개 중 575개만 참조했고 residue 3개였다. 첫 좌표는
  `apascrtu0010t02.c:544`이며, 나머지는 1366, 1833행의 SWITCH subject `rc`였다.
  Parser는 SWITCH `condition`과 CASE `case_value`를 모두 정확히 생산했지만 Analyzer의 CASE guard가
  case value만 연결한 것이 원인이었다. source keyword 재파싱 없이 Parser parent edge와 role로
  두 fact를 같은 guard closure에 연결한 뒤 578/578, residue 0이 되었다.
- canonical EvidenceFact에는 exact line/character range와 전체 discriminated tree를 보존한다.
  모델 view는 해당 slot에 필요한 fact만 request-local `Q…` key로 연결하고 값은
  `[role, compact tagged tree]`만 전달한다. `Q…`가 meaning/missing_context에 누출되면 해당 slot을
  insufficient로 격리한다.
- 동일 46개 routine summary 입력의 추정 token은 기존 175,447에서 196,514로 +21,066
  (+12.01%)이다. 최초 full canonical 전개 +104.11%는 폐기했다. 최대 증가는
  `c000_main_proc` +2,214 추정 token이다. 절단·건수 상한·skip·fallback은 넣지 않았다.
- actual ledger는 structural/grammar-neutral/discriminated 578/578/578, routine 46,
  expression slot closure 578/578이다. resolved ledger SHA-256은
  `a365bf42bdca47e754ba0c3f52be733272978ee0ac1f1f9f95839e4e6ce45db6`이다.
- 집중 회귀는 40 passed와 6 subtests passed, staged-only 전체 회귀는
  1,278 passed/7 skipped/367 subtests passed다. prompt hash 16개 byte-identical이며
  `semantic_frame_contract.md` SHA-256은
  `501bfacb3542d2fbfb6271dab35c3e718a678b4c6fb892c5feab6dcbdfb755c4`이다.
- 이 슬라이스는 LLM semantic input의 구조·소유권·누락을 닫은 것이다. 기존 deterministic Rule의
  CASE 조건은 아직 `case_value`만 렌더링하여 `rc == RC_NFD`가 아니라 `RC_NFD`로 남으므로
  Rule/Python 의미 정확도나 제품 합격으로 승격하지 않는다. 다음 책임 슬라이스에서 canonical
  expression IR로 Rule/Python/Example을 결정론 투영한다.
- 상세 evidence: `docs/audit/semantic-grounding/evidence/c083-expression-slot-closure-20260824.md`.

## 13. 2026-08-24 C-084 Rule condition expression projection

- 확정 원인은 Parser 누락이 아니라 Analyzer의 기존 Rule evidence가 CASE label만 소비하여 SWITCH
  selector와의 관계를 잃은 것이다. 실제 `apascrtu0010t02.c:544/546`에서 Parser는 `rc`와
  `RC_NFD` 및 parent edge를 모두 생산했고, 기존 Rule은 `F("RC_NFD")`만 만들었다.
- Analyzer adapter가 resolved expression role과 Parser parent edge로 `predicate`/`case_match`
  relation을 만들고, language-neutral core가 exact source atom과 operator shell을 결정론 렌더링한다.
  source 정규식 재파싱·binding/type/linkage 추측·프로젝트 이름 특화는 없다.
- 봉인 actual은 expression 578/578/578, condition relation 192/192, Rule 362, Example 89다.
  baseline 대비 조건 253개가 바뀌었고 첫 좌표는 300행이다. `R546`은
  `F("rc") == F("RC_NFD")`로 닫혔으며, 효과가 있는 `R549`는 Rule 조건과 Example `given`, Rule/Example
  `then` 동일성을 직접 판정했다.
- bundle이 있는 경로에서 relation/expression 누락은 fail-closed한다. audit schema v2가 embedded
  condition closure와 결과를 함께 보존하고 replay 시 재물질화하여 불일치를 거부한다. 기존 v1 audit은
  읽기 호환한다.
- 집중 회귀는 113 passed/17 subtests, actual 포함 집중 검증은 89 passed/17 subtests,
  staged-only 전체 회귀는 1,285 passed/7 skipped/367 subtests다. 전체 SKT 23파일 성능 run은 관련
  개선 종료 시 한 번만 수행한다.
- 이 결과는 Rule/Example 구조 의미 개선이며 LLM business meaning이나 제품 전체 합격으로 승격하지
  않는다. 상세 evidence: `docs/audit/semantic-grounding/evidence/c084-rule-condition-expression-projection-20260824.md`.

## 14. 2026-08-24 C-085 call-occurrence semantic closure

- 원인은 Parser 누락이 아니라 Analyzer Step 6가 exact call occurrence를 `(line, callee)`로
  축약하고, branch consumer가 condition source를 이름 정규식으로 다시 읽으며,
  semantic-frame가 `(line, callee)`를 identity로 사용한 것이었다. 같은 줄의 condition/body가
  같은 callee를 호출하면 body occurrence가 사라지는 formal red로 인과를 닫았다.
- Parser의 call/callee/receiver/argument range와 source fragment, scope path, grammar rule,
  presence를 보존하고 Analyzer final resolution ledger와 ordered call ID로 exact join한다.
  중복 구조 필드가 하나라도 다르면 fail-closed하며 source 재파싱이나 이름 추측은 없다.
- sealed SKT 한 파일 actual은 Parser/해석/context/semantic-frame가 모두 1,157/1,157이며,
  condition 183, effect 974이다. Rule effect 974/974, processing slot 974/974,
  condition-to-guard slot 183/183을 unique occurrence ID로 보존했고 deterministic replay도
  동일했다. 첫 누락·중복 좌표는 없다.
- partial upload는 external resolution으로 남고 unresolved/dynamic 상태도 occurrence ledger에
  보존한다. unresolved graph node/label은 만들지 않았다. Python 표기나 이 구조 검증을
  business meaning 정확도 또는 제품 합격으로 승격하지 않는다.
- sparse prompt projection 후 sealed 44-routine summary payload는 2,382,537 bytes,
  최대 routine payload는 273,144 bytes다. 이는 임의 cap·skip 없이 얻은 정확성 baseline이며,
  다음 성능 개선은 dependency projection과 중복 제거로만 수행한다.
- focused 회귀는 149 passed/20 subtests, staged blob hash 일치가 확인된 staged-only 전체
  회귀는 1,299 passed/8 skipped/367 subtests다. 금지된 Strategy A progressive 재실행은
  명시적으로 제외했다.
- 전체 SKT 23파일 run은 아직 수행하지 않았다. call argument/effect 표현과 prompt 효율 관련
  변경을 모두 닫은 마지막에 정확도·성능 전수 회계를 한 번 수행한다.
- 상세 evidence: `docs/audit/semantic-grounding/evidence/c085-call-occurrence-semantic-closure-20260824.md`.

## 15. 2026-08-24 C-086 call effect source order와 ordered arguments

- C-085 이후에도 같은 줄 call effect가 source range가 아니라 hash ID로 정렬될 수 있었고 최종
  `Rule.then`은 `call("target")`만 남겨 서로 다른 인자를 표시하지 못했다.
- occurrence effect를 `(routine line, exact call range offset, canonical ID)`로 정렬하고
  `call("target", F("receiver") | None, [F("argument0"), ...])` 고정 Python leaf로
  물질화한다. argument 배열은 Parser grammar order이며 source fragment를 재파싱하지 않는다.
- sealed SKT 한 파일에서 974개 call effect가 모두 확장 표기, Python parse, exact source-order
  ledger, Rule↔Example 동일성을 통과했다. `R232`의 네 `pfm_try`는 서로 다른 nested-call
  argument를 보존한다.
- 이는 source order이며 언어별 runtime argument evaluation order를 추측한 것이 아니다.
  occurrence metadata가 없는 legacy/DBMS `call("target")`은 호환 유지한다.
- focused 회귀는 168 passed/20 subtests, staged blob hash 일치가 확인된 staged-only 전체
  회귀는 1,301 passed/8 skipped/367 subtests다. 금지된 Strategy A progressive 재실행은
  명시적으로 제외했다.
- 전체 SKT 23파일 run은 아직 수행하지 않았다. 상세 evidence:
  `docs/audit/semantic-grounding/evidence/c086-call-effect-order-arguments-20260824.md`.

## 16. 2026-08-25 Analyzer 전면 진단과 목표 구조 결정

- 이번 단계는 제품 구현이 아니라 현재 Analyzer의 모델 호출·입출력·producer→consumer·KV cache·
  retry·embedding·파일 구조를 읽기 전용으로 전수 대조한 결정 단계다. 제품 코드·프롬프트·Neo4j
  데이터는 변경하지 않았고 전체 SKT 재분석도 수행하지 않았다.
- 시작 안전 게이트에서 Neo4j 인증과 `SHOW DATABASES`를 확인했다. 존재하는 데이터베이스는
  `dbms/framework/neo4j/system/test`뿐이며 모두 online이었다. 새 DB와 임시 DB는 만들지 않았다.
- Parser는 구조 사실만 생산하고, Analyzer 언어별 adapter가 의미를 해석한 뒤 언어 중립 core가
  binding/type/linkage/call resolution을 최종 결정한다. source regex 재파싱·이름 추측·unresolved
  제품 node는 금지하며 partial upload 밖 정의는 external로 처리한다.
- Strategy A 제품 노선은 폐기하고 Strategy B를 Analyzer Rule 의미 노선으로 정했다. A의 좌표·분기·
  exact-once 장부는 B 내부 기반으로 보존한다. Rule은 canonical restricted Python 구조를 사용하되
  Python parse를 의미 정확도나 제품 합격으로 승격하지 않는다. Example은 같은 검증 Rule에서
  결정론 투영한다.
- `Summary / FunctionTableUsage / Rule` 3분할은 유지한다. 세 family는 각각 한 번만 의미를 생산하고
  Table·Rule은 Summary 결과나 warm-up을 기다리지 않는다. 각 family의 첫 실제 요청만 자기 cache
  warm-up으로 사용하며 더미 warm-up·같은 concern 재질문·숨은 judge/repair는 금지한다.
- 현재 성능 원인은 의미 evidence 자체가 아니라 동적 slot schema, 긴 내부 ID와 중복 fact,
  source-closure JSON, 전체 원문의 concern 초과 반복, DBMS 범위 중복, 요청 크기를 무시한 동시성,
  이분 재질의와 겹친 retry다. 속도는 skip·top-N·임의 cap이 아니라 compact 무손실 projection,
  token/KV 기반 admission, exact-once source ownership, retry 한 층으로 개선한다.
- 저장소 정책도 전면 수정 대상이다. `specs`는 현행 SDD와 실제 경계 contract, 활성 변경 문서,
  이 `CURRENT_STATUS.md`만 허용하고 run/evidence/experiment/history/script/source 복제는 제품 밖으로
  이관한다. 헌법도 기존 내용을 그대로 권위로 유지하지 않고, 새 SDD 대응표를 만든 뒤 변하지 않는
  공통 원칙 1~2쪽으로 재작성한다. 기존 문서를 먼저 일괄 삭제하지 않는다.
- 상세 결정 보고서:
  `D:/work/robo/docs/작업보고내용/2026-08-25-Analyzer-전면진단-및-목표구조-결정.html`
  (SHA-256 `0fd6af1e91b3f0e115adee3bc23c33b82812fae525848ce2bf95589b8bb811a6`,
  92,997 bytes, UTF-8, section/table/internal-link 균형 검증 통과).
- 이 진단을 위해 잘못 `specs/.../evidence`에 만들었던 별도 C-088 결정 markdown은 위 HTML에
  내용을 통합한 뒤 제거했다. 기존 사용자 evidence·experiment·보호 WIP·출처 미확정 스크립트는
  이동·삭제·수정하지 않았다.

## 17. 2026-08-26 목표 SDD와 최종 설명서 감사 종결본

- 이번 단계도 제품 구현이 아니라 사용자 피드백과 두 리서치를 반영해 Parser→Analyzer→기존 Neo4j
  공개의 최종 목표 구조를 SDD로 고정한 문서 설계 단계다. 제품 코드·프롬프트 runtime·Neo4j 데이터는
  변경하지 않았고, 전체 SKT 재분석·속도 측정·Strategy A 312대312 재실행도 하지 않았다.
- 현재 실행 가능한 목표 wire는 두 개다.
  - `contracts/parser-structural-facts-v2.md`: 14,229 bytes,
    SHA-256 `8e28390cf9ae2f50634f5273e516ff6ebc250c83924cb2327309c52a17499701`
  - `contracts/analyzer-meaning-and-publication-v2.md`: 42,903 bytes,
    SHA-256 `4cfd4cdca740d8dad3470eb5fb3113c8db4366ef788dd09285dce57b4690446a`
- 기존 v1 계약은 `docs/archive/semantic-grounding/contracts/`로 이동해
  `Historical / Non-executable` 경고를 붙였다. 현재 목표와 충돌하는 old scopePath/slot/Architect/
  Rule LLM 0/CALL occurrence graph 조항은 구현 정본으로 실행하지 않는다.
- 요구·계획·실행 순서 후보는 다음 hash로 동기화했다.
  - `README.md`: SHA-256 `9e64f61417f22d168cf5ee0f31d745ac6cf7a4246a77cd05021fa5b13e01f089`
  - `spec.md`: SHA-256 `2c956f03d8dc466f8e6e2ddebe91a9d9603c2902348749fb71ce6f794ef4b688`
  - `plan.md`: SHA-256 `5ca151bbee4cc427c92cbbd83f9ff77a37b616b1f5fd42408a795932fe1b06a9`
  - `tasks.md`: SHA-256 `b55d9188192af9f24f5cb641c9d9784b07af830be1627fd2e5420f415bbc9e35`
- 사람용 현재 구현 설명서는
  `D:/work/robo/docs/작업보고내용/2026-08-26-Analyzer-최종-구조-고해성사.html`이다. 과거 대형 목표 문서는
  2026-08-29에 현재 코드 기준 44,641 bytes, 14개 순차 단계, 7개 실제 LLM 경계로 전면 교체됐으며 SHA-256은
  `ea6d7d78e785f0277a59766a3aaac080d48703f2fd3acfd77bc4d4b2d5c24a68`다. HTML 여닫는 구조와 필수 함수·경계,
  과거 가짜 경로 부재를 기계 검사했다. 이 hash는 정적 구현 설명의 상태이지 실제 SKT 제품 합격 증거가 아니다.
- 이 hash 묶음은 목표 설계의 단일 전수 감사 지적을 모두 닫은 사용자 피드백 대기본이며 제품 합격 hash가
  아니다. 제품 구현·약 5만 LOC SKT cold run·자동 회귀·exact-once 전수 회계·봉인 직접 의미 판정·
  commit/push는 `T131-N01~N17` 실행 때 별도다.
- Architect와 Catalog/Fabric 사용자 WIP, modified `evidence_ir.py`, 출처 미확정 untracked script는
  읽기·실행·수정·이동·stage하지 않았다. 새 database와 임시 database도 만들지 않았다.

## 18. 2026-08-27 GPU 비가용 상태의 정적 리팩터링 진행본

- GPU 서버가 비가용이므로 LLM 호출, SKT 재분석, Neo4j 쓰기, 전체 성능 측정은 수행하지 않았다.
  이 절의 판정은 producer→consumer 정적 추적과 결정론적 단위 회귀에만 근거한다.
- Summary는 같은 모델 응답에서 세부 slot을 먼저 채우고 target 전체 `summary`와 `stereotype`을
  정확히 한 번만 생성한다. slot 의미를 줄바꿈으로 합친 문장을 최종 Summary로 승격하던 경로와,
  같은 결손을 `questions`/`missing_context`/graph node로 중복 공개하던 경로를 제거했다.
- 모델이 받는 함수 원문은 hash와 JSON wrapper를 반복하지 않고 `절대 줄번호 | 원문`으로 전달한다.
  canonical 내부 frame의 ID·파일·정확 범위·resolution ledger는 보존하되 모델 view에서는 의미를
  바꿀 수 없는 occurrence ID, resolution outcome, 항상 active인 presence 등을 전달하지 않는다.
- 모델 slot 출력은 `meaning`과 `status` 두 필드만 소유한다. 미완료 진단 문장을 모델이 별도로
  생성하지 않으며 Analyzer가 slot 상태와 보존된 구조 근거에서 내부 감사 사유를 결정론 생성한다.
- TABLE/COLUMN의 상세 의미 계약이 파일에는 있었지만 실제 YAML에서 참조되지 않던 consumer 단절을
  확인해 Framework/DBMS 최종 table-description 요청의 공통 정적 계약으로 다시 연결했다. Table
  source 우선순위·컬럼 소유권·물리명 추측 금지는 유지하고 wire는 exact slot map 하나로 통일했다.
- unknown 프로젝트 Scan 도구의 파일 200개·grep 80건·routine 50개 무표식 절단을
  `offset/total/next_offset` 페이지 계약으로 바꿨다. unknown external-format 학습도 앞 3개 학습
  파일·앞 5개 negative만 모델 검증 도구에 보이던 경로를 제거하고 모든 학습/negative를 도구에
  연결했다. 원문은 페이지로 읽으므로 전체를 프롬프트에 밀어 넣지 않는다. held-out 파일은 계속
  모델에 숨겨 독립 검증한다. 알려진 SKILL은 기존처럼 결정론 검증으로 선택되어 LLM 호출 0이다.
- 현재 production 생성 호출은 unknown Scan agent, unknown external-format agent, unmatched custom
  table residual pattern, 함수 Summary, Rule atom 업무 의미, 직접 Table 사용 의미, 최종 TABLE/COLUMN
  설명이다. Rule atom 의미는 Framework 함수당 최대 1회, DBMS 기존 batch당 최대 1회이며 Summary·
  Table과 비차단 병렬 실행한다. 같은 Rule의 judge/repair/두 의미 재질의는 없다. 새 정적 callsite
  inventory가 이 승인 목록 밖의 모델 호출 추가를 실패시킨다.
- Parser/Analyzer가 만든 exact condition/effect atom과 행 결합은 그대로 보존하고 모델은 한 응답에서
  각 atom의 `meaning/status`와 각 행의 `business_rule|implementation_detail|insufficient`만 한 번 채운다.
  모델은 Python·Rule·Example·Summary·Table·근거·질문을 작성하지 않는다. Analyzer가 구조를 결합해
  `business_rule`이면서 필요한 atom이 모두 `sufficient`인 행만 restricted Python 업무 조건/효과와 제품
  Rule로 공개하고 같은 값에서 Example을 결정론 생성한다. 구현 세부는 `excluded`, 의미 불충분은
  `unverified`, 호출 실패는 `unavailable`로 행마다 정확히 한 번 회계한다. Python parse와 상태는 제품
  의미 합격 판정이 아니다.
- Rule 저장 필드는 `anchor_line`, 구조 조건/효과, 업무 조건/효과만 남겼다. 공개 Rule은 모두 충분한 업무
  의미가 확정됐으므로 반복 상수였던 `business_meaning_status=sufficient`도 제거했다. 빈 `nl`,
  복원 가능한 `rule_id/owner_signature`, Example 고정 `kind`, Rule/Example 관계의 중복 occurrence/anchor
  속성을 제거했다. 검색 응답의 rule id·owner signature·`kind=satisfy`는 anchor와 HAS_RULE owner에서
  결정론 복원한다. `AFFECTS_TABLE`은 확정 TABLE만 만들며 출처는 `SCANNER/ANALYZER_DERIVED` 둘뿐이다.
- spec 안에 섞여 있던 비추적 실행 산출물 `_runs` 8,713파일(656,057,189 bytes)과 `_artifacts`
  164파일(9,563,736 bytes)은 내용을 다시 읽거나 재분석하지 않고 Analyzer 루트의 같은 이름 디렉터리로
  이동했다. 비추적 notation 실험 9파일도 `_artifacts/semantic-notation-experiments`로 이동했다.
  audit 47파일은 `docs/audit/semantic-grounding`, archive 22파일과 이전 SDD 초안은
  `docs/archive/semantic-grounding`, seal script는 `scripts`로 이동했다. `specs/131`은 현재 SDD 7파일과
  삭제 정책 때문에 남은 비추적 `__pycache__` 1파일, 총 8파일이다.
- residual pattern의 30-case/3-round 정책은 앞선 사용자 결정대로 이번 슬라이스에서 변경하지 않았다.
  이는 미해결을 성공으로 승격하지 않고 명시적으로 남기지만, 더 나은 탐색 정책은 별도 리서치·설계
  대상이다.
- 실제·Neo4j 통합·봉인 Strategy-A 테스트를 제외한 전체 정적 회귀는 `1,277 passed`, `5 skipped`,
  `351 subtests passed`다. prompt hash `17`개 byte-identical 검사와 `git diff --check`를 통과했다.
- 다음 경계는 GPU 복구 후 작은 SKT 좌표의 Rule 업무 의미 직접 판정이다. 여기서 의미와 동일성이
  닫힌 뒤에만 약 5만 LOC SKT 성능 측정과 마지막 전체 재분석·전수 회계를 한 번 수행한다. 현재는
  GPU 비가용 사용자 지시로 재분석·LLM 호출·Neo4j 쓰기·제품 합격 선언을 하지 않는다.

## 19. 2026-08-27 헌법 2.0.0 정합

- GPU·LLM·SKT 재분석·Neo4j 쓰기 없이 현행 spec/contracts와 기존 헌법을 정적으로 대조했다.
- 기존 헌법의 `매 실행 전체 graph 삭제` 전제와 Analyzer 범위 밖 Architect 제품 경계를 폐기했다.
- Parser 구조 사실과 Analyzer 의미 소유권, exact-once, 최소 비중복 모델 입력, 승인된 생성 호출,
  Strategy B, 임의 제한 금지, 기존 DB의 atomic generation 공개, 3층 검증, 보호 WIP를 177줄의
  헌법 2.0.0으로 다시 고정했다.
- 새 헌법 SHA-256은 `ff106d09cb5198f5144b59abdaf431de454403ddd5f65d9fc9f4458d5031c33c`다.
- 이 변경은 목표 계약 정합이며 atomic generation 제품 구현 완료나 실제 제품 검증을 뜻하지 않는다.

## 20. 2026-08-27 IR 2.0 연결 분류와 atomic publication 정적 진행

- GPU 서버 중단 지시에 따라 LLM 호출, SKT 재분석, Neo4j write/integration 실행은 하지 않았다.
- 실제 producer→consumer 추적 결과, `framework_runtime_functions`는 scanning에서 검증·전달됐지만
  call classifier 입구에서 즉시 폐기되고 있었다. 그 결과 framework 호출은 모두 external로 남고
  `dropped_framework`는 항상 0이었다.
- Analyzer 중립 core가 저장소 소유 framework skill 계약만 `framework_provided/excluded`로 처리하도록
  연결했다. workspace routine과 glob 하나라도 충돌하면 전체 분류를 실패시키며, source 재파싱이나
  이름 기반 내부 함수 연결은 추가하지 않았다. unmatched Scan Agent 제안은 final linkage 권한으로
  사용하지 않는다.
- 당시 C 외 Java·Python·SQL semantic adapter와 standard-library identity registry는 없었다.
  Java·Python Parser producer와 Analyzer 후보/최종 판정 경계의 이후 진행은 §36이 현재 정본이며,
  근거 없는 이름 추측을 하지 않았으므로 B01 전체는 계속 미완료다.
- generation publisher는 scope publication version과 이전 pointer를 CAS로 잠근 뒤 모든 제품 node의
  실제 primitive property와 모든 relationship endpoint/property를 다시 읽는다. 이전 active generation은
  삭제하지 않고 superseded로 보존하며, 전환 후 active generation이 새 generation 하나뿐인지 확인한다.
- call/import occurrence 전체는 run closure의 exact-once ledger로 유지하되 제품 graph node·relationship,
  search payload, API semantic packet에서는 제거했다. 이를 위한 옛 storage encoder/decoder도 소비처 0을
  확인하고 제거했다.
- 정적 집중 회귀는 연결/skill 영역 `155 passed, 19 subtests passed`, graph 단위 영역
  `73 passed, 8 subtests passed`, occurrence 분리 포함 집중 묶음 `193 passed, 2 deselected,
  4 subtests passed`다. 실제 Neo4j rollback proof 파일은 새 계약으로 정적 갱신했지만 실행하지 않았다.
- ProductWriteBatch의 required operation/property-owner expected map과 control-state hash 전체는 아직
  구현되지 않았으므로 B02와 제품 합격은 미완료다.

## 21. 2026-08-27 함수 의미 제품 property와 run artifact 분리

- GPU/LLM/SKT 재분석과 Neo4j write/integration 없이 함수 Summary producer부터 graph publisher,
  검색/API consumer까지 정적으로 추적했다.
- 종전 CodeNode에는 `analysis_status`, 전체 `analysis_findings_json`, 전체
  `semantic_frame_json/schema`, Rule 품질 플래그가 함께 복제됐다. finding은 이미 append-only
  `analysis-findings.jsonl`에 기록됐고 full semantic frame은 제품 표시·검색에 필수인 값이 아니어서,
  제품 그래프와 run artifact 사이에 같은 의미가 중복 저장되는 원인이었다.
- 함수 CodeNode 제품 patch를 `summary`, `stereotype`, `meaning_status`,
  `missing_context_count`, 조건부 `missing_context_artifact_hash`로 닫았다. `sufficient`는 의미 두 필드와
  count 0, `partial`은 의미 두 필드와 count/hash, `insufficient`는 의미 필드 부재와 count/hash를
  강제한다. 비함수 CodeNode에는 이 property를 허용하지 않는다.
- 상세 missing-context와 complete semantic-frame ledger는 SHA-256 내용 주소 방식의 run artifact로
  기록한다. 모델은 해당 hash·상세 목록을 입력받거나 출력하지 않는다. quality finding도 CodeNode
  `extra`와 제품 graph에 다시 복제하지 않는다.
- 모순된 stereotype을 빈 값으로 만들어 summary/stereotype 결합 존재 규칙을 깨던 경로는 닫힌
  ontology 값 `Unclassified`와 명시 finding으로 바꿨다. 의미를 추측해 다른 역할로 바꾸지는 않는다.
- generation closure v6는 graph에서 제거된 semantic slot JSON을 다시 읽지 않고 모든 routine의
  함수 의미 상태·count/hash presence, 모든 TABLE/COLUMN 의미 상태, 모든 Module 함수-summary rollup을
  실제 graph property에서 검증한다. 검색/API의 source-free view도 semantic frame을 요구하지 않는다.
- 집중 회귀는 graph·Step 8 묶음 `114 passed`, `12 subtests passed`; 실제·Neo4j integration·봉인
  Strategy A를 제외한 전체 정적 회귀는 `1,283 passed`, `5 skipped`, `351 subtests passed`다.
  `git diff --check`도 통과했다.
- ProductWriteBatch/property-owner 전수 구현은 아직 끝나지 않았으므로 B02와 제품 합격은 계속
  미완료다.

## 22. 2026-08-27 TABLE/COLUMN 의미와 Module rollup 최소 제품 투영

- TABLE/COLUMN 제품 node에서 `semantic_frame_schema/json`, 상세 missing-context 목록, 옛
  `semantic_evidence_status`를 제거했다. 제품에는 논리명·분석 설명과 `meaning_status`, missing-context
  count/hash만 남기며 complete frame과 상세 사유는 SHA-256 내용 주소 run artifact에 둔다.
- 직접 사용이 없는 DDL TABLE/COLUMN도 건너뛰지 않고 `missing_usage_context` 원인의
  `insufficient`로 정확히 한 번 회계한다. 모델 호출 실패는 finding을 기록한 뒤 generation을 실패시키며
  이전 값을 정상 결과처럼 공개하지 않는다.
- 종전 Module 단계는 Parser가 확정한 `CodeNode.owner_id` 대신 파일·라인 범위로 소속을 다시 계산했고,
  DBMS 중간 구문 node까지 semantic frame으로 재합성해 Module summary·stereotype·logical_name을
  만들었다. 이 우회 경로와 해당 제품 property를 제거했다.
- 현재 Module 단계는 routine만 `owner_id`로 곧바로 묶고 함수 ID 순으로 정렬한 뒤, 검증된 함수 summary
  bytes를 바꾸지 않고 같은 길이의 `summary_rollup_function_ids`와 `summary_rollup_summaries`에 저장한다.
  한 함수라도 summary가 없으면 부분 rollup을 공개하지 않는다. 별도 LLM 호출과 graph stream 재발신도 없다.
- 구조 Module upsert와 의미 rollup patch를 분리했다. rollup patch는 target 전부 존재를 guard한 뒤 해당
  두 property만 쓰며, generation closure v6가 함수 owner·ID 순서·summary bytes와 다시 대조한 후에만
  active pointer 공개를 허용한다.
- GPU/LLM/SKT 재분석과 Neo4j write/integration은 수행하지 않았다. 실제 제품 합격은 GPU 복구 뒤 정해진
  점진 검증과 마지막 전체 SKT 성능 측정까지 계속 미검증이다.

## 23. 2026-08-28 실제 유료 검증 범위와 partial-upload 선택 확정

- Neo4j 인증과 `SHOW DATABASES`를 다시 확인했다. `dbms/framework/neo4j/system/test`만 존재하고
  모두 online이다. 새 database와 임시 database를 만들지 않았고 graph write도 수행하지 않았다.
- 실제 생성 검증은 SKT source 2파일, 합계 1,503줄로 고정했다.
  - `zapamcom13010.c`: 1,259줄, source SHA-256
    `3b7348c64234fd0f7c468fca117d026c081f33b21882a5e7c8d6d6d6a9c4c1ee`
  - `zapamcom10150.c`: 244줄, source SHA-256
    `9be7bee3a4af8a7ee420110bf698cac45ca6a12b711a979e9fa691bd7a3fb834`
- 13010의 corpus 직접 caller `apascrtu0010t02.c`는 4,109줄, 직접 callee `zngmmcap50000.c`는
  2,087줄이므로 어느 쪽도 1,000~2,000줄 총 제한에 함께 넣을 수 없다. 두 선택 파일이 서로 직접
  호출한다고 가장하지 않는다.
- `zapamcom10150.c`는 선택 범위 밖 `zngmmcap50000`을 호출하므로, 업로드되지 않은 사용자 정의가
  정상 `external`로 정확히 한 번 종료되고 제품 `CALLS`가 생기지 않는지 검증하는 짧은 반례다.
- 실행 프로필/API의 `selected_source_ids`가 source↔AST closure, scanner inventory, scan-agent file
  visibility까지 같은 두 파일로 제한한다. preflight는 source/AST/diagnostic 각 2건, 누락·중복 0으로
  통과했다. 선택 밖 source는 모델·scan 입력에 들어가지 않는다.
- 결정론 `code_structure` ProductWriteBatch는 봉인 closure에서 Package·File·Module·CodeNode와
  PARENT_OF·NEXT를 만들며 source 재파싱 없이 operation/property/identity를 exact-once 검증한다.
  partial selection과 구조 투영 집중 회귀는 68 tests, 실패 0이다.
- OpenAI 생성 요청, SKT 의미 재분석, Neo4j write는 아직 0회다. 코드·계약·집중 회귀가 모두 닫힌 뒤
  `gpt-5.4-mini`로 위 1,503줄을 한 번만 실행한다. 실행 직전 파일 2개·1,503줄·예상 logical invocation
  수를 먼저 보고한다.
- 약 5만 LOC 전체 재분석은 수행하지 않는다. 따라서 선택 범위의 단계별 시간·token·cache는 측정하지만
  제품 규모 처리 속도는 미검증이며, 1,503줄 결과로 일반화하지 않는다.

## 24. 2026-08-28 DDL 제품 구조 batch와 FK 누락 원인

- 기존 DDL producer는 `fk_edges`를 만들고 옛 Neo4j writer가 `FK`를 저장했지만, 새 atomic publication의
  닫힌 제품 관계 목록에는 `FK`가 빠져 있었다. 그대로 전환하면 외래키가 조용히 사라지는 확정 결함이다.
- `data_schema_structure` ProductWriteBatch가 Table·Column·HAS_COLUMN과 명시적 FK를 한 책임으로 만든다.
  SQL 원문을 다시 읽거나 정규식으로 추측하지 않고 Step 1의 검증된 DDL 객체만 소비한다.
- 모든 Column은 HAS_COLUMN owner 하나를 가져야 하고, 모든 endpoint는 같은 batch 안에 있어야 한다.
  같은 table pair의 여러 FK column pair는 같은 순서의 세 배열로 손실 없이 보존한다.
- `is_candidate=true`인 추론 FK는 검증된 제품 관계로 승격하지 않고 DB write 전에 fail-closed한다.
  raw DDL comment와 view definition은 현재 제품 소비자가 없어 run/state에 남기고 graph에는 복제하지 않는다.

## 25. 2026-08-28 호출 occurrence와 제품 CALLS 분리 batch

- `call_links` ProductWriteBatch는 완전한 `call_resolution_ledger`의 `internal` 행에서 distinct
  caller→callee routine pair만 투영한다. 같은 pair의 반복 occurrence는 장부에는 모두 남고 제품 관계는
  하나다.
- `external|standard_library|framework_provided|configuration_dependent|ambiguous|dynamic|inactive|
  unsupported` 행은 제품 operation을 만들지 않는다. 별도 stub·unresolved node/label도 만들지 않는다.
- 기존 `calls_edges`와 ledger의 internal pair 집합이 다르거나 endpoint가 사용자 routine이 아니면
  Neo4j write 전에 fail-closed한다. CALLS가 internal이라는 사실과 재귀 여부는 type/endpoints에서
  복원되므로 관계 property로 중복 저장하지 않는다.

## 26. 2026-08-28 직접 data access 구조와 접근별 의미 소유권 분리

- 실제 producer→consumer 추적에서 기존 `TableInsightItem`은 `(CodeNode, Table)` 하나에 `READ_WRITE`와
  문장 하나를 만들지만 제품 계약은 `(function, physical table, access mode)`마다 FunctionTableUsage를
  요구한다는 불일치를 확정했다. 같은 table을 읽고 쓰는 함수의 문장을 READS와 WRITES 양쪽에 복사하면
  접근별 의미가 왜곡되므로 구현하지 않았다.
- ProductWriteBatch에 기존 relationship의 특정 property만 후속 단계가 소유할 수 있는
  `RelationshipPropertySetOperation`을 추가했다. plan은 관계 생성자가 같은 generation 안에 존재하는지,
  property key 소유 단계가 겹치지 않는지, 쓰기·read-back·operation ledger가 exact-once인지 검증한다.
- `data_access_links` batch는 검증된 사용자 routine→physical Table의 직접 READS/WRITES만 만들고 실제
  edge에 존재하는 `data_operations`만 보존한다. 반복 발생의 file/line은 run ledger에 남고 제품 관계에는
  복제하지 않는다. DML 종류가 비어 있으면 relation에서 SELECT/UPDATE를 추측하지 않고 빈 목록으로
  명시한다.
- unresolved ObjectReference target과 routine이 아닌 source는 조용히 거르거나 상위 함수로 임의 승격하지
  않고 DB write 전에 fail-closed한다. DBMS statement→owner routine 정규화와 unresolved occurrence
  exact-once ledger가 아직 새 계약으로 연결되지 않았다는 실제 producer gap이므로 후속 작업 대상이다.
- `used_column_ids`와 FunctionTableUsage 의미·상태·count/hash는 현재 합쳐진 TableInsight 계약을 접근별
  required key로 바꾼 뒤 `function_meanings` relationship patch가 소유한다. 그 전에는 제품값을 만들지
  않는다.
- 관계 property patch, 구조 READS/WRITES, 기존 Code/DDL/CALLS batch를 합친 집중 회귀는 58 tests,
  실패 0이다. LLM 호출, SKT 재분석, Neo4j write는 수행하지 않았다.

## 27. 2026-08-28 함수 의미 제품 patch 단일 계약화

- 기존 함수 의미 presence 검증이 legacy Cypher writer의 `to_summary_item` 안에만 있어 새 atomic
  publication 경로가 같은 규칙을 독립적으로 복제해야 하는 구조였다. 검증과 최소 제품 property 생성을
  `function_meaning_product_properties` 하나로 옮기고 legacy writer와 새 batch가 함께 소비하게 했다.
- `function_meanings` ProductWriteBatch는 분석 closure의 모든 routine을 stable ID 순서로 정확히 한 번
  처리한다. `sufficient|partial|insufficient`별 `summary`, `stereotype`, `meaning_status`,
  `missing_context_count`, 조건부 artifact hash의 결합 존재 규칙을 DB를 열기 전에 검증한다.
- statement/control CodeNode는 함수 의미 property를 받지 않는다. 중복·빈 CodeNode ID, summary와
  stereotype의 불일치, 잘못된 status/count/hash는 부분 patch 없이 fail-closed한다.
- Code/DDL/CALLS/READS-WRITES 구조 batch, 관계 patch writer, 함수 의미 batch와 기존 함수 의미 소비자를
  합친 집중 회귀는 69 tests, 실패 0이다. LLM 호출, SKT 재분석, Neo4j write는 수행하지 않았다.

## 28. 2026-08-28 TABLE/COLUMN 의미와 Module rollup 제품 batch

- `table_meanings` ProductWriteBatch는 generation의 모든 Table·Column 의미를 한 번씩 patch한다.
  `sufficient`는 논리명·설명과 count 0만, `insufficient`는 의미 문장 없이 양수 count와 artifact hash만
  허용한다. 기존 validator가 `sufficient`에도 빈 `missing_context_artifact_hash` key를 남기던 presence
  위반을 고쳐, 값이 없으면 property 자체가 생기지 않게 했다.
- `module_summary_rollups` ProductWriteBatch는 Parser가 정한 `owner_id`를 그대로 따라 routine을 Module에
  묶고 function stable ID 순서로 정렬한다. 검증된 summary bytes를 다시 요약하거나 정규화하지 않고 같은
  길이의 ID·summary 배열로 저장한다.
- 함수가 없는 Module은 rollup property가 없고, 한 함수라도 공개 가능한 summary가 없으면 두 배열 모두
  만들지 않는다. 일부 함수만 담은 prefix rollup은 없다. Module/routine ID 중복과 범위 밖 owner는 DB를
  열기 전에 fail-closed한다.
- 현재까지의 Code/DDL/CALLS/READS-WRITES 구조, 함수·TABLE/COLUMN 의미, Module rollup, product writer
  집중 회귀는 76 tests, 실패 0이다. LLM 호출, SKT 재분석, Neo4j write는 수행하지 않았다.

## 29. 2026-08-28 Strategy-B Rule/Example 제품 batch

- `rule_meanings` batch는 검증된 업무 조건이나 효과가 있는 Rule만 만들고, 소유 함수와의 관계는
  `HAS_RULE` 하나가 소유한다. Rule node에는 owner ID나 반복 상수
  `business_meaning_status=sufficient`를 복제하지 않는다.
- `examples` batch는 결정론적으로 합성된 given/expected Python leaf와 `HAS_EXAMPLE`만 만든다. Rule과
  Example stable ID는 실제 owner·anchor·ordinal에서 재계산한 값과 같아야 하고, 각 child는 owner 하나,
  각 owner의 ordinal은 1부터 끊김 없는 source order여야 한다.
- 기존 ExampleMapping은 Rule→Table `AFFECTS_TABLE`도 생산하지만 최종 제품 relationship 닫힌 목록에는
  이 type이 없다. 해당 행은 Rule 검증·감사 산출물로만 유지하고 새 product batch에는 섞지 않는다.
  Rule의 직접 table 접근 제품 사실은 이미 함수의 READS/WRITES가 소유한다.
- 비routine Rule owner, orphan/다중 owner, 빈 Python leaf, 줄바꿈이 섞인 leaf, 업무 의미가 없는 구조
  후보는 DB를 열기 전에 fail-closed한다. 전체 product batch 집중 회귀는 81 tests, 실패 0이다.
  LLM 호출, SKT 재분석, Neo4j write는 수행하지 않았다.

## 30. 2026-08-28 FunctionTableUsage READ/WRITE 의미 분리

- 기존 Table semantic frame의 DATA_OBJECT slot은 발생별 `table_id`, `access`, exact occurrence를 이미
  갖고 있었지만 normalizer가 table ID만 key로 사용해 READ와 WRITE 의미를 문장 하나로 합치고 있었다.
  이 consumer 결함을 우회 문장 복사 없이 닫았다.
- `project_function_table_usages`는 같은 table이라도 `(function, table, read)`와
  `(function, table, write)`를 별도 key로 만들고, 각 access가 실제로 소유한 slot 문장·qualified column·
  occurrence ref만 모은다. required pair 하나라도 빠지거나 다른 pair가 섞이면 fail-closed한다.
- FunctionTableUsage는 Summary 결과를 기다리거나 입력으로 사용하지 않는다. 기존 3분할 병렬 Table
  호출의 닫힌 frame에서 바로 만들어지고 Framework·DBMS fan-in이 함수 내부 상태에 보존한다.
- `function_meanings` batch 하나가 CodeNode 함수 의미와 READS/WRITES 관계의 접근별 의미 patch를 함께
  소유한다. 관계에는 `used_column_ids`, 접근별 summary, meaning status, missing-context count/hash만
  들어가고 occurrence refs는 run 내부 회계로 남는다.
- 현 semantic profile의 `table_op`은 같은 table의 read/write operation을 하나로 합칠 수 있어
  access별 `data_operations` 정본으로 사용하지 않았다. operation은 Step 5의 실제 ReadsWrites edge와
  `data_access_links` batch가 계속 소유하며, 의미 patch는 edge population과 대조한다.
- 같은 table READ+WRITE 반례에서 두 의미·두 column·두 occurrence가 분리되고 하나를 제거하면 실패하는
  집중 회귀를 추가했다. 이 변경 동안 LLM 호출, SKT 재분석, Neo4j write는 수행하지 않았다.

## 31. 2026-08-28 ExternalChunk 제품 투영 선행 gap

- 최종 제품 계약은 ExternalChunk에 `source_file_id`, `source_record_identity`, accepted adapter ID/version을
  요구하지만 현재 producer model은 `file_path`, key, span/hash만 갖는다. ProjectFile과 HAS_CHUNK는 최종
  제품 닫힌 목록에도 없다.
- 따라서 file path나 key에서 provenance를 추측해 ExternalChunk batch를 만들지 않았다. Step 3/4 adapter가
  위 네 필드를 실제 선택 시점에 생산하도록 바꾼 뒤에만 ExternalChunk와 REFER_TO 제품 투영을 연결한다.

## 32. 2026-08-28 table-usage 재질의·컬럼 근거 결박 제거

- producer→consumer 추적 결과, 모델이 반환하던 `table_roles`는 Analyzer가 이미 확정한
  READ/WRITE/DML을 다시 분류하는 중복 출력이었다. 또한 table-level role은 같은 table을
  READ와 WRITE하는 함수의 access별 의미를 정확히 표현할 수 없었다.
- Framework/DBMS 응답 schema에서 `table_roles`를 제거하고 assigned semantic `slots`만 받도록
  바꾸었다. 모델이 table identity, access, operation, column, 좌표를 복창하지 않도록 prompt와
  closed response schema를 같이 바꾸었다.
- 기존 `table_insights` 혼합 객체는 함수-table 의미, table-level role, column 좌표를 한데
  묶어 운반했다. 이 호출이 실패하면 결정론적으로 생성한 column 근거까지 사라지는
  consumer 결함을 확정했다.
- 혼합 객체와 dead legacy response model을 제거했다. 함수-table 의미는
  `function_table_usages`, 결정론 column 원문 근거는
  `direct_column_usage_evidence_by_table`로 별도 이동한다. 후자는 table-usage LLM 실패
  여부와 관계없이 최종 Table/Column 의미 단계에 도달한다.
- 파일·함수·prompt·scheduler lane의 `insight` 호환 이름을 실제 책임인
  `function_table_usage` / `table_usage_meanings`로 교체했고, 사용되지 않던 빈 호환 배열과
  `_insight_semantic_frame`을 제거했다.
- 결정론 column 근거가 모델 응답 없이도 보존되는 반례를 포함해 집중 회귀
  118 tests가 통과했다. prompt hash baseline은 변경된 17개 prompt 전체를 한 번 갱신한 후
  byte-identical check를 통과했다. LLM 호출, SKT 재분석, Neo4j write는 수행하지 않았다.
- 남은 gap: 최종 계약은 `results → mapped/insufficient`만 모델이 반환하도록 정했지만,
  현재 구현은 아직 occurrence별 `slots` 봉투와 model-authored status/missing-context를 사용한다.
  이 봉투를 최종 계약으로 축소하는 작업은 별도 책임 슬라이스로 남아 있다.

## 33. 2026-08-28 FunctionTableUsage 최종 입출력 계약 전환

> 이 절의 `results → mapped|insufficient` 형식은 당시 중간 이력이다. 현재 실행 계약은 아래 §50과
> 현행 Analyzer contract §5의 `FT key → 의미 문자열 또는 빈 문자열` 직접 map이 대체한다.

- §32의 남은 gap을 닫았다. 모델 응답 root는 `results` 하나이며 Analyzer가 배정한 모든 FT key를
  정확히 한 번 포함한다. 각 FT는 `mapped.function_table_usage_summary` 또는 고정된
  `insufficient.disposition_reason_code=semantic_not_derivable` 중 하나만 가진다. 누락·추가·두 branch
  동시 출력은 dynamic closed schema가 거부한다.
- occurrence별 semantic slot은 모델 입출력에서 제거하고 Analyzer 내부 exact-once 회계에만 남겼다.
  Analyzer가 FT branch를 소유 occurrence 전체에 결합한 뒤 FunctionTableUsage를 만든다. request-local
  FT key가 제품 의미 문장에 섞이면 정규화하지 않고 실패한다.
- 테이블 의미 입력은 생성 시점부터 최소 projection으로 바꾸었다. 전체 structural profile과 불투명한
  evidence ID를 만들거나 보낸 뒤 제거하지 않는다. 각 FT에는 확정 physical table, access, 실제 발생 줄,
  연산, 직접 한정 column만 한 번 담고, 검증된 table 설명과 번호가 붙은 현재 원문을 별도 섹션으로 보낸다.
- 공용 semantic-slot 계약은 이 호출의 실제 출력과 달라 재사용하지 않는다. Framework·DBMS가 함께 쓰는
  FunctionTableUsage 전용 계약 하나가 입력 권한, 판정 순서, 잘못/올바른 반례, self-check를 고정한다.
- FunctionTableUsage의 `data_operations` 복제 필드를 제거했다. 이 값은 Step 5의 직접 READS/WRITES
  구조 관계가 이미 소유하며 의미 결과에서 다시 만들 이유가 없었다. 같은 이유로 `partial` 상태도
  제거했다. 한 FT occurrence 집합은 전부 mapped 또는 전부 insufficient여야 하며 혼합은 실패한다.
- Step 7 전체 216 tests와 publication·prompt·제품 writer 집중 81 tests가 통과했다. 변경된 공용 계약을
  포함한 18개 prompt hash를 갱신하고 byte-identical check를 통과했다. LLM 호출, SKT 재분석,
  Neo4j write는 수행하지 않았다.

## 34. 2026-08-28 외부 자료 provenance·완전성 책임 구간

- §31의 선행 gap을 닫았다. Step 3/4는 최종 graph node가 아니라 accepted adapter ID/version,
  source file/record stable ID, exact source span/text/hash, 실제 주소 key를 가진 `ExternalSourceRecord`를
  생산한다. Step 5는 Parser의 정확한 호출·인자 사실만 사용해 연결하고 발생별 exact-once ledger를
  만든다. Step 6은 연결된 원문만 Summary 문맥에 넣고, 최종 publication이 `ExternalChunk`와
  `REFER_TO`를 투영한다. ProjectFile/HAS_CHUNK와 unresolved 전용 graph entity는 없다.
- 소비처가 없던 외부 file/record의 `file_name`, `project_name`, `record_name`, `discovered_keys`와
  디코딩 `confidence`를 계약·생산자·소비자·테스트에서 함께 제거했다. custom adapter의 검증된 key가
  `addressable_keys`로 전달되지 않던 producer 전환 결함은 실제 값 흐름으로 확정해 수정했다.
- 실제 정상 입력을 막던 고정 경계를 제거했다: 짧은 단일 파일 skip, negative 파일 6개/20,000자,
  구조 후보 6개, scanner 확장자 20종·카테고리 50개·manifest 60줄, custom match 1,000건,
  regex 5초 천장, 문서 bytes/output/page/sheet/row/cell/120초, Agent 16/30회와 출력 8,000 token이다.
  도구 페이지는 `next_offset`/`next_start_line`으로 끝까지 접근 가능하므로 정보 상한이 아니다.
  문서 parser는 별도 process에서 전체를 처리하고 run 취소 시 child를 종료한다.
- 외부 로딩→탐색→링킹→LLM 문맥→제품 투영, Scan Agent 경계, 문서/인코딩을 합친 집중 회귀는
  168 tests 통과, 실패 0, Windows symlink 권한 skip 1이다. LLM 호출, SKT 재분석, Neo4j write는
  수행하지 않았다.
- 이때 확인한 `run_executor` legacy 성공 경로 gap은 §35에서 닫았다. External만 먼저 쓰지 않고 14개
  제품 stage 전체를 같은 final state에서 조립·검증·공개한다.

## 35. 2026-08-28 실행기 14-stage 단일 공개 경로

- `run_executor`는 workflow 종료 후 checkpointer의 final state를 한 번 읽고, 제품 producer 필드가 모두
  존재하는지 확인한다. stream에서 수집한 call/import ledger와 final state ledger가 다르면 DB 쓰기 전에
  실패한다.
- final state는 `CompleteProductPlanInput`으로 한 번 투영된다. 14개 `ProductWriteBatch`는 endpoint node
  생성 → relationship 생성 → 의미 property patch의 dependency order를 보존한다. 기존 alphabetical sort가
  `call_links`를 `code_structure`보다 먼저 실행하던 결함을 제거했다.
- 실행기는 각 batch의 operation/property/read-back ledger를 검증해 building generation에 쓴 뒤
  `publish_generation`을 정확히 한 번 호출한다. publish transaction은 완전한 제품 plan을 다시 읽고,
  AnalysisHealth와 call/import/data-object 회계를 대조한 후에만 publication-version CAS와 active pointer를
  전환한다.
- 제품 node 13종 모두 `(analysis_scope_id, generation_id, stable_id)` 유일성 제약을 갖는다. 제품 관계
  닫힌 목록에 생산자·소비자가 없던 `HAS_HEALTH`는 제거했다. AnalysisHealth는 generation-scoped 제품
  record로 직접 검증된다.
- 실패 종료는 옛 `ANALYZER_OWNED` label에 의존하지 않고 정확한 scope/generation의 모든 제품 node를
  제거한다. 이전 active generation과 AnalysisGeneration control record는 보존한다.
- 옛 property-scan helper와 테스트 fake, workflow의 `neo_client` 전달 설명을 제거했다. pipeline step
  내부 `Neo4jClient|upsert_|execute_write|execute_query|session(` 검색 결과는 0건이며 DB 쓰기 소유자는
  실행기 하나다.
- 새 경로 집중 회귀는 `79/79` 통과했다. opt-in 실제 Neo4j test도 새 14-stage 계약으로 교체했지만
  사용자 지시대로 실행하지 않아 `1 skipped`다. LLM 호출, SKT 재분석, Neo4j 제품 쓰기는 계속 0회이며
  실제 rollback·read-back·pointer 전환은 미검증이다.

## 36. 2026-08-28 Java·Python Parser 구조 사실과 Analyzer 판정 경계

- Parser producer를 실제 grammar listener부터 추적한 결과, C만 callable 구조 사실을 만들고
  Java·Python은 call/import만 만들며 lexical scope가 비어 있었다. Oracle·PostgreSQL은 call/callable
  population 자체를 생산하지 않는다. 이 차이를 언어 이름 추측이 아니라 첫 producer 실패 좌표로 확정했다.
- Parser 공통 `GrammarStructureEvidence`가 ANTLR parse-tree의 grammar component·direct token·정확한
  source range·lexical scope를 생산하도록 만들었다. Java는 method/interface method/constructor/record
  compact constructor/annotation element, Python은 decorator/async/classmethod/nested function을 포함한
  callable 사실과 call scope를 생산한다. Parser는 final binding/type/linkage나 standard/external 판정을
  만들지 않는다.
- Analyzer의 language adapter는 최종 판정을 반환하지 않고 `LanguageSemanticCandidateSet`만 만든다.
  lexical visibility, deepest binder, linkage/type compatibility, internal/external/ambiguous 최종 판정은
  language-neutral resolution core 하나가 소유한다. 현재 제품 adapter 등록은 C만 완료됐고,
  Java·Python의 symbol/type/import 환경과 SQL producer/adapter는 미완료다.
- 이 분리 과정에서 common core가 C의 `declaration_point <= call_start`와 scope-prefix/deepest-scope 규칙을
  직접 적용하던 원인을 추가로 확정했다. C adapter가 candidate별 `linkage_visibility`를 계산하고 lexical
  shadowing까지 표시하도록 옮겼으며, neutral core는 그 visible 집합에서 final disposition만 결정한다.
  선언 순서가 무관하고 다른 class scope의 visible method도 가능한 Java형 반례로 C 규칙 재적용 0을
  고정했다.
- Java·Python의 class scope를 strict decoder가 읽기 위해 사용자 WIP인
  `pipeline/steps/step2_load_ast/evidence_ir.py`에는 `_SCOPE_KINDS`의 `class` 한 항목만 추가했으며 다른
  WIP 줄은 덮어쓰지 않았다.
- Python Parser가 대문자 호출을 `NEW_INSTANCE`로 만들고 초기화 변수의 `variableType`으로 복제하던
  추측 경로를 실제 `HTTP()` 대문자 함수와 `lower()` 소문자 callable class 반례로 확정했다. 이 값은
  Analyzer 변수 문맥과 LLM 입력까지 전파되므로 미미한 내부 표기가 아니었다. 대소문자·initializer
  regex 추측 producer와 Python `NEW_INSTANCE` capability를 제거하고 두 호출 모두 ANTLR call 사실과
  원문 범위만 보존했다.
- active 제품 코드와 테스트의 전체 경로 검색에서 `c_stdlib.json`, `java_stdlib.json`,
  `python_stdlib.json`을 여는 소비자는 0이었다. 세 목록은 exact identity/version registry가 아니며
  Java type·Python module/exception 등 호출 함수가 아닌 이름도 섞여 있어 active 경로에서 제거했다.
  DBMS builtin은 별도 ontology의 exact package identity 경로를 계속 사용한다. 봉인 registry가 없는
  언어에서 이름만으로 `standard_library`를 지어내지 않으며, workspace 밖 호출은 제품 CALLS 없이
  external 장부로 남는다.
- Parser 집중 회귀는 `35 passed`, Python capability 변경의 Analyzer 집중 회귀는
  `17 passed, 4 subtests passed`, Analyzer의 표준 함수 자산 제거 후 호출/IR 집중 회귀는
  `33 passed, 10 subtests passed`다. 시스템 Python에는 pytest가 없어 실행되지 않았고 기존 Analyzer
  `.venv`로 같은 묶음을 실행했다. Parser·Analyzer `git diff --check`와 계약·상태·HTML 동기화도
  통과했다.
- 이번 구간에도 LLM 호출, SKT 재분석, Neo4j 제품 쓰기, 임시 DB 생성, 봉인 Strategy A 재실행은 0회다.
  Java·Python import 구조 사실은 complete지만 Analyzer에 build/classpath/Python import-system binding
  environment 입력이 없어 모든 active import가 `insufficient_import_binding_environment`로 끝난다.
  다음 첫 실패 좌표는 이 환경 계약과 Java·Python symbol/type fact producer이며, 그 다음은
  Oracle·PostgreSQL 구조 사실 producer다.

## 37. 2026-08-28 Java 직접 호출 adapter와 Python fail-closed 경계

- `build_import_scope_plan`은 모든 import를 unresolved ledger로 만든 뒤 항상 빈 `scopes={}`를 반환했고,
  실제 reference·call 연결 소비자는 그 값을 한 번도 읽지 않았다. 소비처 없는 `ImportScopePlan`과 wrapper를
  제거하고 canonical import occurrence ledger를 실제 결합 위치에서 직접 만들도록 직선화했다. 결과 상태와
  occurrence ID·순서는 바뀌지 않았으며 집중 회귀 16건이 통과했다.
- Java의 첫 안전한 내부 연결 범위를 ANTLR 구조 사실만으로 닫았다. receiver 없는 named method call이 같은
  source의 같은 enclosing class에 있고 grammar parameter arity가 맞을 때만 Java adapter가
  `visible|compatible` 후보를 만든다. neutral core가 선언 순서와 무관하게 최종 internal을 결정한다.
- 다른 class의 동명이인, receiver dispatch, constructor type lookup은 classpath·type/import 환경 없이
  추측하지 않는다. 각각 external 또는 명시적 dynamic 사유로 종료하며 제품 CALLS를 만들지 않는다.
- Python은 assignment·import·nested definition·`global|nonlocal` rebinding 사실이 아직 Parser
  구조 wire에 없으므로 callable 이름 일치로 연결하지 않는다. Python adapter가 모든 call occurrence를
  `python_name_binding_facts_required` 또는 runtime-expression dynamic으로 exact-once 닫는다.
- Java·Python/C 호출 경계 집중 회귀는 `24 passed, 10 subtests passed`다. LLM 호출, SKT 재분석,
  Neo4j 제품 쓰기, 임시 DB 생성, Strategy A 재실행은 0회다. 다음 첫 producer 결손은 Python binding-site
  구조 사실이며, Java receiver/constructor/import·inheritance도 봉인 binding environment가 생긴 뒤에만
  내부 후보로 승격할 수 있다.

## 38. 2026-08-28 Python parameter 구조 사실 producer→consumer

- 확정 원인은 Python callable 선언의 parameter 구문이 호출 binding 소비자가 사용할 독립 사실과
  completeness ledger로 나오지 않아, 같은 이름의 사용자 함수와 parameter callable을 구분할 수 없었던
  것이다. 이름 일치 fallback은 사용하지 않았다.
- Parser는 function과 lambda의 모든 parameter 이름을 `parameter` 사실로 만들며, payload는 정확한 이름
  token range와 `translation_unit → ... → function|lambda` lexical scope path만 가진다. type이나 최종
  binding 대상은 만들지 않는다. function 4개와 lambda 3개를 포함한 formal red가 7/7 exact slice와
  complete ledger로 닫혔다.
- Analyzer strict decoder와 Step 2 state가 이 사실을 exact-once로 읽고 Step 5 language adapter까지 직접
  전달한다. 같은 source·같은 innermost function/lambda scope에서 호출 이름과 parameter 이름이 같으면
  `python_parameter_binding` dynamic으로 닫고 CALLS를 만들지 않는다. 이 내부 좌표는 LLM 입력이 아니다.
- Parser 집중 회귀는 `36 passed`, Analyzer parameter/call 집중 회귀는 `29 passed`, Step 2 및 call graph
  통합 회귀는 `19 passed, 1 skipped, 10 subtests passed`다. skip은 기존 선택적 actual fixture이며 새 실패가
  아니다.
- LLM 호출, SKT 재분석, Neo4j 제품 쓰기, 새 DB, Strategy A 재실행은 0회다. 다음 첫 결손은 assignment·for·
  with·except·comprehension·import target·nested/class definition·`global|nonlocal`의 이름 결속 구조 사실이다.

## 39. 2026-08-28 Python binding target 구조 사실 producer→consumer

- Parser는 Python assignment·annotated/augmented assignment·`for`·`with ... as`·`except ... as`·
  comprehension target을 `binding_target` fact로 exact-once 생산한다. fact는 target range, grammar component
  tree, grammar 발생 문맥, lexical scope만 가지며 local/global binding이나 최종 symbol을 만들지 않는다.
- actual grammar tree 계측으로 단순 이름은 `name→NAME`, attribute/subscript mutation은 같은 `expr` 아래
  `trailer`가 존재함을 확인했다. Analyzer strict decoder가 이 tree를 원문 range와 대조한 뒤 같은
  function/lambda scope의 단순 이름과 unpacking 이름만 `python_local_binding_target` dynamic으로 닫는다.
  원문 정규식 재파싱과 대소문자 추측은 사용하지 않는다.
- attribute/subscript mutation은 지역 이름 binding으로 오인하지 않는다. comprehension target은
  `[x for x in x()]`의 outer iterable 가시성 반례가 아직 닫히지 않아 함수 지역 collision 근거로 사용하지
  않는다. `global|nonlocal`, import target, nested/class definition 환경도 후속 구조 사실 전까지 남아 있으므로
  Python callable 이름 일치만으로 새 CALLS를 만들지 않는다.
- Parser formal 회귀는 `37 passed`, Analyzer strict decoder·C/Java/Python adapter 집중 회귀는 `27 passed`,
  기존 Step 2·call graph 통합 회귀는 `23 passed, 1 skipped, 10 subtests passed`다. skip은 기존 선택적 actual
  fixture이며 새 실패가 아니다.
- LLM 호출, SKT 재분석, Neo4j 제품 쓰기, 새 DB, Strategy A 재실행은 0회다. 새 사실과 원문 tree는 Analyzer
  내부 call binding에만 소비되며 LLM prompt나 제품 graph node로 공개하지 않는다.

## 40. 2026-08-28 Step 7 LLM 호출 설명과 실제 경로 일치

- 정적 callsite inventory와 `three_split_llm_fanout.py` 실행 경로를 대조했다. Framework 코드노드의 정상
  경로는 Summary 1회, Rule atom meaning 0~1회, direct Table usage meaning 0~1회로 총 1~3개 독립 관심사다.
  Rule 구조·좌표는 Analyzer가 결정론으로 만들고 모델은 배정된 atom 의미와 Rule 여부만 채운다. Example은
  검증 Rule에서 투영하므로 별도 LLM 호출이 없다.
- 코드 머리말과 함수 설명에는 과거 “Summary/Table 1~2회, Rule LLM 0회, module intent 사전분석”이 남아
  있었지만 실제 제품 경로에는 Rule meaning 호출이 있고 module intent LLM은 없었다. 이 레거시 설명을
  현재 실행 경로에 맞춰 제거했다. 제품 동작·프롬프트 값·호출 수는 변경하지 않았다.
- Summary·Rule·Table은 같은 노드에서 서로 기다리지 않고 병렬로 시작한다. 첫 Summary의 전역 system
  prefix warm 신호는 다른 Summary만 제어하며 Rule/Table을 막지 않는다. Summary transport가
  deadline·length·repetition으로 실패할 때만 서로 다른 slot/evidence closure로 분할하고, 같은 성공 요청을
  의미만 바꿔 다시 묻지 않는다.
- LLM callsite inventory와 Summary·Rule·Table prompt projection·dataflow scheduling 집중 회귀
  `21 passed`를 통과했다. 실제 LLM
  호출, SKT 재분석, Neo4j 제품 쓰기는 0회다.

## 41. 2026-08-28 Python global/nonlocal 구조 사실 producer→consumer

- Parser는 `global`과 `nonlocal` statement에 나열된 모든 이름을 `scope_directive` fact로 분리한다. fact는
  이름 token range, directive 종류, lexical scope만 가지며 실제 module/enclosing binding을 결정하지 않는다.
  nested function의 `nonlocal handler`와 `global service, registry` formal red에서 3/3 exact slice와 complete
  ledger를 확인했다.
- Analyzer strict decoder와 Step 2 state가 이 fact를 exact-once로 읽어 Python adapter에 전달한다. 같은
  function/lambda scope의 명시 directive는 parameter·assignment target 판정보다 우선한다. `global`은
  `python_global_binding`, `nonlocal`은 `python_nonlocal_binding` dynamic으로 닫고 CALLS를 만들지 않는다.
  같은 이름의 global/nonlocal이 상충하면 `python_conflicting_scope_directives` unsupported로 fail closed한다.
- Parser formal 회귀는 `38 passed`, Analyzer strict decoder·C/Java/Python adapter 집중 회귀는 `31 passed`,
  기존 Step 2·call graph 통합 회귀는 `23 passed, 1 skipped, 10 subtests passed`다. 실제 LLM 호출, SKT
  재분석, Neo4j 제품 쓰기, 새 DB, Strategy A 재실행은 0회다.
- 남은 Python 이름 환경은 import target, nested/class definition, module runtime order와 comprehension outer
  iterable 가시성이다. 이들이 닫히기 전에는 Python callable 이름 일치만으로 내부 CALLS를 만들지 않는다.

## 42. 2026-08-28 Python import 결속 이름 producer→consumer

- 기존 Parser `import` fact에는 ordered entry의 module path·member·alias·정확한 range가 있었지만 lexical
  scope가 없어 함수 안 import와 module import를 구분할 수 없었다. 별도 import-binding fact를 추가하면 같은
  이름·범위를 중복 생산하므로 배제하고, C·Java·Python의 기존 import fact에 구조적 `scopePath`만 보강했다.
- C include와 Java import는 `translation_unit`, Python 함수 내부 import는 실제 문법 구조인
  `translation_unit → function → block`을 보존한다. Parser는 bound name이나 module resolution을 만들지 않는다.
  Analyzer Python adapter가 기존 entry를 재사용해 `import package.module`의 첫 component, alias, from-import
  member를 언어 규칙으로 해석한다.
- 같은 함수의 명시 import 또는 함수에서 보이는 module import가 bare call 이름과 충돌하면
  `python_import_binding`, 가시적 wildcard import면 `python_wildcard_import_binding` dynamic으로 닫는다.
  sibling function의 import는 보이지 않는 반례를 통과했다. 실제 imported module/member 연결과 runtime order는
  아직 추측하지 않으므로 CALLS를 만들지 않는다.
- Parser formal 회귀는 `39 passed`, Analyzer strict decoder·C/Java/Python adapter 집중 회귀는 `40 passed`,
  기존 Step 2·call graph 통합 회귀는 `27 passed, 1 skipped, 10 subtests passed`다. skip은 환경변수로만 켜는
  기존 actual fixture다. 실제 LLM 호출, SKT 재분석, Neo4j 제품 쓰기, 새 DB, Strategy A 재실행은 0회다.
- 남은 Python 이름 환경은 module runtime order, 실제 import environment와 comprehension outer iterable
  가시성이다. Java type/import 환경과 Oracle·PostgreSQL call/callable producer도 아직 미완료다.

## 43. 2026-08-28 Python comprehension outer iterable 가시성

- 실제 Parser scope를 대조한 결과 기존에는 comprehension element call과 가장 바깥 iterable call이 모두
  같은 `comprehension` scope를 받았다. 이 상태에서 target을 승격하면 `[x() for x in x()]`의 바깥 `x()`까지
  새 target `x`가 가리는 오판이 생기므로 지금까지 comprehension target 소비를 보류한 원인이 확정됐다.
- Python Parser는 call이 가장 바깥 `comp_for`의 iterable grammar subtree에 있을 때만 현재 comprehension
  scope를 제외하고 enclosing scope를 전달한다. element·filter·후속 iterable은 comprehension scope를
  유지한다. 원문 문자열이나 정규식은 사용하지 않았다.
- Analyzer는 `comprehension_target`의 scope가 call의 전체 scope path에 실제 포함될 때만 이름 결속으로
  적용한다. 이 방식은 같은 comprehension 본문을 막고 가장 바깥 iterable은 막지 않으며, 중첩 comprehension의
  바깥 target도 안쪽 body에서 계속 보이게 한다.
- Parser formal 회귀는 `40 passed`, Analyzer strict decoder·C/Java/Python adapter 집중 회귀는 `41 passed`,
  기존 Step 2·call graph 통합 회귀는 `27 passed, 1 skipped, 10 subtests passed`다. 실제 LLM 호출, SKT
  재분석, Neo4j 제품 쓰기, 새 DB, Strategy A 재실행은 0회다.
- 남은 Python 이름 환경은 module runtime order와 실제 import module/member resolution이다. Java type/import
  환경과 Oracle·PostgreSQL call/callable producer도 아직 미완료다.

## 44. 2026-08-28 Python delete target compile-time binding

- Parser listener와 grammar를 대조한 결과 `del_stmt`는 존재하지만 `binding_target` producer가 없었다.
  최소 bytecode 역실험에서 `def f(): x(); del x`의 `x`는 `co_varnames=('x',)`이며 호출도
  `LOAD_FAST_CHECK`를 사용했다. 즉 `del x`는 실행 위치와 무관하게 함수의 compile-time local binding을
  결정하므로 누락 시 같은 이름의 내부 함수를 잘못 연결할 수 있다.
- Parser는 `del`의 전체 `exprlist`를 `delete_target` fact로 생산한다. 기존 grammar component tree를 그대로
  사용하므로 Analyzer는 단순 이름·unpacking 삭제만 지역 결속으로 보고 attribute/subscript 삭제는 상태
  변경으로 제외한다. 새 정규식·원문 재파싱·별도 사실 형식은 추가하지 않았다.
- Parser formal 회귀는 `40 passed`, Analyzer strict decoder·C/Java/Python adapter 집중 회귀는 `42 passed`,
  기존 Step 2·call graph 통합 회귀는 `27 passed, 1 skipped, 10 subtests passed`다. 실제 LLM 호출, SKT
  재분석, Neo4j 제품 쓰기, 새 DB, Strategy A 재실행은 0회다.
- 현재 grammar에는 assignment expression(`:=`) token/rule이 없음을 확인했다. 현재 지원 grammar 범위에서
  남은 Python final binding 결손은 module runtime order와 실제 import module/member resolution이다.

## 45. 2026-08-28 복제 산출물 제거와 SQL producer 결손 확인

- `target/c081-staged-snapshot`은 활성 코드·테스트·SDD 소비처가 0이며 과거 보고서에서도 제품 저장소
  복제 산출물로 분류돼 있었다. exact-path dry-run으로 범위를 확인한 뒤 일반 파일과 ignored cache를 포함한
  1,690파일·15,355,353바이트를 제거했다. 같은 `target`에 남아 있던 과거 HTML 렌더링 확인용 PNG도 문서상
  제품·근거·커밋 대상이 아님을 확인하고 제거했다. 두 경로는 Git 비추적 산출물이므로 복구 대상이 없다.
- spec 131은 현재 README, CURRENT_STATUS, spec, plan, tasks와 계약 2개, 정확히 7파일이다. 금지된
  `scripts/recompute_strategy_a_notation.py`와 표기 실험 WIP는 읽거나 수정하거나 삭제하지 않았다.
- SQL은 작은 부분 구현을 하지 않았다. Oracle은 `call_statement` AST node는 만들지만 expression function-call
  전체의 canonical call population이 없고, PostgreSQL은 dollar-quoted PL/pgSQL을 별도 parser로 읽어 원문 줄만
  rebasing하며 원본 char range를 생산하지 않는다. 일부만 IR 2.0 complete로 승격하면 누락을 숨기므로 producer
  범위와 원문 좌표를 함께 닫을 때까지 미완료로 유지한다.

## 46. 2026-08-28 Java static import 의미 환경

- Parser 실제 계약은 이미 `import static pkg.Type.member`와 `import static pkg.Type.*`에서 type path component,
  member, wildcard, directive range와 translation-unit scope를 구조 사실로 생산했다. Analyzer Java adapter가
  `import_refs`를 전달받고도 버리던 지점이 실제 손실 좌표였다.
- Analyzer는 call의 `caller_id`와 callable의 `code_node_id`가 가리키는 기존 `CodeNode.owner_id`를 사용해
  enclosing type을 비교한다. static import는 Parser의 type path와 owner ID가 정확히 같고 callable grammar
  modifier tree에 `static`이 있을 때만 보이게 한다. 명시적 멤버와 wildcard 모두 닫았고 instance method는
  연결하지 않는다. 파일명 추측·정규식 재파싱·LLM·새 중복 fact는 없다.
- Analyzer strict decoder·C/Java/Python adapter 집중 회귀는 `44 passed`, 관련 Step 2·call graph 합집합 회귀는
  `71 passed, 1 skipped, 10 subtests passed`다. 기존 Parser formal 40개에는 두 static import 구조 반례가
  포함돼 있다. 실제 LLM 호출, SKT 재분석, Neo4j 쓰기, 새 DB는 0회다.
- 이 시점에는 receiver dispatch와 constructor type lookup을 필요한 구조 투영이 완결되지 않은 후속 항목으로
  남겼다. 바로 다음 §47에서 일반 class constructor의 실제 producer/consumer 모순을 닫았으며, receiver와
  compact/qualified constructor 결손은 계속 이름이나 파일명으로 추측하지 않는다.

## 47. 2026-08-28 일반 Java constructor 구조 투영과 제한된 type 가시성

- Parser는 일반 class constructor를 callable fact로 생산하면서 AST 함수 노드는 만들지 않았다. Analyzer의
  definition projection은 모든 callable definition을 실제 CodeNode와 exact-once로 결합하므로 생성자가 있는
  실제 Java 파일에서 projection failure가 날 수 있는 producer/consumer 모순이었다.
- Parser는 문법으로 확인된 일반 class constructor 본문을 기존 함수 분석 단위 `METHOD` 구조 노드로 함께
  생산한다. 이름·signature·modifier·generic parameter·formal parameter는 해당 grammar context에서 가져오며
  final binding/type을 결정하지 않는다. 기존 Parser 집중 회귀 `40 passed`를 유지했다.
- Analyzer는 실제 생성자 CodeNode의 owner ID를 사용해 같은 type, 같은 package, 명시적 type import,
  namespace wildcard를 판정하고 grammar parameter arity가 맞는 업로드 정의만 연결한다. 정의가 없으면 partial
  upload 경계의 external로 닫는다. 같은 줄의 같은 이름 overload는 이름+줄 단일 일치로 가정하지 않고, 같은
  Parser traversal에서 나온 AST node와 callable fact를 좌표 그룹 안의 occurrence 순서로 exact-once 결합한다.
  Analyzer 집중 회귀는 `47 passed`, 관련 합집합은 `74 passed, 1 skipped, 10 subtests passed`다.
- 실제 owner ID는 `code:package:Type` 형식이므로 import의 `package.Type`과 비교할 때 Analyzer가 container
  separator를 Java qualified type separator로 정확히 변환한다. 테스트용 점 표기에서만 통과하던 반례를 실제
  ID 형식으로 교정했다. 실제 LLM·SKT·Neo4j 쓰기는 수행하지 않았다.
- record compact constructor는 대응 Module/CodeNode projection이 아직 없고 qualified constructor call은
  callee type component 구조 사실이 아직 없으므로 미완료다. receiver dispatch와 함께 추측 없이 남긴다.

## 48. 2026-08-28 Summary·TableDescription 모델 자체 상태 제거

- 공통 semantic slot response는 각 배정 key마다 `{meaning,status}` 객체를 요구했다. `status`는 모델의
  자기평가였고 Analyzer가 그대로 canonical 의미 상태로 승격했으며, slot key가 이미 출력 칸을 고정하므로
  `meaning` 포장 key도 같은 의미를 반복했다.
- provider-visible 응답을 `slots: {request-local-key: 의미 문자열}`로 축소했다. 모델은 의미를 입증할 수
  없으면 빈 문자열만 반환하며, `status`, missing-context, evidence, 내부 ID를 출력하지 않는다. Summary의
  target-level `summary`·`stereotype`과 TableDescription의 고정 table/column identity는 그대로 유지한다.
- Analyzer가 비어 있지 않은 정상 의미는 `sufficient`, 빈 의미나 private wire key 누출은 `insufficient`,
  실제 구조 사실에 `unavailable_cycle` callee가 있는 비어 있지 않은 의미만 `partial`로 결정한다. 내부
  missing-context는 이 확인된 원인에서만 만들므로 모델 자기평가가 제품 상태가 되지 않는다.
- 관련 Step 7 전체와 TableDescription·aggregate·LLM boundary 회귀는 `243 passed, 99 subtests passed`다.
  실제 LLM 호출, SKT 재분석, Neo4j 제품 쓰기, 새 DB는 0회다. Rule atom의 모델 `status`는 별도 계약이므로
  이번 slice에서 추정으로 제거하지 않고 다음 producer→consumer 감사 대상으로 남겼다.

## 49. 2026-08-28 Rule atom 모델 자체 상태와 반복 포장 제거

- Rule의 실제 제품 경로를 추적한 결과 행의 `business_rule | implementation_detail | insufficient`
  분류는 제품 Rule 생성 여부를 결정하는 필수 의미 판정이었다. 반면 각 condition/effect의 `status`는
  별도 구조 사실과 연결되지 않은 모델 자기평가였고 `{meaning,status}` 객체가 atom마다 반복됐다.
- 당시 provider-visible Rule 응답은 `conditions: {C-key: 의미 문자열}`, `effects: {E-key: 의미 문자열}`,
  `rows: {R-key: 분류 문자열}`의 세 exact map만 가진다. 모든 key는 그대로 required이며 extra·누락은
  거부한다. atom 의미를 입증할 수 없으면 빈 문자열로 두고, Analyzer가 필요한 atom 중 하나라도 비었으면
  해당 행을 `insufficient`로 닫았다. 이 condition 자연어 응답은 실제 반례 뒤 §110에서 폐기됐다.
- 행 분류 자체는 강제로 양자택일시키지 않기 위해 `insufficient`를 유지한다. `implementation_detail`은
  제품 Rule을 만들지 않고, `business_rule`이면서 모든 필요 atom 의미가 있을 때만 Analyzer가 Python
  Rule/Example을 결정론적으로 한 번 투영한다. 추가 judge·repair·Example LLM 호출은 없다.
- Rule 집중 회귀는 `46 passed, 20 subtests passed`, 관련 Step 7 전체 합집합은
  `243 passed, 99 subtests passed`다. 실제 LLM 호출, SKT 재분석, Neo4j 제품 쓰기, 새 DB는 0회다.

## 50. 2026-08-28 FunctionTableUsage branch와 root 포장 제거

- 확정된 `(function, physical table, access)` FT key마다 모델 응답이
  `results → mapped → function_table_usage_summary` 또는
  `results → insufficient → semantic_not_derivable`를 반복했다. FT key가 이미 정확한 출력 칸이고
  insufficient reason도 항상 같은 상수여서 의미 정확도에 기여하지 않는 포장이었다.
- provider-visible 응답 root를 `{FT-key: 의미 문자열 또는 빈 문자열}`로 변경했다. required FT key와
  extra 금지, direct table/access/occurrence exact assignment는 그대로 유지한다. 빈 문자열은 Analyzer가
  `insufficient`와 고정 missing-context artifact를 만들며, 의미 문자열에 request-local FT key가 누출되면
  이전과 같이 terminal validation failure로 막는다.
- 집중 schema·projection·batch·dataflow 회귀는 `21 passed, 9 subtests passed`, 관련 Step 7 전체
  합집합은 `243 passed, 99 subtests passed`다. 실제 LLM 호출, SKT 재분석, Neo4j 제품 쓰기,
  새 DB는 0회다.

## 51. 2026-08-28 생성 요청 단일 transport attempt 경계

- 실제 설치 객체를 확인한 결과 OpenAI와 Anthropic client는 모두 내부 기본 retry가 2였고, Analyzer
  runner가 transport·429·5xx 등에 최대 3 attempt를 다시 적용했다. 한 logical request가 최악의 경우
  최대 9번 전송될 수 있었으며, 서버가 request bytes를 받지 않았다는 증명도 없었다.
- Summary는 `deadline|length|repetition` 실패 뒤 같은 atom 의미를 더 작은 schema로 다시 질의했고,
  runner는 invalid JSON escape를 수정해 schema 실패를 성공으로 승격했다. framework/dbms 공통 prompt의
  210초 wall timeout도 provider hard limit이 아닌 application 상한이었다.
- provider SDK `max_retries=0`, Analyzer runner single attempt, Summary 실패의 즉시 격리로 직선화했다.
  210초 wall timeout과 JSON output repair, 소비처가 없어진 transport-retry 모듈을 제거했다. 실제 provider
  hard limit를 실행 전에 봉인한 partition planner는 아직 미구현이므로, 현재는 실패 후 재질의하지 않고
  fail-closed한다.
- 이름 변경 전 1차 집중 회귀는 `54 passed`, 최종 모듈명으로 다시 실행한 single-attempt·raw schema
  failure·Summary exact-once·3분할 scheduling·호출처 inventory 합집합은 `62 passed`다. 계약·출력
  schema·병렬성·기존 cache 동작 합집합도 별도로 `83 passed`했으며, 변경된 prompt 정본은 18개 hash를
  갱신한 뒤 byte-identical 검사를 통과했다. 실제 LLM 호출, SKT 재분석, Neo4j 쓰기, 새 DB,
  Strategy A 재실행은 0회다.

## 52. 2026-08-29 영속 응답 cache의 봉인된 실행환경 경계

- 기존 cache key는 system/user/model/rule `max_tokens`/structured method/schema만 포함했다. 실제 client의
  provider·endpoint·temperature·top_p·reasoning·extra body가 빠졌고, rule에 `max_tokens`가 없으면 실제
  config 한도 대신 `null`을 key에 넣었다. 같은 모델 별칭으로 weights·tokenizer·quantization·runtime이
  교체돼도 이전 답이 적중하는 원인이었다.
- 영속 cache는 기본 OFF로 바꿨다. weights/revision·tokenizer·quantization·serving runtime·provider hard
  limit을 기록한 content-addressed 정본의 ref와 실제 bytes SHA-256이 모두 맞을 때만 활성화된다. 정본은
  process당 한 번만 읽는다. 모델 이름이나 endpoint만으로 cache를 켤 수 없다.
- 적중 key는 filter 적용 뒤 실제 system/user message와 runtime SHA-256, provider, endpoint, model,
  effective max tokens, temperature, top-p, reasoning, extra body, repetition guard, structured method,
  response schema를 포함한다. LangChain의 별도 전역 cache 설정은 소비 의미가 없어 제거하고 항상
  비활성화했다.
- cache·LLM 경계·설정 집중 회귀는 `72 passed`, single-attempt·schema·3분할 scheduling까지 합친
  책임 slice 회귀는 `134 passed`다. 현재 GPU runtime capability 정본은 없으므로 현재
  환경의 영속 cache는 안전하게 비활성이다. 전체 분석 시작 자체를 막는 `SealedAnalysisInputs` preflight는
  아직 구현되지 않았으며 다음 봉인 책임 slice에 남는다. 실제 LLM 호출, SKT 재분석, Neo4j 쓰기,
  새 DB는 0회다.

## 53. 2026-08-29 분석 입력 단일 봉인과 partial-upload 실제 범위

- `SealedAnalysisInputs` preflight를 구현했다. source·AST/diagnostic 대응표·DDL·외부 프로젝트 입력·사람
  용어집·LLM 설정·prompt·현재 계약·Analyzer 구현·검증 baseline·정답지·model/tokenizer/serving
  runtime/GPU identity를 `sealed-analysis-inputs.json` 하나로 묶는다. secret은 제외하고 실제 유효 설정만
  canonical hash에 넣는다. 성공 manifest는 이 단일 artifact의 ref와 SHA-256만 가리킨다.
- preflight는 source/AST closure와 model runtime 정본을 검증한 뒤에만 기존 Neo4j client를 만든다. runtime
  ref·hash가 없거나 실제 bytes가 다르면 `failed_preflight` manifest를 남기고 DB 접속 전에 종료한다. 같은
  process의 이전 runtime 객체가 다음 run으로 넘어가지 않도록 run 경계에서 parsed cache를 비운다.
- 원인은 봉인 hash를 만든 뒤 prompt·설정·skill·용어집·외부 문서·table linker가 원본 경로를 다시 열어
  다른 revision을 소비할 수 있던 경로였다. run-scoped immutable bytes cache를 분석 준비의 첫 단계로 옮기고
  모든 해당 소비처가 같은 bytes를 사용하도록 했다. Windows CRLF는 기존 `Path.read_text()`와 같은
  universal-newline text로만 해석하며 bytes hash는 그대로다. 외부 문서 subprocess는 원래 파일을 다시 열지
  않고 봉인 bytes로 만든 격리 복사본을 읽는다.
- partial upload 원인 추적에서 `selected_source_ids`가 Step 2와 Scan Agent 파일 도구에는 적용됐지만 Step 3
  inventory와 통합 봉인은 project root의 선택 밖 source까지 읽고 있었음이 확정됐다. 이제 선택 밖
  Parser-supported source는 열지 않으며, 선택 source·root manifest·source가 아닌 실제 외부 입력만 한 번
  inventory한다. 이 inventory를 봉인과 Step 3가 공유하고, project root 아래 run output은 입력에서 제외한다.
  Scan Agent는 선택 source와 root manifest만 볼 수 있어 다른 source를 우회해 읽을 수 없다.
- CPU-native component clustering이 async event loop를 막던 경로는 worker thread로 옮기되, 취소할 수 없는
  동일 계산을 pause 중복 실행하지 않도록 앞뒤 gate만 둔다. 관련 외부 문서·AST·DDL·publication·structure
  광역 회귀 291건을 통과했다. 최신 공유 bytes·봉인·LLM·execution·Step 3·Step 4 영향 범위 회귀는
  `262 passed, 1 skipped`다. skip은 기존 환경 선택형 fixture이며 새 실패가 아니다.
- 실제 LLM 호출, SKT 재분석, Neo4j 제품 쓰기, 새 DB, Strategy A 재실행은 0회다. 따라서 이 구간은 정적
  실행 경계 완료이며 제품 의미 정확도나 속도 합격으로 승격하지 않는다. GPU가 복구될 때까지 1,503줄 SKT
  선택 범위와 5만 LOC 성능은 미검증으로 남는다.

## 54. 2026-08-29 partial 검증의 제품 공개 scope 격리

- `selected_source_ids`는 source/AST/Scan 입력만 제한했지만 기존 `analysis_scope_id`는 root와 strategy만
  사용했다. 실행기는 partial run도 항상 `publish_generation`하므로, 정상 종료한 1,503줄 검증이 같은 root의
  전체 active generation을 대체할 수 있는 producer→consumer 결함이었다.
- scope scheme을 `analysis-scope/v2`로 올리고 canonical root·strategy·정확한 source population을 함께
  hash한다. 전체와 partial, 서로 다른 partial 집합은 다른 scope다. 선택 순서만 다른 동일 집합의 반복 실행은
  같은 scope를 유지한다.
- 이 변경은 DB를 읽거나 쓰지 않고 정적 계약과 로컬 회귀로 검증한다. 실제 SKT·LLM·Neo4j 제품 write는
  수행하지 않았으며, partial 제품 공개의 실DB 축은 계속 미검증이다.

## 55. 2026-08-29 Oracle Parser 구조 사실과 Analyzer 의미 adapter

- Oracle case-insensitive lexer wrapper에서 공용 좌표 도우미가 실제 입력이 아니라 wrapper `toString()` 길이를
  읽던 원인을 확정했다. ANTLR code-point stream의 실제 `size()`를 사용하도록 고쳐 qualified call과 EOF scope
  range를 원문 반열린 좌표로 복구했다.
- Parser는 Oracle call의 qualified component·DB link·argument·scope와 callable 정의의
  schema/package/member component를 구조 사실로 생산한다. `package` lexical scope를 Parser와 Analyzer 공통
  어휘에 추가했으며 binding, builtin, internal/external 의미는 Parser에 넣지 않았다.
- Analyzer strict decoder는 위 range를 원문에서 한 번만 복원한다. Oracle adapter는 unquoted/quoted identifier,
  구조적 caller package prefix, parameter/default arity를 해석하고 neutral core가 internal/external/ambiguous를
  결정한다. DB link와 partial upload는 external, 등록 builtin은 업로드 사용자 정의 충돌이 없을 때만 제외한다.
  같은 caller→callee 반복 발생은 ledger에 모두 남고 제품 CALLS 쌍은 한 번만 투영한다.
- Parser 구조 계약 `21 passed`, Analyzer decoder·Oracle adapter 교차 계약 `76 passed`, C·Java·Python을 포함한
  call-resolution 회귀 `55 passed, 10 subtests passed`를 통과했다. 실제 LLM, SKT 재분석, Neo4j write, 새 DB는
  0회다. PostgreSQL nested 원문 좌표와 DBMS runtime 단일 경로 전환은 아직 미완료다.

## 56. 2026-08-29 PostgreSQL 구조 사실과 DBMS 호출 runtime 단일화

- Neo4j 읽기 전용 관문에서 인증과 `SHOW DATABASES`를 확인했다. `dbms/framework/neo4j/system/test`만
  존재하고 모두 online이었다. DB write와 새 database 생성은 0회다.
- 첫 formal red는 `dispatch.py`가 PostgreSQL Parser call fact 1건을 소비하지 못해
  `common evidence DBMS call consumer is not implemented`로 중단한 좌표였다. DBMS AST tree에는 CALL
  CodeNode가 있었지만 canonical `call_sites`와 `callable_facts`로 투영되지 않아 새 PostgreSQL adapter가
  실제 pipeline에서 호출될 수 없었다.
- PostgreSQL Parser는 dollar-quoted PL/pgSQL nested body의 call/callee/argument/scope를 전체 파일의
  code-point range로 rebasing하고, qualified FUNCTION/PROCEDURE 정의를 callable fact로 생산한다. Analyzer는
  callable의 정확한 시작 줄과 Parser occurrence order로 정의 CodeNode를 결합한 뒤, call의 innermost
  `function` scope range와 callable `scope_range`가 정확히 같을 때만 caller를 배정한다. source 정규식,
  점·괄호 분해, 이름 전용 owner 추측은 없다.
- Framework와 DBMS는 모두 `link_parser_calls_step` 하나에서 language adapter와 neutral resolution core를
  호출한다. framework만 repository-owned skill의 검증된 제외·wrapper 계약을 주입하고 DBMS는 빈 제외
  계약을 사용한다. 옛 `link_calls_by_ast_step.py`, `call_linker.py`, DBMS legacy linker 테스트는 제거했다.
  Component가 쓰던 검증된 사용자 routine edge 재검사는 `verified_call_edges.py`로 책임과 이름을 분리했다.
- PostgreSQL qualified call은 같은 schema·kind·구조 arity 정의와 internal로 연결된다. unqualified call은
  봉인 search_path가 없어 configuration-dependent, 업로드 밖 qualified 정의는 external, type overload가
  남으면 ambiguous다. 같은 occurrence ledger에서 제품 CALLS pair만 중복 제거한다.
- Parser 구조 계약은 `27 executed, 1 skipped`, 실패·오류 0이며 Parser `git diff --check`도 통과했다.
  DBMS 투영 formal red 1건과 C·Java·Python·Oracle·PostgreSQL adapter, 공통 core, pipeline authority,
  component 소비자를 합친 Analyzer 집중 회귀는 `77 passed, 10 subtests passed`다. 실제 LLM 호출, SKT 재분석,
  Neo4j 제품 쓰기, 새 DB, Strategy A 재실행은 0회다. Java/Python 완전한 type/import 환경과 실제 SKT
  1,503줄·Neo4j publication은 계속 미검증이다.

## 57. 2026-08-29 Oracle package-name 표준 라이브러리 추측 제거

- 남은 B01 감사를 따라간 결과 Oracle adapter가 봉인된 version별 callable identity가 아니라
  `DBMS_OUTPUT` 같은 패키지 이름만으로 모든 멤버를 표준 라이브러리로 제외하는 경로를 확인했다. 같은
  패키지의 미등록 멤버나 partial upload 바깥 사용자 정의를 구분할 수 없어 과분류가 가능한 원인이었다.
- `dbms_builtin_packages` 소비자는 Oracle adapter 하나뿐이었다. adapter의 package-name 분기,
  `pipeline/languages.py`의 미사용 loader, `ontology_roles.yaml`의 패키지 목록을 함께 제거했다. 업로드의 exact
  callable 정의가 있으면 기존처럼 internal 후보가 되고, 없으면 제품 `CALLS` 없이 external occurrence로
  보존한다. 봉인된 exact callable registry가 생기기 전에는 `standard_library`를 추측하지 않는다.
- 첫 집중 red는 external ledger의 제품 outcome을 테스트가 `external`로 잘못 기대한 좌표였다. 기존 계약대로
  상태는 `external`, occurrence outcome은 `preserved`임을 고정했다. Oracle·공통 classifier·ontology 집중 회귀는
  `47 passed, 4 subtests passed`다. 공통 owner 명칭 영향 회귀도 `39 passed, 1 skipped,
  10 subtests passed`였으며 활성 코드의 옛 Framework/DBMS 전용 owner reason과 legacy linker 참조는 0이다.
- 실제 LLM 호출, SKT 재분석, Neo4j 제품 쓰기, 새 DB, Strategy A 재실행은 0회다.

## 58. 2026-08-29 Java record compact constructor 구조 단절 복구

- Parser는 `Value {}` compact constructor의 callable fact는 생산했지만 대응 record와 함수 분석 단위 AST
  node를 만들지 않았다. 따라서 Analyzer의 callable fact→CodeNode exact projection이 실제 입력에서는 닫힐
  수 없는 producer→consumer 단절이었다.
- Parser가 Java record를 새 graph 종류가 아닌 기존 `CLASS`, compact constructor를 기존 `METHOD`로 보존하도록
  수정했다. callable 발생 범위·이름·scope는 그대로이며, Analyzer 공통 projector가 같은 시작 줄·이름·Parser
  occurrence 순서로 정확히 한 번 결합한다.
- record header는 compact constructor 발생 범위 바깥이므로 callable syntax 안으로 끌어오지 않았다. Analyzer는
  암시적 parameter를 0개로 오인하지 않고 arity compatibility를 `unknown`으로 넘긴다. 정확한 owner type에
  유일한 compact constructor 정의가 있을 때만 neutral core가 internal로 연결한다.
- Parser `SemanticEvidenceIrContractTest` 22건과 `StructuralStatementAstContractTest` 21건, 합계 43건이 실패·오류·
  skip 0으로 통과했다. Analyzer IR2 projection과 Java adapter는 `23 passed`다. 실제 LLM 호출, SKT 재분석,
  Neo4j 제품 쓰기, 새 DB, Strategy A 재실행은 0회다.

## 59. 2026-08-29 Java qualified constructor type component 연결

- Java Parser의 공통 call fact 형식에는 `calleePathRanges`가 있었지만 constructor producer는 이를 비워
  `new external.Target(1)`의 `external`과 `Target` 구조를 Analyzer에 전달하지 않았다. Analyzer는 terminal
  `Target`만 받아 같은 package·import 이외의 명시적 qualified type을 정확히 연결할 수 없었다.
- Parser가 `classOrInterfaceTypeToInstantiate`의 각 identifier range를 occurrence 순서대로 생산하도록 했다.
  Analyzer는 복원된 전체 component `external.Target`가 candidate `CodeNode.owner_id`의 type identity와 정확히
  같을 때만 visible로 만든다. `other.Target` 반례는 external이며 import나 파일명 fallback은 없다.
- Parser 첫 red는 expected `[external, Target]`, actual `[]`였고 수정 후 통과했다. Analyzer 첫 red는 exact
  qualified call의 edge 0건이었으며 수정 후 Java adapter 10건이 통과했다. 실제 LLM 호출, SKT 재분석,
  Neo4j 제품 쓰기, 새 DB, Strategy A 재실행은 0회다.

## 60. 2026-08-29 Java `this` receiver 폐쇄 사례 연결

- Java adapter는 receiver가 있다는 이유만으로 `this.target()`도 일반 객체 receiver와 함께 dynamic 처리했다.
  그러나 Parser가 보존한 exact receiver range가 `this`이면 별도 type 환경 없이 현재 callable owner type임이
  문법적으로 확정된다.
- exact receiver source가 `this`인 경우만 같은 owner type의 method 후보 경로로 보냈다. `service.target()`이나
  `Outer.this.target()` 등 다른 receiver expression은 기존처럼 `java_receiver_type_binding_environment_required`
  dynamic이며 이름 일치로 연결하지 않는다.
- Parser 실제 문법 fixture에서 `this.local()`의 receiver slice가 정확히 `this`임을 확인했고, Analyzer의 첫
  red edge 0건을 internal edge 1건으로 닫았다. Java adapter 11건이 통과했다. 실제 LLM 호출, SKT 재분석,
  Neo4j 제품 쓰기, 새 DB, Strategy A 재실행은 0회다.

## 61. 2026-08-29 B01 언어 adapter·중립 resolution core 감사 종료

- C·Java·Python·Oracle·PostgreSQL의 Parser 구조 fact→언어 adapter→중립 resolution core를 다시 합쳤다.
  구조로 증명 가능한 same-owner/static-import/constructor/SQL identity는 internal로 연결하고, 봉인된 type·runtime
  환경이 필요한 Java 일반 receiver와 Python runtime binding은 발생을 버리지 않은 채 dynamic으로 종료한다.
- package name만 사용한 Oracle standard-library 추측, Java record compact constructor AST 단절, qualified
  constructor component 누락, `this` receiver 과도 dynamic을 각각 원인 좌표에서 제거했다. standard-library는
  봉인 version의 exact callable registry가 없으면 이름으로 분류하지 않고 업로드 밖 호출을 external로 보존한다.
- C·Java·Python·Oracle·PostgreSQL adapter, IR2 projection, DBMS runtime, pipeline authority, component 소비자를
  합친 Analyzer 회귀는 `82 passed, 14 subtests passed`다. Java Parser semantic evidence 22건과 structural AST
  21건도 실패·오류·skip 0이다. 이는 정적 구조·결정론 계약 통과이며 실제 제품 의미 정확도나 성능 합격으로
  승격하지 않는다.
- 실제 LLM 호출, SKT 재분석, Neo4j 제품 쓰기, 새 DB, Strategy A 재실행은 0회다.

## 62. 2026-08-29 B02 atomic generation rollback 실DB 검증

- 실행 직전 Neo4j 인증과 `SHOW DATABASES`를 다시 확인했다. `dbms/framework/neo4j/system/test`만 존재하고
  모두 online이었다. 새 DB는 만들지 않았다.
- 기존 `test` DB에서 UUID로 격리한 `analysis_scope_id`와 generation을 단일 caller-owned transaction 안에만
  만들었다. 14개 product stage, analysis health record, call occurrence ledger를 쓴 뒤 active generation pointer와
  closure count를 transaction 안에서 확인했다.
- 검증 성공·실패와 무관하게 같은 transaction을 rollback하고, 새 session에서 해당 scope의 node가 0건임을
  확인했다. opt-in integration 결과는 `1 passed in 9.01s`다. 기존 제품 DB와 기존 test 데이터는 변경하지
  않았고 새 DB 생성도 0회다.
- 이는 atomic publication·rollback 계약 검증이며 SKT 제품 재분석이나 실제 LLM 의미 정확도 검증은 아니다.

## 63. 2026-08-29 B03 저장소 정리와 구형 호출 소비 경로 폐쇄

- specs 001~130과 이미 삭제된 spec을 안내하던 루트 `specs/README.md`를 제거했다. 현재 `specs`에는
  131의 진입점·SDD·계획·작업·현재 상태와 Parser/Analyzer 계약, 합계 7파일만 남는다.
- 활성 코드 소비처가 없는 Analyzer 로컬 `docs/archive`·`docs/audit` 72파일, Python bytecode cache,
  일회성 Strategy A/APAS/쇼핑몰 표기 실험 스크립트, `.codegraph`와 `.pytest_cache`를 제거했다.
  `_runs`·`_artifacts`는 baseline과 봉인 검증 소비 가능성이 있어 크기만으로 삭제하지 않았다.
- `scripts/evidence_preflight.py`가 삭제된 `CallLinker`로 DBMS 하위 함수 순서를 다시 만들던 formal red를
  확인했다. Parser가 생산한 call/callable/parameter/binding/scope/import/source completeness/preprocessing
  사실을 한 번 모아 현행 `classify_call_sites`에 전달하도록 교체했다. 전달 계약 직접 검증을 포함한
  `tests/scripts`는 `27 passed`다.
- 런타임 로그 표시표의 삭제된 `link_calls_by_name`, `link_calls_by_ast`, `summarize_module`, `embed` 이름을
  제거하고 실제 pipeline의 단일 `link_parser_calls`, module rollup, component, table description, search
  segment 단계 이름으로 맞췄다. 관련 구조·스크립트 회귀는 `36 passed`다.
- 활성 제품 코드의 구형 call linker 이름은 0건이며, 남은 세 문자열은 폐기 파일이 다시 생기지 않음을
  고정하는 negative contract test뿐이다. 금지된 `scripts/recompute_strategy_a_notation.py`는 파일명 존재만
  확인했고 읽기·수정·실행·stage하지 않았다.
- 실제 LLM 호출, SKT 재분석, Neo4j 제품 쓰기, 새 DB, Strategy A 재실행은 0회다.

## 64. 2026-08-29 삭제한 과거 spec을 읽는 구조 테스트 교체

- 전체 test collection은 `1,459 tests collected`로 import 오류 없이 끝났지만, 구조 테스트를 실행하자
  삭제한 specs 058·076·077의 과거 이동 장부 TSV를 읽는 7건이 formal red가 됐다. 제품 코드 결함이 아니라
  오래된 저장소 이관 절차 자체가 테스트 정본으로 남은 것이 원인이었다.
- 과거 503파일 baseline·move map·folder audit을 복구하지 않았다. 해당 테스트 3파일을 제거하고 현재 runtime
  책임 디렉터리, spec 131의 정확한 7파일, 폐기 구현 부재, 실제 prompt asset과 기본 data directory를 직접
  검사하는 `test_repository_layout.py` 하나로 교체했다.
- 현행 구조 회귀는 `15 passed, 16 subtests passed`다. 활성 코드에서 삭제된 old-spec 경로를 가리키던 Catalog와
  검색 설정 주석도 현행 계약·회귀 테스트 설명으로 바꿨다.
- 실제 LLM 호출, SKT 재분석, Neo4j 제품 쓰기, 새 DB, Strategy A 재실행은 0회다.

## 65. 2026-08-29 LLM 진입점·입력 봉인·응답 경계 집중 회귀

- production Python AST의 `run_yaml_rule`과 `run_structured_agent` 호출 좌표를 exact Counter로 고정한
  inventory를 실행했다. 승인된 scanning/external, residual table pattern, Framework·DBMS의 Summary·Table
  Usage·Rule Meaning, Table Description을 고정했다. 이후 확인된 Search 질의 재작성 경계는 §83에서
  구조화 단일 호출로 명시 편입했다.
- agent system prompt는 직접 파일 재읽기가 아니라 run-scoped 첫 bytes를 반환하는 `read_run_text`를 사용한다.
  이 개선 전에 작성된 direct `Path.read_text` 문자열 검사가 formal red 2건을 냈으므로, 같은 봉인 bytes 사용을
  직접 고정하도록 테스트를 교체했다.
- 의미 충분성 `status`를 모델이 출력해야 한다고 요구하던 낡은 prompt test 1건을 제거했다. 모델은 배정된
  meaning 문자열만 반환하고, 빈값·호출 순환·응답 검증에 따른 충분성은 Analyzer가 판정한다.
- LLM callsite inventory, response boundary, KV-prefix 순서, prompt 계약, agent 경계와 step7 전체 집중 회귀는
  `285 passed, 163 subtests passed`다. 실제 외부 모델 호출은 0회다.

## 66. 2026-08-29 publication·검색 소비 경계 집중 회귀

- graph writer, generation lifecycle, 모든 publication stage와 검색 reader/ranking을 한 책임 묶음으로 실행했다.
  내부 사용자 CALLS만 제품 관계로 공개되고 occurrence ledger는 제품 payload에서 분리되며, 모든 검색 읽기는
  active completed generation의 scope를 요구하는 계약이 유지됐다.
- 결과는 `156 passed, 1 skipped, 28 subtests passed`다. skip 1건은 opt-in Neo4j integration이며, 같은
  integration은 B02에서 기존 `test` DB transaction rollback으로 별도 통과했다.
- 실제 LLM 호출, SKT 재분석, Neo4j 제품 쓰기, 새 DB, Strategy A 재실행은 0회다.

## 67. 2026-08-29 나머지 pipeline 확대 회귀와 마지막 DBMS formal red 폐쇄

- agents, API, LLM runtime, shared, pipeline execution·quality gates와 step1~6을 확대 실행했다. 결과는
  `948 passed, 3 skipped, 174 subtests passed`와 formal red 1건이었다.
- 실패 테스트는 이름부터 `DBMS call consumer가 구현될 때까지 실패`를 요구하는 C-080 이전 관문이었다.
  이제 DBMS structural call consumer가 구현됐으므로 예외를 복구하지 않고, 함수 scope가 없는 call도
  `caller_id=""`, `owner_status=unresolved_no_routine`, exact call source로 보존되어 neutral core의 exact-once
  ledger에 도달하는 현행 계약으로 바꿨다.
- 변경 테스트와 missing-owner classifier 집중 회귀는 `9 passed`다. 이미 통과한 948건은 수정 영향 밖이므로
  즉시 반복하지 않고 마지막 전체 회귀에서 한 번 다시 합친다.
- 실제 LLM 호출, SKT 재분석, Neo4j 제품 쓰기, 새 DB, Strategy A 재실행은 0회다.

## 68. 2026-08-29 FunctionTableUsage 프롬프트와 strict schema 정합

- 실제 Framework·DBMS FunctionTableUsage response model은 이미 각 FT key에 문자열 하나만 허용하고,
  빈 문자열의 `insufficient` 상태와 고정 missing-context artifact는 Analyzer normalizer가 만들었다.
  그러나 두 YAML prompt만 폐기된 `results → mapped|insufficient` branch를 계속 지시했다. 같은 요청에서
  모델 지시와 provider strict schema가 서로 다른 객체를 요구하므로 정상 의미가 schema failure로 끝날 수
  있는 producer→consumer 계약 단절이었다.
- Framework prompt는 최상위 객체 자체를 FT exact map으로, DBMS batch prompt는 `nodes`의 각 node 값 자체를
  FT exact map으로 명시했다. 각 FT 값은 업무 의미 문자열 하나이며 입증할 수 없을 때만 빈 문자열이다.
  model-authored status·reason·branch·구조 좌표를 다시 만들지 않는다.
- 회귀 테스트는 두 prompt에 폐기 wrapper 문자열이 다시 들어오면 실패하고, 기존 동적 schema가 direct string,
  exact required key, extra 금지, Analyzer-owned insufficiency를 계속 검증한다. 첫 집중 회귀는
  `19 passed, 29 subtests passed`, Step 7과 LLM 경계 확대 회귀는 `242 passed, 153 subtests passed`다.
  prompt 18개 byte-identical 검사를 통과했다.
- prompt SHA-256은 Framework
  `0f914d38a19f8e9f412ee683da597110ed669e69c65ed517d4e8da31823cced8`, DBMS
  `0cca766304f734d58fbeb3f55fab3125d7583c3b15d52b814cb4634962f0e251`다. 실제 LLM 호출,
  SKT 재분석, Neo4j 제품 쓰기, 새 DB, Strategy A 재실행은 0회다.

## 69. 2026-08-29 Scan Agent 절대 경로 노출 제거

- unknown-project Scan Agent의 도구들은 생성할 때 이미 `project_root`와 선택 source allowlist에 결속된다.
  그런데 user message도 같은 절대 `project_root`를 한 줄 복사했고, 모델 출력·도구 호출·검증기는 이 문자열을
  소비하지 않았다. 의미 정확도에는 기여하지 않으면서 machine 경로만 모델 문맥에 노출하는 중복 입력이었다.
- `_build_user_message` 입력에서 `project_root`를 제거하고 project inventory, available skill 요약, DDL table
  목록만 남겼다. 파일 읽기·grep·검증 도구의 실제 root와 partial-upload 가시성은 코드에서 계속 강제되므로
  기능 범위나 완전성은 바뀌지 않는다.
- Agent 경계와 Step 4 전체 집중 회귀는 `109 passed, 16 subtests passed`다. 실제 Agent/LLM 호출,
  SKT 재분석, Neo4j 쓰기, 새 DB, Strategy A 재실행은 0회다.
## 70. Residual table-pattern 모델 입력에서 내부 좌표 메타데이터 제거 (2026-08-29)

- 원인: `agents/residual.py::_prompt_case`가 새 공통 table-access pattern의 의미를 바꾸지 않고 응답에서도 소비되지 않는 `case_id`, `file`, `line`, `source_id`, `source_context_status`, `source_coordinate_system`을 모델 입력에 포함했다. 실제 물리 발생 재적용과 exact-once 판정은 Analyzer가 원본 residual ledger로 수행하므로 이 값들은 모델에 필요하지 않았다.
- 수정: 모델-visible case를 `call_text`, `attempted_patterns`, `numbered_source` 세 필드로 닫았다. 물리 identity와 좌표는 Analyzer 내부 ledger에만 유지한다.
- fail-closed: 범위 밖·읽기 실패·빈 source window는 상태 문자열과 빈 원문을 모델에 넘기지 않고 `ResidualPatternVerificationError`로 LLM 호출 전에 중단한다.
- 집중 검증: `tests/pipeline/step5_linking/test_residual_resilience.py`, `tests/pipeline/test_llm_callsite_inventory.py`, `tests/pipeline/test_llm_response_boundary_inventory.py` — `18 passed, 9 subtests passed in 11.58s`.
- 미실행: 실제 LLM·SKT 재분석·Neo4j 쓰기는 수행하지 않았다.

## 71. 현재 구현 기준 고해성사 HTML 재작성 (2026-08-29)

- 기존 HTML의 존재하지 않는 목표 함수 경로와 과거 구조 설명을 제거하고, API 요청 → 입력 봉인 →
  source/AST/diagnostic closure → 구조 생성 → scanning → 결정론 linking → context → 3분할 의미 →
  결정론 후처리 → 14-stage product plan → generation publish 순서로 현재 구현만 다시 작성했다.
- Scan, Summary, FunctionTableUsage, Rule/Example, TableDescription, ExternalSkill, residual table pattern과
  Search 질의 용어집 치환의 실제 8개 LLM 경계를 완전한 입력·출력 모양과 각 필드 존재 이유로 기록했다.
  W/E/FT/C/E/R/줄범위 key는 실제 요청 전용 wire key로 표시하고, residual case는 수정 완료된
  `call_text|attempted_patterns|numbered_source`만 보인다.
- 파일은 52,684 bytes, SHA-256
  `04655c391b9676f5f32758994f3454ee0b44db2e5dcbcbe0aa1849dd743c5e69`다.
- 구조 검사: `<html>` 1/1, `<section>` 14/14, 필수 실제 함수·8개 LLM 경계 누락 0, 과거 가짜 경로와 제거된
  `source_context_status|source_coordinate_system` 0건.
- 실제 LLM·SKT 재분석·Neo4j 제품 쓰기는 수행하지 않았다.

## 72. Residual table-pattern 임의 30건·3라운드 제한 제거 (2026-08-29)

- 원인: `run_residual_loop`가 현재 미해결 중 첫 30건만 모델에 보이고 최대 3라운드에서 중단했다. 네 번째
  서로 다른 구문이 필요한 원본 발생은 모델에 한 번도 제시되지 않을 수 있었으며, 두 값은 provider hard limit이
  아니라 Analyzer 임의 설정이었다.
- 수정: 매 라운드 현재 미해결 모집단 전체를 전달한다. 검증된 새 패턴이 원본 발생을 하나 이상 해소할 때만 다음
  라운드로 가므로 유한 모집단이 감소해 종료가 보장된다. 명시적 무패턴·진전 없음은 즉시 종료하며 실제 model
  context를 넘으면 truncation·skip·fallback 없이 실패한다.
- 제거: `residual_sample_size`, `residual_max_rounds`,
  `ROBO_PIPELINE_RESIDUAL_SAMPLE_SIZE`, `ROBO_PIPELINE_RESIDUAL_MAX_ROUNDS`와 코드 slice를 모두 제거했다.
- 집중 검증: residual `14 passed`; settings/shared 확대 포함 `53 passed, 2 subtests passed`. 31건 전수 전달과
  과거 상한을 넘는 4라운드 verified progress를 회귀로 고정했다.
- 실제 LLM·SKT 재분석·Neo4j 제품 쓰기는 수행하지 않았다.

## 73. SearchSegment embedding의 2,000자 의미 절단 제거 (2026-08-29)

- 원인: 제품 SearchSegment의 `text`와 `text_sha256`은 함수 summary 전체를 소유했지만 `encode_texts`만
  `ROBO_EMBED_MAX_CHARS=2000` 이후를 잘랐다. 저장 text와 검색 vector가 서로 다른 의미 모집단을 대표했다.
- 수정: `_truncate_oversized`와 `EmbeddingSettings.max_chars`를 제거하고 exact input text를 그대로 provider에
  보낸다. 문자 예산은 여러 항목의 전송 묶음만 나누며 단일 text bytes를 자르지 않는다. provider가 단건을
  끝내 거부하면 truncation·skip·local 의미 fallback 없이 `EmbeddingError`로 실패한다.
- 집중 검증: 5,000자 뒤 마지막 의미까지 encoder에 동일하게 전달하는 회귀와 검색·발행 확대 회귀
  `110 passed, 28 subtests passed`. 제거 설정·함수·slice 잔존 0건.
- 실제 embedding 서버·LLM·SKT 재분석·Neo4j 제품 쓰기는 수행하지 않았다.

## 74. 제거된 Agent call-limit의 죽은 환경변수 정리 (2026-08-29)

- `ROBO_AGENT_SCAN_MAX_CALLS=60`과 `ROBO_AGENT_RESIDUAL_MAX_CALLS=15`는 현재 설정 모델·middleware·runtime
  소비처가 0이고, call-limit middleware는 이미 제거되어 제품 동작에 적용되지 않았다.
- `.env.example`과 settings 설명에서 두 죽은 설정을 제거했다. `AnalyzerSettings()` 구성 검증 통과,
  설정명·필드명 잔존 검색 0건이다.
- 실제 Agent·LLM·SKT 재분석·Neo4j 쓰기는 수행하지 않았다.

## 75. OpenRouter cross-provider 자동 fallback 제거 (2026-08-29)

- 원인: `qwen36_openrouter.yaml`과 `gpt54_nano_openrouter.yaml`의 `allow_fallbacks:true`는 loader와
  `build_llm_client`를 거쳐 실제 request `extra_body`에 전달됐다. 선택 provider가 실패하면 같은 분석 중
  다른 serving runtime으로 자동 전환되어 단일 시도·봉인 실행환경 계약을 우회했다.
- 수정: 두 config를 `allow_fallbacks:false`로 고정하고 모든 OpenRouter config를 검사하는 회귀를 추가했다.
  최초 provider 선택의 `sort:throughput` 정책은 fallback과 다른 책임이므로 이 slice에서 바꾸지 않았다.
- 집중 검증: config/cache/single-attempt/runtime seal `32 passed`; `allow_fallbacks:true` 잔존 0건.
- 실제 OpenRouter·LLM·SKT 재분석·Neo4j 쓰기는 수행하지 않았다.

## 76. 필수 pipeline step 설정 누락의 빈 성공 제거 (2026-08-29)

- 원인: DDL, AST, Framework table linking, context, table description step은 `execute_run`이 반드시 넣는
  `codebase|strategy`가 없어도 warning 뒤 `{}`를 반환했다. 배선 오류가 빈 구조·빈 관계의 정상 결과처럼 다음
  단계로 흐를 수 있었다.
- 수정: `require_step_config_value`와 `StepConfigurationError` 공통 경계를 만들고 다섯 필수 step이 설정 누락 시
  즉시 실패하게 했다. handoff abort, DDL table 0건, 선택적 datasource/embedding off 같은 정상 무작업은 유지했다.
- 확대 회귀에서 과거 `results` wrapper를 요구하던 Step8 formal red 2건도 현재 direct FT-string schema로 수정했다.
- 검증: 관련 step1/2/5/6/8과 execution 확대 `427 passed, 2 skipped, 14 subtests passed`; 필수 설정
  `config_missing → step skip` 잔존 0건.
- 실제 LLM·SKT 재분석·Neo4j 제품 쓰기는 수행하지 않았다.

## 77. Framework 함수 분석 불완전 집계의 경고 성공 제거 (2026-08-29)

- 원인: Framework dataflow는 정상 반환된 함수 수가 배정 대상 수보다 적어도 경고만 남기고 다음 단계로
  진행했다. 함께 집계하던 `failed_count`와 `skipped_count`는 실제 producer가 없어 항상 0이었다.
- 수정: 죽은 두 계수를 제거하고 정상 반환된 함수 수가 배정 대상 전체와 다르면 `RuntimeError`로 즉시
  실패하게 했다. DBMS 엔진의 기존 fail-closed 완전성 계약과 동일하다.
- 영향 좌표: `pipeline/steps/step7_analyze_code/framework_per_node.py`의 Framework 함수 분석에만 적용된다.
  LLM 호출 수·입력·출력, DBMS 배치, Parser, graph 쓰기 계약은 바뀌지 않았다.
- 집중 검증: dataflow scheduling과 semantic failure 회귀 `25 passed`.
- 실제 LLM·SKT 재분석·Neo4j 제품 쓰기는 수행하지 않았다.

## 78. Scan Agent 모델-visible 경로의 프로젝트 상대경로 통일 (2026-08-29)

- 원인: 절대 `project_root`를 user message에서 제거한 뒤에도 시스템 prompt와 도구 설명은 그 값을 쓰라고
  지시했고, `glob_search|grep`은 절대경로를 반환했다. 모델은 전달받지 않은 값을 요구받고 환경 종속 경로를
  다음 도구 입력으로 다시 소비했다.
- 수정: Scan prompt와 다섯 filesystem 도구의 공개 경로 계약을 `.` 기준 프로젝트 상대경로로 통일했다.
  `glob_search|grep` 결과와 read 실패 좌표도 상대경로만 반환하고, `list_code_nodes`는 같은 상대경로를 실제
  Parser node에 연결한다. 절대 root와 partial-upload allowlist 검사는 도구 내부에 그대로 남는다.
- 집중 검증: Agent 경계·결정론 SKILL 선택·step4 Scan 회귀 `114 passed, 16 subtests passed`.
- 실제 Agent·LLM·SKT 재분석·Neo4j 제품 쓰기는 수행하지 않았다.

## 79. Rule 종료문 행의 160자 의미 절단 제거 (2026-08-29)

- 원인: `branch_inventory._ast_termination_rows`가 Parser의 `RETURN|THROW|RAISE` 식을 공백 정규화한 뒤
  160자로 잘랐다. 뒤쪽 식이 Rule 후보 행과 누락 회계에서 사라지지만 함수 원문에는 남아 두 입력이
  불일치했다.
- 수정: 길이 절단을 제거해 Parser가 준 종료식 전체를 Rule 행에 보존한다. 진단 메시지의 첫 실패 예시는
  제품 의미가 아니므로 이 변경 범위에 포함하지 않았다.
- 집중 검증: 40개 항의 160자 초과 반환식 마지막 항 보존 회귀와 branch inventory·grading·prompt 확대
  `106 passed, 3 subtests passed`.
- 실제 LLM·SKT 재분석·Neo4j 제품 쓰기는 수행하지 않았다.

## 80. Table pattern 임의 통과 비율 제거 (2026-08-29)

- 원인: table pattern 검증이 op token 50% 이상, DDL 미해소 80% 이하, 중의성 20% 이하라는 설정값으로
  일부 잘못된 매치와 중의성을 정상 규칙으로 승격할 수 있었다. 반대로 비율은 partial upload의 정상 범위 밖
  정의와 거짓 매치를 구분하지 못한다.
- 수정: 신규·residual 규칙은 매치한 op token 전부 인식, 부분 entity 0, 중의 0, 로컬 DDL 미해소 0을
  요구한다. 등록 SKILL은 원문 계약이 별도로 검증됐으므로 기존처럼 partial upload의 범위 밖 table을
  허용한다. 세 임계 설정과 죽은 `.env.example` 설명을 제거했다.
- 집중 검증: 단 1건의 미해소·중의와 단 1건의 미등록 op token 반례를 포함한 link/Agent 회귀
  `88 passed, 12 subtests passed`; `AnalyzerSettings()` 구성 통과, 제거 설정 잔존 0건.
- 실제 Agent·LLM·SKT 재분석·Neo4j 제품 쓰기는 수행하지 않았다.

## 81. 현재 정적 구현 전체 자동 회귀 통과 (2026-08-29)

- 실행: `python -m pytest -q` 전체를 한 번 수행했다.
- 결과: `1461 passed, 4 skipped, 310 subtests passed in 83.76s`; 새 실패 좌표 0건.
- 이 결과는 자동 회귀층의 증거다. GPU가 필요한 실제 LLM 생성, 봉인 SKT exact-once 전수 회계,
  작은 SKT 직접 의미 판정, 약 5만 줄 속도 측정으로 승격하지 않는다.
- 실제 SKT 재분석과 Neo4j 제품 쓰기는 수행하지 않았다.

## 82. 무시되던 Analyze API 경로 호환 필드 제거 (2026-08-29)

- 원인: `AnalyzeRequest`가 `project_root`, `ddl_dir`를 받지만 제품 입력 선택에는 쓰지 않았다. 요청자는 경로가
  적용됐다고 오해할 수 있고, 최종 계약에는 소비처 없는 호환 껍데기가 남아 있었다.
- 수정: 두 필드를 요청 모델에서 제거하고 `extra="forbid"`를 적용했다. Analyzer 입력은 설정의
  `data/source`, `data/ddl`만 사용하며 과거 경로 필드는 조용히 무시하지 않고 계약 오류로 거부한다.
- 집중 검증: `tests/api/test_http_boundaries.py` — `3 passed in 22.24s`. 첫 시스템 Python은 pytest 미설치,
  존재하지 않는 추가 테스트 경로는 실행 0건이었고, 저장소 `.venv`로 실제 집중 회귀를 완료했다.
- 고해성사 HTML의 요청 예시와 당시 확인된 LLM wire 예시를 현재 코드와 다시 동기화했다. 실제 LLM·SKT 재분석·
  Neo4j 제품 쓰기는 수행하지 않았다.

## 83. Search 질의 재작성의 숨은 raw LLM 경계 제거 (2026-08-29)

- 원인: 기본 `novel_abbrev` 검색은 질의마다 LLM 재작성을 실제로 호출했지만, 공통 LLM client 밖의 raw
  `httpx`를 사용하고 마크다운 fence 제거와 `{...}`·`[...]` 부분 문자열 복구를 수행했다. 호출 실패나
  schema 실패는 원질의로 조용히 되돌렸다. 모델 출력의 `intent`와 `rewritten`은 제품 소비처가 0건이었고,
  실제 재작성은 별도 코드가 `substitutions`로 다시 만들었다.
- 수정: Search 전용 설정·인증은 유지하면서 공통 LangChain provider adapter와 strict structured output을
  사용한다. 모델은 질의에 실제 존재하는 `source_text`와 용어집에 정확히 존재하는 `canonical_term` 목록만
  한 번 출력한다. Analyzer는 모든 항목을 원질의·용어집과 대조하고 용어집 순서로 정렬해 병기한다.
  같은 system prompt와 질의의 cache key는 process-random hash 정수가 아니라 충돌 없는 tuple 원문이다.
- 제거: raw HTTP, 수동 JSON 복구, `intent`, 모델 작성 `rewritten`, `ok` 상태, 임의 1,024 출력 상한,
  실패 시 원질의 우회, 제거된 Search judge/legacy selection 경로를 남기지 않았다.
- 회귀: 새 경계의 exact-one 호출·strict extra 금지·잘못된 용어 실패·안정된 다중 병기·실패 전파를 검증했다.
  집중 묶음은 `63 passed, 27 subtests passed`, Search 전체와 LLM callsite inventory는
  `29 passed, 2 subtests passed`다. 실제 LLM·SKT 재분석·Neo4j 제품 쓰기는 수행하지 않았다.
- 문서: 고해성사 HTML은 Search를 포함한 8개 LLM 경계로 현재 구현과 동기화했다. 최신 파일 지문은
  §84가 소유한다.

## 84. AnalysisContext의 table 사실 호환 껍데기 제거 (2026-08-29)

- 원인: 생산 `build_context_step`은 모든 함수 Context에 `data_object_usages`를 만들지만 이를 자유형 `extra`에
  숨겼다. `exact_direct_table_ids`는 이 key가 없을 때 옛 단위 테스트를 위해 `direct_reads|direct_writes`를
  그대로 신뢰했다. 실제 발생의 access·resolution·table ID 교차검증을 건너뛸 수 있는 별도 의미 경로였다.
- 수정: `AnalysisContext.data_object_usages`를 명시 필드로 만들고 direct 목록과 exact usage의 교집합만 LLM
  table 의미 배정으로 보낸다. Oracle column 소유 판정이 실제 소비하는
  `unqualified_identifier_references`도 명시 필드로 옮겼다. `None`은 Parser 계약 미지원, 빈 tuple은 Parser가
  전수 확인했지만 해당 식별자가 없다는 서로 다른 상태다.
- 제거: Context에 복사된 뒤 소비가 0건이던 `data_object_evidence_version`,
  `unresolved_qualified_columns`, `unresolved_data_object_usages`와 usage 부재 시 direct 목록을 신뢰하는
  테스트 호환 fallback을 제거했다. CodeNode의 원본 unresolved ledger와 제품 AnalysisHealth 회계는 바뀌지 않는다.
- 회귀: 생산 Context 생성, column 소유 판정, Summary/Table/Rule frame, DBMS 비차단 scheduling, publication과
  response schema를 묶어 `119 passed, 15 subtests passed`다. usage 없이 direct ID만 있는 반례는 빈 배정으로
  고정했다. 실제 LLM·SKT 재분석·Neo4j 제품 쓰기는 수행하지 않았다.
- 문서: 고해성사 HTML에 이 내부 구조와 “전체 객체는 모델에 보내지 않고 필요한 table 발생만 한 번 투영”하는
  경계를 추가했다. 현재 52,684 bytes, 14개 section, 12개 접이식 상세, SHA-256
  `04655c391b9676f5f32758994f3454ee0b44db2e5dcbcbe0aa1849dd743c5e69`다.

## 85. Rule 호출 발생 단일 경로와 내부 메타데이터 최소화 (2026-08-29)

- 원인: Rule 경로가 exact `ast_call_occurrences`와 이름·줄만 가진 `ast_call_lines`를 함께 운반했고,
  occurrence가 없으면 `call("name")`을 만들어내는 호환 경로가 있었다. 같은 이름의 반복·중첩 호출에서
  receiver와 ordered arguments를 잃을 수 있고, Rule evidence와 SemanticFrame에는 최종 resolution 진단이
  중복 복사됐다.
- 수정: Parser call occurrence만 Rule의 단일 입력으로 사용한다. Rule call은 occurrence ID, exact call
  source, receiver, ordered arguments가 모두 있어야 하며 `call("name", receiver|None, [arguments...])`
  하나로만 물질화된다. 이름 전용 fallback과 단일 인자 표기는 거부한다.
- 모델 경계: Rule 모델에는 callee, exact call source, receiver, arguments와 실제 소비되는 callee/API 문맥만
  보낸다. occurrence ID·range·resolution 상태/결과/사유·presence·graph ID는 보내지 않는다.
- 내부 정리: SemanticFrame에 다시 복사되지만 소비처가 없던 resolution scope/status/outcome/reason,
  external identity와 항상 빈 call 필드를 제거했다. 같은 줄 중첩·condition/effect 소유·partial upload를
  검증하는 occurrence ID, exact range, presence, context role과 원본 CallOccurrenceFact 장부는 유지한다.
- 검증: 영향 묶음 `340 passed, 83 subtests passed`; call 문법 직접 반례 `14 passed`; compile과
  `git diff --check` 통과. 단일 인자 제품 표기의 남은 검색 결과는 이를 거부하는 테스트와 C 원문 fixture뿐이다.
- 문서: 고해성사 HTML은 section 5의 call 장부를 현재 `CallOccurrenceFact` 전체 필드와 각 존재 이유로
  교체했다. 57,172 bytes, 14개 section, 12개 접이식 상세, SHA-256
  `970bf72bd065984a10ef843200cadc964245ae7b69ef3329d386242f2f36ea37`이며 여닫는 구조가 일치한다.
- 환경: Neo4j 인증 성공, `dbms|framework|neo4j|system|test`만 존재하고 모두 online이다. 기존 `neo4j`는
  979 nodes, 2,088 relationships이며 DB write·새 DB·LLM·SKT 재분석은 0회다.

## 86. TABLE/COLUMN 최종 의미 응답의 물리 식별자 복창 제거 (2026-08-29)

- 원인: TableDescription LLM response는 table `db|schema|name`과 각 column `name`을 입력 그대로
  다시 출력하도록 강제했다. 실제 제품 결합은 이 반환값을 사용하지 않고 원본 table FQN과 DDL column
  allowlist를 사용하므로, 이 값들은 decode token과 불일치 실패 가능성만 늘리는 중복이었다. 공통 prompt는
  실제 packet에서 이미 사라진 `direct_operations|table_usages|candidates|focus_text`도 계속 설명했다.
- 수정: 최상위 응답을 TABLE `slots`와 exact `columns` map만 갖는 닫힌 schema로 바꿨다. column 물리명은
  map key로 한 번만 존재하며 별도 `name` 값을 출력하지 않는다. Analyzer는 봉인된 TABLE 구조 사실에서
  `db|schema|name`을 복원해 의미 결과에 결합하므로 모델의 식별자 echo를 신뢰하지 않는다.
- prompt: 실제 입력 필드 `observed_operations|usage_meanings|usage_evidence|source.text`와 response shape를
  계약 원문·Framework·DBMS prompt에서 동일하게 설명한다. 근거 부족은 빈 의미 leaf이고 내부 상태와
  missing-context 장부는 Analyzer가 결정한다.
- 검증: step8·table context/response·publication·graph·LLM inventory 묶음
  `101 passed, 125 subtests passed`; 관련 compile 통과, prompt hash 18개 byte-identical, diff 오류 0건이다.
  실제 LLM·SKT 재분석·Neo4j 제품 쓰기는 수행하지 않았다.
- 문서: 기존 최종 고해성사 HTML의 TableDescription 완전 입력·출력 예시를 같은 shape로 갱신했다.
  57,242 bytes, 14개 section, 12개 접이식 상세, SHA-256
  `16fd9f0f41cdf585ad3dc0825049e3338e9d81aa989a7ff163509403bbbe4ed1`이다.

## 87. 미지 외부 형식 Agent 응답과 저장 skill 선택 직선화 (2026-08-29)

- 원인: ExternalSkill LLM은 실제 파싱·연결에 소비되지 않는 `explanation|provenance|value_group`을
  출력했다. `content_signatures`는 검증되지 않은 모델 표식인데 저장 skill 선택 전에 후보를 제외했고,
  한 학습 그룹의 확장자를 Analyzer가 이미 알면서도 모델이 `extensions`를 복창해 검증하지 않은 확장자를
  추가할 수 있었다. 같은 확장자 후보가 둘이면 실제 규칙 검증 전에 즉시 거부했다.
- 수정: 모델 경계를 `ExternalSkillProposal{name,rules[{name,pattern,key_group}]}` 하나로 닫았다.
  Analyzer가 현재 학습 확장자를 결합해 `ExternalSkill`을 만들고, regex 안전성·named key group·positive
  원문 비공백 exact-once coverage·negative 오탐·고객 key 열거를 기존 production 검증기로 판정한다.
  확장자 없는 skill은 빈 extension 장부의 run-only 값이며 persistence API도 저장을 거부한다.
- 저장 skill 선택: 현재 확장자를 선언한 모든 후보를 현재 그룹의 전체 원문과 negative sample에 실제로
  검증한다. 1개만 통과하면 재사용하고, 0개면 Agent가 새 proposal을 한 번 만들며, 2개 이상이면 임의 선택
  없이 명시적 ambiguity로 거부한다. 미검증 문자열 prefilter와 조기 다중후보 거부는 없다.
- 검증: step4 전체·Agent boundary·LLM inventory·external load/link/context 확대 묶음
  `215 passed, 1 skipped, 77 subtests passed`; 관련 compile 통과, proposal top-level은
  `name|rules`, rule leaf는 `key_group|name|pattern`만 존재하고 prompt hash 18개가 일치한다.
  실제 Agent·LLM·SKT 재분석·Neo4j 제품 쓰기는 수행하지 않았다.
- 문서: 기존 최종 고해성사 HTML의 ExternalSkill 완전 출력과 선택 원리를 현재 구현으로 교체했다.
  57,156 bytes, 14개 section, 12개 접이식 상세, SHA-256
  `53461035a004657379fedd66a9098883bde363d82c908ff386ddfcdc92d9c687`이다.

## 88. residual table-pattern 응답 최소화와 검증 pattern 누적 수정 (2026-08-29)

- 원인: residual 모델의 `no_pattern_reason`은 로그 외 소비처가 없고 빈 proposal 자체와 의미가 같았다.
  `matched_skill`은 저장 skill 일치 시 모델 호출 전에 반환하므로 prompt에서 실제 값이 될 수 없었다.
  더 중요한 결함은 두 번째 라운드부터 새로 검증된 pattern을 다음 prompt의 `confirmed_patterns`에 넣지 않아
  같은 pattern을 반복 제안하고 no-progress로 조기 종료할 수 있었다는 점이다.
- 수정: `ResidualPatternResult`를 `table_access_patterns|ddl_transform_override` 두 값으로 닫았다.
  prompt에서 `matched_skill|no_pattern_reason`을 제거하고, 각 라운드에는 initial scan pattern과 앞선
  production 검증 통과 pattern을 모두 활성 pattern으로 전달한다. 빈 제안 로그 이유는 Analyzer가 고정한다.
- 미영향: 미해결 사례의 exact 호출문·기존 pattern 시도 결과·줄번호 원문, table이 아닌 검증 형식,
  framework runtime 제외 목록, DDL allowlist와 transform은 그대로 유지한다. 새 pattern은 기존과 같은
  production matcher·DDL 검증을 통과해야만 다음 라운드와 최종 결과에 들어간다.
- 검증: residual 집중 묶음 `71 passed, 21 subtests passed`; step5 전체·Agent boundary·LLM inventory 확대
  묶음 `226 passed, 31 subtests passed`; 관련 compile, `git diff --check`, prompt hash 18개 byte-identical을
  통과했다. 실제 Agent·LLM·SKT 재분석·Neo4j 제품 쓰기는 수행하지 않았다.
- 환경: Neo4j 인증 성공, `dbms|framework|neo4j|system|test`만 존재하며 모두 online이다. Docker 엔진은
  실행 중이 아니며 Neo4j는 로컬 프로세스로 7474/7687 포트에서 동작한다.
- 문서: 기존 최종 고해성사 HTML의 residual 완전 입력·출력 예시와 반복 원리를 현재 구현으로 교체했다.
  57,318 bytes, 14개 section, 12개 접이식 상세, SHA-256
  `68357f950d7b458446d033d7543e87e7e401ee4c7b76e14f4bbbca05cc4a8829`이다.

## 89. 의미 프롬프트의 임의 output-token 상한 제거 (2026-08-29)

- 원인: 6개 production YAML prompt가 model runtime config와 별도로 2,000·5,000·6,000 output-token
  override를 가졌다. YAML runner는 이 값을 client와 cache key에 그대로 전달한다. 입력 항목 수에 따라
  required response key가 늘어나는 Rule/TABLE/DBMS batch에서 응답이 잘리면 해당 concern 전체가
  `length` 실패가 되므로 완전성에 직접 영향을 주는 제품 상한이었다.
- 수정: residual, Framework/DBMS TABLE description, Framework TABLE usage, DBMS Summary/TABLE usage
  batch의 prompt별 `max_tokens`를 제거했다. 각 호출은 봉인된 model runtime config의 실제 출력 한계를
  사용한다. 모든 production YAML prompt에 별도 override가 없음을 inventory 회귀로 고정했다.
- 미영향: `ROBO_PIPELINE_BATCH_MAX_TOKENS`는 결과를 자르는 상한이 아니라 전체 required population을
  exact-once batch에 나누는 입력 planning 기준이므로 유지한다. 3분할 관심사, 병렬 scheduling, 단일 시도,
  schema 실패 전파도 바꾸지 않았다.
- 새 실패 좌표: 확대 회귀에서 LLM 실패 증거 테스트 6개가 필수 response locale을 설정하지 않아 제품
  호출 전 실패했다. 제품에 기본 locale fallback을 넣지 않고 해당 경계 테스트가 `ko` locale을 명시하고
  원복하도록 수정했다.
- 검증: 최초 집중 묶음 `116 passed, 63 subtests passed`; LLM 전체·prompt·Agent·관련 의미 경계 확대 묶음
  `169 passed, 67 subtests passed`; 관련 compile, `git diff --check`, prompt hash 18개 byte-identical을
  통과했다. 실제 LLM·SKT 재분석·Neo4j 제품 쓰기는 수행하지 않았다.
- 문서: 기존 최종 고해성사 HTML의 3분할 단계에 runtime 출력 능력과 exact-once input batch의 차이를
  추가했다. 57,737 bytes, 14개 section, 12개 접이식 상세, SHA-256
  `88373542306435ca80c94645ddaf6136ef468586375e5adcc5ee0b8dac758a3b`이다.

## 90. ScanProposal 실패 설명 제거와 상태 계약 정규화 (2026-08-29)

- 원인: `ScanProposal.no_match_reason`은 모델 prose를 최종 ScanResult에 복사했지만 어떤 검증 분기도
  바꾸지 않았다. 실제 제안 채택·거부 사유는 pattern 존재 여부와 production verifier가 이미 결정했다.
  또한 모델 response schema는 `ScanProposal`인데 실제 user message 끝에는 `ScanResult`를 출력하라는
  과거 이름이 남아 있었다.
- 수정: 모델 output에서 `no_match_reason`을 제거했다. matched skill과 custom rule/pattern이 모두 빈
  proposal은 Analyzer가 `table_access_status=unavailable`과
  `model proposed no table access rule`이라는 고정 사유로 변환한다. 지시의 출력 이름도
  `ScanProposal`로 통일했다.
- 최종 계약: `ScanResult.no_match_reason`은 내부 실패 장부로 유지한다. analysis/table access가
  unavailable이면 비어 있을 수 없고, table access가 completed이면 존재할 수 없다. 실제 검증 실패는
  production verifier의 요약으로 기록되며 모델 설명은 관여하지 않는다.
- 새 실패 좌표: 확대 회귀에서 residual fixture 9건이 completed 상태와 실패 사유를 동시에 만들고 있어
  새 계약에 거부됐다. 제품 우회 없이 빈 initial pattern fixture를 unavailable로, 실제 pattern이 있는
  fixture만 completed로 바로잡았다.
- 검증: Scan 집중 묶음 `154 passed, 63 subtests passed`; Scan→step5→context 확대 묶음
  `360 passed, 73 subtests passed`; 관련 compile, `git diff --check`, prompt hash 18개 byte-identical을
  통과했다. 실제 Agent·LLM·SKT 재분석·Neo4j 제품 쓰기는 수행하지 않았다.
- 문서: 기존 최종 고해성사 HTML의 ScanProposal 완전 출력과 Analyzer 소유 상태 설명을 현재 구현으로
  교체했다. 57,688 bytes, 14개 section, 12개 접이식 상세, SHA-256
  `cd828a39b7207b76eead98994c39a849ff896c1b4d9bb990f7a47c6d275a57c3`이다.

## 91. 소비처 없는 Rule 표현 실험 모듈 제거 (2026-08-29)

- 원인: `pipeline/models/rule_semantic_relation.py`의 세 응답 모델은 생산 호출 경로가 0건이고,
  전용 테스트만 자신을 참조했다. 실제 Strategy B 생산 경계는 `rule_meaning_frame.py` 하나다.
- 수정: 미추적 실험 모듈과 그 전용 테스트를 제거했다. 호환 wrapper·주석·대체 경로는 남기지 않았다.
- 영향: 생산 LLM 호출 수·응답 계약·Rule 결과는 바뀌지 않는다. 제품처럼 보이는 죽은 표현법 경로만
  사라져 호출 경계 inventory와 실제 코드가 일치한다.
- 검증: 제거된 심볼과 경로 참조 0건, Rule/LLM 경계 집중 묶음
  `27 passed, 9 subtests passed`. 실제 LLM·SKT 재분석·Neo4j 쓰기는 0회다.

## 92. Summary 단일 호출 경로 직선화 (2026-08-29)

- 원인: Summary는 실제로 항상 한 번 호출됐지만, 한 개짜리 `_summary_slot_batches`와
  `_run_bounded_frame_summary`의 반복·수집·병합, fragment별 stereotype 소유 분기가 과거 분할 구조처럼
  남아 있었다. 생산상 두 번째 호출은 없었으나 코드와 명명이 실제 동작을 불필요하게 감쌌다.
- 수정: Framework와 DBMS 모두 `_run_frame_summary` 하나로 통일했다. 전체 배정 슬롯을 한 번 직렬화하고,
  `slots+summary+stereotype` 폐쇄 schema로 한 번 호출한 뒤 한 번 정규화한다. fragment/batch label,
  조건부 stereotype schema, 한 개짜리 merge loop를 제거했다.
- 미영향: Summary·Table usage·Rule의 3분할 병렬 실행, callee 선후관계, 배정 슬롯 exact-once,
  모델 실패 즉시 전파, KV-cache 공통 prefix는 바꾸지 않았다.
- 검증: 집중 `72 passed, 15 subtests passed`; Step 7·LLM 경계 확대
  `255 passed, 133 subtests passed`; 관련 compile과 `git diff --check` 통과. 실제 LLM·SKT 재분석·
  Neo4j 쓰기는 0회다.
- 문서: 최종 HTML의 과거 fragment 설명을 단일 요청 구조로 교체했다. 57,686 bytes,
  section `14/14`, details `12/12`, SHA-256
  `189da7111ee02f637a7a49e3b49e45061dc0ff115d580b1b36e0749e75937208`이다.

## 93. Summary 지시 계약 동기화와 죽은 수동 hash 목록 제거 (2026-08-29)

- 원인: 단일 Summary 코드로 바뀐 뒤에도 Framework/DBMS prompt가 `bounded fragment`, owner fragment의
  조건부 `summary/stereotype`을 지시했다. 실제 response schema는 두 필드를 항상 요구하므로 모델에
  상충 지시를 주었다. `llm/prompts/prompt_hashes.json`은 생산·테스트 소비처가 0건인 수동 목록이었다.
- 수정: 두 Summary prompt와 공통 역할 계약을 현재 단일 응답 계약으로 맞췄다. 모든 배정 slot을 채우고
  같은 응답에서 summary와 stereotype을 각각 정확히 한 번 내도록 했다. fragment 조건은 제거했다.
  수동 hash 목록은 삭제했다.
- 봉인 정본: 실행마다 `build_sealed_analysis_inputs`가 `llm/prompts`의 실제 전체 bytes를 직접 읽어
  `prompt_contracts` tree seal에 넣는다. 별도 수동 hash 파일은 필요 없고 오히려 누락 시 거짓 정본이 된다.
- 검증: 제거 파일·심볼 소비처 0건, prompt/response/seal 집중 묶음
  `72 passed, 46 subtests passed`; 실제 LLM·SKT 재분석·Neo4j 쓰기는 0회다.

## 94. 중복 Neo4j 개별 writer 계층 제거 (2026-08-29)

- 원인: 실제 제품 저장은 `build_complete_product_write_plan`이 14개 publication stage를 완전하게 모아
  `product_write_batch` 한 경로로 검증·쓰기·readback한다. 그러나 과거의 `graph/nodes`, 개별
  `graph/edges`, `graph/writers/example_writer.py`가 제품 소비자 없이 전용 테스트만으로 남아 두 개의
  저장 계약처럼 보였다.
- 수정: 제품 소비자 0을 확인한 옛 node writer 12개, CALLS를 제외한 옛 relationship writer 11개,
  example coordinator와 전용 테스트 5개를 제거했다. 중요한 상속 Parser 필드·함수 의미 publication·
  run 품질 분리 검증은 현재 생산 계약인 구조 모델과 `function_meaning_product_properties`로 옮겼다.
- 유지: `graph/edges/calls.py`는 Parser call resolution 결과를 실제 `link_parser_calls_step`에 전달하는
  현재 producer이므로 유지했다. Neo4j client·schema·generation·원자적 product writer와 14개
  publication stage는 변경하지 않았다.
- 검증: 옛 `graph.nodes|graph.writers` import 0건, `graph.edges` production import는 CALLS 1건만 남는다.
  graph·publication·상속·함수 의미·품질 장부 확대 회귀
  `130 passed, 1 skipped, 26 subtests passed`. 실제 LLM·SKT 재분석·Neo4j 제품 쓰기는 0회다.

## 95. 소비처 없는 logging 별칭 파일 제거 (2026-08-29)

- 원인: `shared/events/labels.py`, `log_api.py`, `run_file_handler.py`는 실제 호출자가 0건인 함수 하나 또는
  `runtime_logger` 재수출 wrapper였다. 실제 제품과 공개 import 회귀는 `runtime_logger`,
  `ndjson_event_stream`, `processing_events`를 직접 사용한다.
- 수정: 세 wrapper를 제거하고 호환 alias·주석·대체 경로를 남기지 않았다. 실제 run 파일 handler,
  오류/진행 로그, NDJSON 전달 로직은 현재 단일 구현에 그대로 있다.
- 검증: 제거 경로·심볼 참조 0건, shared events·공개 import·run lifecycle 묶음
  `44 passed, 17 subtests passed`. 실제 LLM·SKT 재분석·Neo4j 쓰기는 0회다.

## 96. DBMS Summary의 죽은 batch 응답 계약 제거 (2026-08-29)

- 원인: 실제 DBMS Summary는 정확한 노드 원문마다 동적 slot schema로 한 번씩 호출하지만,
  `BatchSummaryOutput`, `_collect_keyed_concern.response_model`, 파일 설명은 여전히 Summary도 형제 N개를
  한 응답으로 받는다고 표현했다. 전달된 response model은 Summary 분기와 TABLE 분기 모두에서 쓰이지
  않았고 실제 호출 구조를 가렸다.
- 수정: 죽은 `BatchSummaryOutput`과 전달 인자를 제거했다. Summary는 노드별 단일 호출,
  Rule/TABLE만 같은 부모의 형제를 keyed map으로 묶는 현재 계약을 코드 설명과 함수 경계에 일치시켰다.
  지원하지 않는 concern은 모델 호출 전에 명시적으로 거부한다.
- 미영향: LLM 호출 수·순서·입력·출력 schema·3분할 병렬성·완전성 판정은 바꾸지 않았다.
- 검증: 제거 심볼·인자 참조 0건, 집중 `24 passed`, Step 7 전체
  `210 passed, 82 subtests passed`. 실제 LLM·SKT 재분석·Neo4j 쓰기는 0회다.

## 97. DBMS 형제 Summary의 불필요한 직렬 대기 제거 (2026-08-29)

- 원인: 같은 dependency round·같은 부모 batch의 Summary는 서로 독립인데도 `for` 내부에서 모델 응답을
  하나씩 `await`했다. 다른 ready batch가 적으면 run 공유 LLM 슬롯이 비어 있어도 형제 N개의 지연시간을
  순서대로 더하는 직접 병목이었다.
- 수정: 새 semaphore·고정 상한을 추가하지 않고 기존 `AnalysisHandoff.slots.capacity`만큼 Summary worker를
  사용한다. 각 노드의 source·slot·callee·external packet과 단일 호출·단일 응답 계약은 그대로이며,
  각 worker는 노드를 정확히 한 번만 가져간다.
- 실패 경계: capacity 1에서 첫 schema 실패가 나면 다음 노드를 호출하지 않는다. capacity N에서는 이미
  시작한 최대 N개만 취소·수거하고 아직 가져가지 않은 노드는 호출하지 않는다.
- 검증: capacity 2에서 두 형제 Summary의 동시 in-flight=2와 결과 ID exact-once를 새 회귀로 고정했다.
  기존 capacity 1 fail-fast 호출 수=1도 유지했다. Step 7 전체
  `211 passed, 82 subtests passed`. GPU가 중단 상태라 실제 wall time·throughput 개선은 미검증이며
  1,048.8초 기준선에 수치 승격하지 않았다.
- 문서: 최종 HTML의 Summary 호출 설명에 같은 ready batch가 기존 공용 슬롯을 함께 사용한다는 현재
  동작을 반영했다. 57,823 bytes, section `14/14`, details `12/12`, SHA-256
  `b59a5674aff651daa7fad0a2dfe1a171f14e4b76b9b56312d6cd9b64e4554448`이다.

## 98. Agent의 제품 무관 Todo 도구와 상태 경로 제거 (2026-08-29)

- 원인: Scan/외부 형식 Agent는 source 탐색·SKILL·production verifier·submit 도구만 필요하지만 공통
  stack의 `TodoListMiddleware`가 `write_todos` 도구를 매 요청 schema에 추가했다. Todo 상태는
  `todos.jsonl`과 `todo_update` 진단 이벤트 외 제품·검증·프론트 소비자가 0건이고 최종 proposal에도
  관여하지 않았다. 모델이 이 도구를 선택하면 의미 없는 추가 tool turn까지 생긴다.
- 수정: `TodoListMiddleware`, `TodoTrackerMiddleware`, `write_todos` tool message/통계/NDJSON label과
  전용 파일을 제거했다. Agent는 허용된 탐색·검증·submit 도구만 받으며 ToolEvent·비용·cache ordering·
  반복 guard는 그대로 유지한다.
- 검증: 제거 심볼·이벤트·artifact 참조 0건을 고정했고 stack 회귀에 세 이름 금지를 추가했다.
  Agent·shared events·public import 묶음 `99 passed, 29 subtests passed`, Agent boundary 집중
  `45 passed, 15 subtests passed`. 실제 Agent·LLM·SKT 재분석·Neo4j 쓰기는 0회다.

## 99. 현재 실행에서 무효인 deepagents 보정 의존 제거 (2026-08-29)

- 원인: 공통 stack의 `PatchToolCallsMiddleware`는 초기 state에 과거 AI tool-call과 대응하지 않는
  tool result가 있을 때 합성 결과를 삽입한다. 실제 Analyzer Agent는 매 invoke를 새 `HumanMessage`
  하나로 시작하고 과거 AI/tool state를 재개하거나 subagent에 넘기는 경로가 없어 이 보정은 매번
  아무 동작도 하지 않았다.
- 수정: 무효 미들웨어 주입과 `deepagents` 직접 의존을 제거했다. Scan/외부 형식 Agent는 계속
  LangChain `create_agent`에 현재 필요한 탐색·검증 도구와 비용·ToolEvent·cache 미들웨어만 직접 받는다.
- 미영향: Agent 도구 목록, 구조화 응답 schema, 호출 횟수, 비용 회로 차단, transcript·usage 적재,
  실제 LangChain/LangGraph 의존은 바꾸지 않았다.
- 검증: production `deepagents|PatchToolCallsMiddleware` 참조 0건을 확인하고 Agent·public import 회귀
  `78 passed, 32 subtests passed`를 통과했다. 실제 Agent·LLM·SKT 재분석·Neo4j 쓰기는 0회다.

## 100. 의미 호출 직전 외부 원문 전달 경로 직선화 (2026-08-29)

- 원인: Step 6의 외부 문맥 객체는 연결·hash 검증에 필요한 ID·경로·좌표·출처를 소유한다. 그러나
  Step 7은 그 객체를 다시 전체 prompt 사전으로 직렬화한 뒤 최종 packet에서 `text` 하나만 꺼냈다.
  모델에는 잡음이 도달하지 않았지만 내부 자료형과 이름이 실제 소비값을 숨겼고, 테스트만 쓰는 옛
  referenced-symbol/external prompt serializer도 남아 있었다.
- 수정: 의미 호출용 중간값을 명확한 `external_source_text_by_id: {chunk_id: exact_text}`로 만들고
  Summary packet은 여기서 배정된 외부 원문만 가져간다. 연결 검증용 메타데이터는 Step 6 객체에
  그대로 보존하되 LLM 준비 경로로 복제하지 않는다. 소비자 0인 두 dump 함수와 세 prompt 변환
  메서드를 제거했다.
- 미영향: 최종 `external_evidence`의 key와 exact text, structural profile의 연결 사실, hash 검증,
  referenced symbol slot, LLM 호출 수와 응답 계약은 바뀌지 않는다.
- 검증: 제거 심볼·옛 중간 key 생산 참조 0건, Step 6·Step 7 확대 회귀
  `243 passed, 82 subtests passed`. 실제 LLM·SKT 재분석·Neo4j 쓰기는 0회다.

## 101. 공식 분석 CLI의 실행 입력 계약 복구 (2026-08-29)

- 새 실패 좌표: 전체 회귀 수집 중 `scripts/analyze_data_dir.py`가 존재하지 않는
  `pipeline.execution.analysis_profile`을 import해 공식 CLI 자체를 시작할 수 없었다. Profile 모듈을
  제거하면서 실제 CLI consumer와 partial-upload 검증 필드가 함께 닫히지 않은 원인이었다.
- 수정: CLI/API가 공유하는 `Codebase` 입력에 맞춰 profile의 data/source/DDL, strategy, 기존 Neo4j DB,
  locale, output, `selected_source_ids`, hash-pinned baseline/answer-key 참조를 한 번에 검증·해석한다.
  경로·database·locale·selection·hash 쌍은 모델 호출이나 graph 쓰기 전에 실패한다. 제거된 Todo event를
  표시하던 CLI 분기도 함께 없앴다.
- 미영향: profile은 원문을 담거나 DB를 만들지 않는다. 명시 CLI 값이 profile보다 우선하고, profile이
  없으면 기존 data/settings와 strategy detection을 사용한다. 이번 검증에서 실제 분석은 실행하지 않았다.
- 검증: CLI/profile 집중 `7 passed, 4 subtests passed`; 전체 자동 회귀
  `1434 passed, 4 skipped, 319 subtests passed`. opt-in 실제 Neo4j rollback test는 skip됐고 LLM·SKT
  재분석·Neo4j 제품 쓰기는 0회다.
- 문서: 최종 고해성사 HTML의 자동 회귀 수와 별도 opt-in 실DB rollback 검증·현재 full-suite skip을
  구분해 갱신했다. 57,989 bytes, section `14/14`, details `12/12`, SHA-256
  `cdb67245b8eb2ebbfb806f64dea86e95da927d98e3f8e157d5028b009d0c02c2`이다.

## 102. GPU 전 인도 감사 상태 (2026-08-29)

- baseline: Analyzer `HEAD == origin/main == 3e7bada7bc30371eed998212ab7bcaf9f6c7e9a9`에서 시작한 전체
  refactor diff이며 `git diff --check`는 오류 0이다.
- 보호 경계: `pipeline/steps/step2_load_ast/evidence_ir.py`의 기존 modified WIP는 unstaged 상태로
  보존했고, `scripts/recompute_strategy_a_notation.py`는 untracked·unstaged 상태이며 읽기·실행·stage하지
  않았다. `integrations/catalog.py`의 기존 WIP도 unstaged로 보존한다. Architect repository와
  goal-verify는 실행·수정하지 않았다.
- 완료 증거: 전체 자동 회귀 `1434 passed, 4 skipped, 319 subtests passed`; 최종 HTML 구조
  section `14/14`, details `12/12`; 실제 LLM·SKT 재분석·Neo4j 제품 쓰기 0회다.
- 보류: GPU가 중단된 동안 사용자가 실제 재분석을 금지했으므로 SKT 1,503줄 직접 의미·속도 검증과
  약 5만 LOC 최종 성능 검증은 아직 수행하지 않았다. 따라서 최종 제품 합격과 refactor commit/push도
  이 검증 전에는 승격하지 않는다.

## 103. 소비처 없는 로컬 실험·생성 쓰레기 제거 (2026-08-29)

- 원인: 저장소 정리 중 `_runs`와 `_artifacts`를 모두 잠정 보존했지만 이후 actual-test와 봉인 검증의
  실제 소비 경로를 구분하지 않았다. `_runs`는 마지막 actual 검증의 명시 입력으로 남아 있는 반면,
  `_artifacts`의 173개 과거 표현 실험 파일은 현재 코드·테스트 소비자가 0건이었다.
- 수정: Git 비추적 `_artifacts` 173파일·9,585,509 bytes를 제거하고 ignore 예외도 없애 재등장을
  드러내도록 했다. 전체 test가 만든 project `__pycache__` 72개와 `.pytest_cache`, 빈 docs/logs/target도
  제거했다. repository layout 회귀는 `_artifacts` 재등장을 거부한다.
- 보존: `_runs` 8,714파일·656,057,507 bytes는 SKT/Parser baseline, 봉인 직접 판정과 opt-in actual test의
  입력이므로 크기만으로 삭제하지 않았다. 최종 GPU 검증이 끝난 뒤 실제 소비가 종료됐는지 다시 판정한다.
- 문서: Analyzer AGENTS/README에서 이미 삭제된 docs archive와 `_artifacts`를 현재 자료처럼 안내하던
  설명을 제거하고, 현재 7개 SDD 파일·`_runs`·루트 최종 HTML만 안내한다.
- 검증: repository layout `5 passed`; 전체 자동 회귀
  `1434 passed, 4 skipped, 319 subtests passed`. 회귀가 만든 cache와 빈 디렉터리는 검증 후 다시
  제거했다. 최종 HTML은 57,989 bytes, section `14/14`, details `12/12`, SHA-256
  `cdb67245b8eb2ebbfb806f64dea86e95da927d98e3f8e157d5028b009d0c02c2`다. 실제 LLM·SKT 재분석·
  Neo4j 쓰기는 0회다.

## 104. 임의 상한·절단·skip 생산 경로 감사 (2026-08-29)

- 전수 범위: Agent 도구, LLM runner/prompt, Scan/외부 형식, DDL·Call·Table linking, Step 7 3분할,
  postprocess와 검색 생산 Python에서 cap/truncate/skip/fallback 및 숫자 slice를 다시 분류했다.
- 무손실로 확인: 100줄 source 도구와 후보/grep은 `next_start_line|next_offset`이 있는 pagination이고,
  DBMS `batch_max_tokens`는 전체 node를 exact-once 여러 batch로 나누는 경계다. max output은 모델
  transport 능력이며 길이 종료를 성공으로 바꾸지 않는다. 로그·오류 sample slice와 response-cache
  용량은 제품 population·의미를 자르지 않는다. Rule/Table 호출은 배정 row/RW가 0일 때만 생략한다.
- 열린 외부 계약: Analyzer의 datasource discovery는 Catalog `GET /tables/discovery`를 `max_tables=1000`으로
  한 번 호출하고 `truncated>0`이면 partial dictionary를 공개하지 않고 generation을 실패시킨다. 로컬
  Catalog 1차 source를 읽기 전용 확인한 결과 endpoint는 `max_tables` 1..10000 eager 열거만 지원하고
  cursor/page/snapshot 계약이 없다. 숫자를 10000으로 올리거나 prefix를 정상 결과로 쓰는 것은 금지된
  우회이므로 Analyzer만으로 lossless 완료할 수 없다.
- 필요한 외부 결정: Catalog가 안정된 snapshot ID, deterministic order, cursor, page exact-once와
  마지막 page closure를 제공해야 한다. Catalog 기존 WIP는 수정·stage하지 않았다. 해당 계약이 생기면
  Analyzer가 page를 전부 소비·중복/누락 0을 검증한 뒤 한 번 병합하도록 연결해야 한다.
- 검증 상태: 현재 truncation formal red는 이미 partial merge 전에 실패함을 고정한다. 실제 Catalog 호출,
  LLM·SKT 재분석·Neo4j 쓰기는 0회다.

## 105. SKT 1,503줄 실제 생성과 기존 framework DB 공개 (2026-08-29)

- 입력: `zapamcom10150.c` 244줄과 `zapamcom13010.c` 1,259줄, 합계 1,503줄이다. Parser 결과 2개와
  DDL table 7개·column 73개를 기존 `framework` DB에서만 검증했으며 새 DB와 Docker는 사용하지 않았다.
- 최초 원인: Framework와 DBMS Step 7이 LangGraph state의 `code_nodes`를 제자리에서 바꾸고 반환값에는
  넣지 않았다. LangGraph는 그 변경을 다음 state로 채택하지 않아 실제 LLM 결과가 있어도 제품 계획은
  빈 `meaning_status`를 읽었다. 두 경로 모두 분석된 `code_nodes`를 명시적으로 반환하도록 수정했다.
- 실행: generation `20260829_000846_8fc97de13e9a`, 19.3초, 함수 17·Rule 145·Example 100·
  SearchSegment 17이다. 논리 LLM 호출 40건 중 38건은 봉인 캐시 적중, 실제 호출 입력 16,366·출력
  1,036·서버 캐시 15,360 token, 비용 `$0.0054165`다. OpenAI embedding 17개는 모두 1,536차원이다.
- 공개: generation은 `completed_with_findings`와 `active`, 제품 node 651개·relationship 671개다.
  실패한 선행 generation은 제품 node 0건이고 active scope를 바꾸지 않았다. AnalysisHealth의
  missing/duplicate는 각각 0이며 unresolved call 12건은 업로드 범위 밖 정의로 남았다.
- 품질 격리: finding 35건은 summary `unverified` 16건과 구현 상세 Rule `excluded` 19건이다. 이 실행과
  Python 표기 구문 통과를 제품 의미 합격이나 약 5만 줄 성능 합격으로 승격하지 않는다.

## 106. 동적 table 응답 캐시 alias 왕복 불일치 폐쇄 (2026-08-29)

- 원인: table response model은 내부 field `column_0`에 실제 column 이름을 validation alias로 배정했다.
  캐시는 Pydantic 내부 이름으로 저장했지만 다음 load는 실제 alias를 요구해 table 설명 2건만 매번
  schema mismatch가 났다.
- 수정: 캐시와 LLM 실행 기록은 `model_dump(by_alias=True)`로 실제 wire 이름을 저장한다. 모델 입력이나
  제품 graph에 내부 `column_0`을 추가하지 않으며, 실제 column 이름으로 저장·검증·감사가 일치한다.
- 검증: 일반 alias와 실제 동적 table response의 store→load 왕복, Step 8, 실행 lifecycle을 포함한 집중
  회귀 64건과 후속 관련 회귀 91건을 통과했다. 같은 SKT 전체를 다시 분석하지 않았다.

## 107. Rule 의미의 효과 우선 응답 순서 복구 (2026-08-29)

- 첫 직접 실패 좌표: `zapamcom10150.c` 86·94행. 구조 조건과 오류 효과는 정확했지만 모델은 조건을
  `비어 있지 않음`으로 생성해 오류 branch와 반대 의미를 제품 Rule/Example에 넣었다.
- 원인 역실험: 기존 순서+기존 지침, 기존 순서+모순 금지 지침, 효과 우선+기존 지침은 모두 같은 반대
  의미를 냈다. 효과 우선+모순 금지 지침만 `입력이 유효하지 않은 상태`로 실제 효과와 일치했다.
  지침은 효과를 먼저 판단하라고 했지만 response schema는 `conditions→effects→rows`를 강제한 것이
  직접 원인이었다.
- 당시 수정: Framework와 DBMS가 공유하는 Rule 의미 response schema를 `effects→conditions→rows`로
  바꾸고, condition은 참일 때 실행되는 row effects 및 원문 주석과 모순될 수 없다는 언어 중립 계약을
  추가했다. 이 prompt-only 수정은 후속 실제 generation에서 같은 반례를 막지 못해 §110의
  `effects→rows` 계약으로 대체됐다.
- 검증: 같은 실제 packet의 2×2 단일 호출 4건으로 원인을 분리했고, response order·closed schema·
  prompt/LLM/cache 관련 집중 회귀 91건을 통과했다. 이 4건은 제품 generation 밖 진단 호출이므로
  generation usage ledger에는 포함되지 않는다. active generation은 수정 전 결과이므로 의미 합격으로
  선언하지 않는다.

## 108. 최신 전체 회귀와 최종 문서 동기화 (2026-08-29)

- 전체 자동 회귀 `1204 passed, 2 skipped`, `git diff --check` whitespace 오류 0이다. skip은 환경 의존
  opt-in 경계이며 실제 SKT generation 증거를 대체하지 않는다.
- 최종 고해성사 HTML은 59,013 bytes, section 14·details 12, SHA-256
  `db57f037039345076b4d2ba8bb930a6e92a2b470ae89c4048336f7cf37bef095`다.
- 중간보고 HTML은 27,951 bytes, section 7·details 4, SHA-256
  `1ed348268f3707a2b22956b8c5216090d0296f6dddc4a657707ff758abc5b842`다.
- 두 문서는 실제 1,503줄 생성 수치, Neo4j 공개 수치, 캐시 alias 원인, 86·94행 의미 반례와
  당시 `effects→conditions→rows` 계약을 반영했다. 현재 최종 계약은 §110과 최종 HTML의
  `effects→rows`다.

## 109. staged 책임 경계 확인 (2026-08-29)

- Analyzer 전면 리팩터링 1,022개 path만 staged했다. 삭제된 과거 spec·죽은 로직과 새 단일 제품 경로,
  회귀, 현재 7개 SDD가 한 책임 slice다.
- `integrations/catalog.py`, `pipeline/steps/step2_load_ast/evidence_ir.py`,
  `scripts/recompute_strategy_a_notation.py`의 staged path는 0건이다. 세 파일은 각각 기존 사용자 WIP 또는
  출처 미확정 untracked 보호 대상 그대로 남겼다.
- Architect·goal-verify·Fabric는 읽기·실행·수정·stage하지 않았다. `_runs` 실제 입력과 generation
  산출물도 commit 대상에 넣지 않았다.

## 110. 최종 Rule/Example 단일 의미 경로와 SKT 선택 범위 폐쇄 (2026-08-29)

- 원인 확정: generation `20260829_003719_eabfbb2ec37c`의 86·94행 반대 조건 의미는 Parser,
  Analyzer 조립, Neo4j 저장 중 생긴 변형이 아니라 모델 원시 응답 자체였다. 입력에는 오류 기록·메시지·
  `RC_ERR` 효과가 있었고 prompt도 효과와 모순하지 말라고 명시했지만 모델은 opaque framework predicate
  `mpfm_isnull(...) == RC_NRM`을 `비어 있지 않음`으로 잘못 번역했다. 프레임워크 계약에는 이 API의
  반환 의미가 없으므로 Analyzer가 반대 극성을 추측해 고치는 것도 금지된다.
- 제품 수정: Rule 모델 응답에서 중복·미검증 `conditions` 자연어 map을 제거했다. 모델은 exact
  `effects`와 `rows`만 한 번 반환한다. Rule 조건은 Parser 사실에서 Analyzer가 만든
  `structural_condition` 하나만 유지하고, 제품 `business_condition` 필드와 모든 소비처를 제거했다.
  `target_id`는 모델 입력이 아니라 prompt 감사 기록에만 명시적으로 전달해 최종 17건의 `unknown`을 0으로
  닫았다.
- Example 직선화: 기존 합성기는 이미 확정된 Rule 조건을 다시 Python AST로 parse/unparse하면서 따옴표를
  바꾸고 conjunction을 여러 항목으로 분해했다. 이 232줄 두 번째 변환을 제거하고 Rule의
  `structural_condition` 문자열과 `business_effects` 배열을 그대로 재사용한다. 최종 Example 95건의
  Rule↔Example byte 불일치는 0이다.
- 최종 실제 실행: 기존 `framework` DB에서 SKT 2파일 1,503줄만 generation
  `20260829_005638_ac5f8999f742`로 공개했다. 17.4초, 논리 호출 40건, 응답 캐시 23건, 새 Rule 호출
  17건의 입력 90,003·출력 8,996 token, 비용 `$0.10798425`다. 함수 17, Rule 114, Example 95,
  SearchSegment 17, generation-owned node 615, relationship 635다. 새 DB와 Docker는 사용하지 않았다.
- 전수 회계: Rule 후보 164행은 공개 114·`excluded` 39·`unverified` 11로 정확히 한 번씩 닫혔다.
  AnalysisHealth `missing=0`, `duplicate=0`, unresolved call 12는 선택 범위 밖 정의다. 이전 generation
  대비 Rule은 제거 18·추가 11, Example은 제거 3·추가 12다. 첫 새 격리 좌표는
  `zapamcom10150.c:b000_main_proc::R-119`이며 모델이 구현 상세로 판정해 제품 Rule에서 제외했다.
- 직접 의미: 86·94행 제품 Rule에는 정확한 구조 조건과 입력 오류 기록·메시지·`RC_ERR` 업무 효과가
  있으며 자연어 조건 속성은 0건이다. Python 구문 성공을 의미 합격으로 승격한 것이 아니라, 거짓 극성을
  만들던 미검증 출력 경로 자체를 제거하고 실제 효과를 직접 대조했다.
- 자동 회귀: 전체 `1433 passed, 4 skipped, 319 subtests passed`. 최초 실행 시 잘못된 runtime hash
  환경변수명으로 model/DB 전에 끝난 generation과, `.env` key 전달 순서로 401이 난 generation은 제품
  node 0건이고 active publication을 바꾸지 않았다. 최종 active generation만 위 수치의 제품 결과다.
- 남은 한계: 약 5만 LOC SKT 전체 성능은 GPU 복구 후 마지막 한 번만 측정한다. Catalog pagination은
  snapshot/cursor/page closure 외부 계약이 생기기 전까지 C05로 남으며 기존 Catalog WIP를 수정하지 않는다.

## 111. 현재 인도 지점과 다음 실행 조건 (2026-08-29)

- 검증된 Analyzer 책임 slice는 `f993ac949a0ec395881adfb307fac06796cd87f5`로 `origin/main`에 push됐고,
  push 직후 `HEAD == origin/main`을 확인했다. 보호 대상 `integrations/catalog.py`,
  `pipeline/steps/step2_load_ast/evidence_ir.py`, `scripts/recompute_strategy_a_notation.py`는 staged diff에
  포함하지 않았다.
- 현행 Rule 계약은 모델 응답 `effects + rows`, 제품 Rule의 `anchor_line + structural_condition +
  structural_effects + business_effects`, Example의 byte-identical projection 하나다. 폐기된
  `business_condition` 소비자는 0건이다.
- 사람용 최종 HTML은 60,060 bytes, SHA-256
  `cca80c5c276b3bca96d36479c03938cfc1346c45bf116c348f0d51f96f2bddba`이고, 중간보고 HTML은
  27,733 bytes, SHA-256 `f80528afb441f2e59a18ba0ee59624dbfa8889fb62fbe0baf10bf6809ab95f35`다.
- 바로 다음 제품 변경은 두 외부 조건 중 하나가 충족된 뒤에만 시작한다. Catalog가 안정 snapshot ID,
  deterministic order, cursor/page와 마지막 page closure를 제공하면 C05를 연결한다. GPU가 복구되면
  모든 관련 수정 종료 뒤 약 5만 LOC SKT를 한 번 실행해 제품 규모 성능과 전수 회계를 판정한다.
- 그 전에는 작은 실제 generation을 반복하지 않고 정적 계약·문서 불일치만 수정한다. 1,503줄의
  17.4초를 약 5만 LOC 성능으로 일반화하지 않는다.

## 112. 여러 파일 정답지·baseline 전체 봉인 (2026-08-29)

- 원인: 공식 analysis profile은 validation ref를 단일 파일로만 제한했고, 실행 봉인도 그 파일 bytes만
  검사했다. SKT gold의 `manifest.json`은 하위 정답 파일의 content hash를 소유하지 않으므로 manifest만
  고정해도 routines·tables·relationships·claims가 바뀌는 재현성 구멍이 있었다.
- 수정: validation ref가 파일 또는 디렉터리를 받는다. 파일은 기존 bytes SHA-256을 유지하고, 디렉터리는
  모든 하위 파일의 정렬된 상대 경로와 원본 bytes로 canonical tree SHA-256을 만든다. 봉인에는 파일별
  상대 경로·hash·크기와 총 파일 수가 남는다. 빈 디렉터리와 root 이탈은 실패한다.
- 검증: formal red 2건을 먼저 확인한 뒤 profile·봉인 집중 회귀 `7 passed, 4 subtests passed`, 전체 자동
  회귀 `1435 passed, 4 skipped, 319 subtests passed`를 통과했다. 실제 LLM 호출, SKT 재분석, Neo4j
  쓰기, 새 DB 생성은 수행하지 않았다.
- 사람용 최종 HTML의 입력 봉인 예시도 단일 파일과 전체 directory tree 형식으로 동기화했다.

## 113. 모델 입력의 내부 evidence 명칭과 모호한 고변동 section 제거 (2026-08-29)

- 원인: canonical `StructuralProfile.evidence`는 Analyzer의 exact-once·원문 좌표 결합에 필요한 내부 장부인데,
  모델용 최소 projection도 `evidence`와 slot별 `evidence`라는 같은 이름을 썼다. 실제 payload는 이미 내부
  ID·hash·절대 경로를 제거한 구조 사실이었지만 이름 때문에 내부 증거 메타데이터 전달처럼 보였고 계약의
  “모델에는 evidence를 보내지 않는다”와 불일치했다.
- 수정: 내부 canonical ledger는 그대로 보존하고 모델 wire만 `facts`와 `fact_refs`로 바꿨다. 각 구조 사실은
  한 번만 존재하며 칸이 request-local E key로 참조한다. 하위 함수와 외부 원문은
  `linked_callee_outcomes`·`linked_external_sources`, COLUMN 실제 사용 원문은 `direct_usage_sources`, prompt
  loader의 고변동 section은 `user_template_dynamic_inputs`로 통일했다. 옛 alias는 남기지 않았다.
- 미영향: 모델 출력 schema는 `slots + summary + stereotype` 그대로이고, canonical evidence ID·좌표,
  exact-once ledger, Rule/Table/Summary 3분할과 LLM 호출 수는 바뀌지 않는다.
- 검증: 최초 Summary import/key formal red와 TABLE `usage_evidence` formal red를 확인한 뒤 집중 회귀
  `94 passed, 132 subtests passed`, 전체 자동 회귀 `1435 passed, 4 skipped, 319 subtests passed`를
  통과했다. 실제 LLM 호출, SKT 재분석, Neo4j 쓰기, 새 DB 생성은 0회다.
- 사람용 최종 HTML도 같은 완전한 Summary와 TABLE/COLUMN 입력 예시로 동기화했다. 현재 크기는
  이후 Neo4j 실행 경로 설명까지 반영한 §114의 최신 hash가 정본이다.

## 114. 기존 Neo4j의 구형 Analyzer 자산 일회 정리와 실행 경로 직선화 (2026-08-29)

- 원인: `ensure_schema`가 분석을 시작할 때마다 DB 전체에서 Analyzer 후보 라벨을 찾고,
  `SHOW CONSTRAINTS`로 구형 단일 ID 제약을 판별한 뒤 삭제를 반복했다. 현재 제품이 사용하지 않는
  대문자 `TABLE.schema` 인덱스도 매번 선언했다. 과거 이관 책임이 정상 분석 경로에 남은 우회였다.
- 기존 DB만 사용해 일회 정리했다. `neo4j`의 구형 Analyzer 노드 978개·제약 11개,
  `test`의 구형 Analyzer 노드 2,660개·제약 6개, `framework`의 구형 세대 관리 노드 1개·제약 3개와
  세 DB의 구형 `table_schema` 인덱스를 제거했다. 새 DB와 Docker는 사용하지 않았다.
- 보존 검증: `framework` 현재 제품 노드는 정리 전후 1,807개로 같고, `neo4j`의 Fabric 노드 1개도
  그대로다. `neo4j/test/framework/dbms/system`은 모두 `online/online`이며 금지 라벨,
  구형 제약, 구형 인덱스는 0건이다.
- 제품 수정: `ensure_schema`는 이제 현재 generation-scoped 제약을 `IF NOT EXISTS`로 선언만 한다.
  DB 탐색, 동적 구형 판별, `DROP`, 구형 호환 상수와 보조 함수는 제품 코드에서 제거했다.
- 검증: formal red 2건을 먼저 확인했고, 그래프 회귀 `73 passed, 1 skipped, 12 subtests passed`,
  전체 자동 회귀 `1435 passed, 4 skipped, 319 subtests passed`를 통과했다. 실제 LLM 호출과 SKT
  재분석은 수행하지 않았다.
- 사람용 최종 HTML도 같은 동작으로 갱신했다. 60,380 bytes, section `14/14`, details `12/12`,
  SHA-256 `fdfb1b3594e5738722ac22175c454e983de4d196b06d52eb07d0d1d59a9437fb`다.

## 115. 현재 제품 graph 검색·상세조회 소비자 복구 (2026-08-29)

- 원인 확정: 현재 publisher와 기존 `framework` DB는 `CodeNode/Table/Column/Rule/Example/SearchSegment`,
  `stable_id`, scope/generation을 공개하지만 검색·MCP 소비자는 폐기된 대문자 label, `id`, `graph_owner`,
  `EMBEDDED/EMBED_META`를 읽고 있었다. 실제 첫 실패는 존재하는
  `code:zapamcom10150.c:a000_input_validation`과 `db:ZNGM_COMM_CD_DTL`이 `NODE_NOT_FOUND`가 된 좌표다.
  Rule reader는 제품에 없는 `AFFECTS_TABLE`까지 조회했다.
- 수정: corpus·ranking·상세조회가 현재 제품 graph를 직접 읽는다. Rule/Example은 HAS_RULE/HAS_EXAMPLE
  ordinal로 결합하고, Table 문맥은 CodeNode의 공개 READS/WRITES만 사용한다. `semantic-detail/v3`은
  함수 의미, 구조화 Rule/Example, 사용자 callee, Table/Column 문맥을 한 번씩 반환하며 원문·내부 evidence,
  storage bookkeeping, 중복 `rule_examples`를 반환하지 않는다.
- 벡터 원인과 수정: 구형 인덱스는 빈 `EMBEDDED.embedding`을 가리켰고 기존 vector procedure는 전체
  population top-k 뒤 scope/generation을 사후 필터링했다. `search_segment_embedding`을
  `SearchSegment.embedding`과 두 additional property로 재생성하고 Neo4j 2026.04의 Cypher `SEARCH`
  in-index filter로 바꿨다. 구형 `embedded_code_vec`는 정확한 target 확인 뒤 일회 제거했다.
- 기존 DB 직접 검증: 인증과 `SHOW DATABASES`는 허용된 5개가 모두 online이다. 최종 scope의 함수 상세는
  Rule 3건, Table 상세는 Column 14건, 동일 embedding fingerprint로 읽은 corpus는 17/17 unique,
  저장 vector 직접 질의는 범위 내 top-5와 자기 유사도 1.0을 반환했다. 저장 model
  `openai:text-embedding-3-small`과 기본 실행 설정 `sglang:BAAI/bge-m3` 불일치는 우회하지 않고
  fail-closed하며, 검증에서는 API 호출 없이 fingerprint만 일치시켰다.
- 검증: 검색 집중 회귀 `45 passed, 25 subtests passed`, 전체 자동 회귀
  `1430 passed, 4 skipped, 340 subtests passed`다. 실제 LLM 호출, SKT 재분석, 새 DB와 Docker는
  사용하지 않았다. 보호된 Catalog/evidence IR WIP와 금지 script는 건드리거나 stage하지 않았다.
- 사람용 최종 HTML도 현재 검색·상세조회 경로와 동기화했다. 61,396 bytes, section `14/14`,
  details `12/12`, SHA-256
  `44830c7fb94f092b325d7a6cf4ec3ec6115b4706b8a615fee9f53017b754ecf6`다.

## 116. C-080 Parser 구조 사실과 Analyzer 의미 어댑터 경계 완결 (2026-08-31)

- 원인 확정: Parser의 언어별 구조 사실 생산 WIP와 Analyzer의 공통 evidence 소비 WIP가 각각 존재했지만
  커밋되지 않아, 새 Parser 출력은 기존 Analyzer 정본이 거부하는 producer/consumer 단절 상태였다.
  Oracle remote call 집중 검증에서는 `APP.PKG.FN@REMOTE_DB`의 경로가 `PKG.FN`으로 잘려 실제 첫 실패를
  확인했다. 원인은 PL/SQL listener가 재귀 경로가 아닌 현재 단계의 `general_element_part`만 읽은 것이었다.
- Parser 수정: ANTLR grammar와 parse-tree 좌표에서 callable 이름 경로, call 이름 경로와 DB link,
  parameter, Python binding target·`global/nonlocal`, import scope를 구조 사실로 생산한다. Parser는
  internal/external, 최종 binding, type, linkage와 업무 의미를 결정하지 않는다. PL/SQL 경로는 재귀적으로
  평탄화해 `APP`, `PKG`, `FN` 세 구성요소와 `REMOTE_DB` 식별자를 원문 범위로 보존한다.
- Analyzer 수정: 공통 evidence 검증기가 위 구조 사실의 필드·범위·scope·completeness를 fail-closed로
  검증하고, 언어별 의미 어댑터가 최종 binding을 소유한다. Parser 5개 실제 golden의 AST 본문은 변경 전과
  byte 구조가 같고 FILE root의 evidence만 확장됐다. 다섯 golden 모두 Analyzer 소비기를 직접 통과했다.
- 검증: Parser 전체 `246 passed, 12 skipped`, Analyzer 집중 회귀
  `225 passed, 2 skipped, 10 subtests passed`다. 새 DB·Docker·LLM 호출·SKT 재분석은 0회다.
  Analyzer `3a361cc8ab2034a75db0643233d24b19fd1fc212`, Parser
  `c155af4`를 각각 `origin/main`에 push했다. Catalog WIP와 출처 미확정 script는 수정·stage하지 않았다.
  사람용 최종 HTML도 새 경로 구성요소와 DB link 내부 필드를 포함하도록 동기화했다. 61,917 bytes,
  section `14/14`, details `12/12`, SHA-256
  `d580045cb63a3b3e026b17181b2b25a9ce2db4bcccb0208ebe4d4d54440413c3`다.

## 117. Summary·nested Rule·failure·finding·inventory 책임 경계 폐쇄 (2026-08-31)

- 원인: 17개 실제 Summary 본문은 모두 존재했지만 `FUNCTION.RESPONSIBILITY`가 최종 `summary`와 의미를
  중복 소유했고, source fact 없이 만든 `FUNCTION.INPUT_ROLE`까지 빈 detail 칸으로 판정되어 16개
  `unverified`가 생겼다. 직접 effect가 없는 nested parent control은 child의 완전한 `when` 경로에 이미
  포함되는데도 Rule 모델에 다시 배정됐다. 모델 실패 helper는 이 결정론적 제외까지 `unavailable`로
  덮어썼고, 정상 `excluded` ledger가 제품 차단 finding으로 합산됐다. inventory는 현재 TABLE/COLUMN
  `logical_name + description` 응답 대신 폐기된 `slots` wire를 읽었다.
- 수정: Summary 응답의 필수 `summary`가 whole-function 책임을 한 번만 소유한다. 중복
  `RESPONSIBILITY/INPUT_ROLE` detail 칸을 제거했고 detail 칸이 0개인 함수도 정확한 전체 numbered source와
  target을 보내 한 Summary를 생성한다. direct effect 없는 `nested_control` 행은 모델 경계 전에
  `implementation_detail/not_business`로 결정론 종결하고, 모델 실패는 model-owned 행에만
  `unavailable`을 만든 뒤 결정론적 제외와 합친다. `blocking_total`은 `excluded`를 제외하며 call/import
  unresolved는 별도 run-quality 차단을 유지한다. inventory는 현재 TABLE/COLUMN wire와 Rule의
  model-owned/deterministic 경계를 직접 읽는다.
- sealed 재생: generation `20260831_063541_470a779494a7`의 164 Rule 의무는 현재 projection에서 모델 소유
  149행 + 결정론 제외 15행으로 정확히 분할된다. 15행 중 `R866`, `R943`은 기존 응답도 이미
  `implementation_detail`이었고 나머지 13행이 기존 Rule `unverified` 전부다. recorded response schema가
  과거 모델에 배정했음을 증명하는 15행은 `legacy_deterministic_response_rows`로 분리되어 현재 응답
  missing/extra가 각각 0이다. judge의 유일한 false는 재실행하지 않은 기존 graph/findings가 여전히
  `excluded 48 + unverified 13`인 데 따른 `rule_disposition_accounting`이다. 이를 현재 결과처럼
  승격하거나 graph를 쓰지 않았다.
- 검증: 수정된 8개 테스트 모듈의 집중 회귀는 `89 passed, 17 subtests passed`, 전체 Step 7과 관련
  finding/inventory/judge 확대 회귀는 `226 passed, 82 subtests passed`, `git diff --check` 오류는 0이다.
  Neo4j 인증 뒤 `SHOW DATABASES`에서 `neo4j/test/framework/dbms/system`만 존재하고 모두 online임을
  확인한 뒤 sealed inventory/judge를 read-only로 실행했다. LLM 호출·SKT 재분석·Neo4j write·새 DB·
  Strategy A 재실행은 0회이며 추가 API 비용도 0이다.
- 다음 첫 좌표는 sealed call ledger의 `runtime_wrapper_target` 12건이다. Parser가 보존한 call argument의
  grammar-owned literal/identifier 구조와 Analyzer adapter 소비를 연결하되 현재 Parser working tree의
  출처가 다른 WIP는 먼저 별도 보호한다. 그 다음 import 25건을 binding environment와 언어별 구조 사실로
  닫는다. 약 5만 LOC 성능은 GPU 복구 전까지 미검증이다.

## 118. runtime wrapper 12건의 Parser→Analyzer binding 폐쇄 (2026-08-31)

- 원인과 계약: 기존 Parser WIP는 `2.0.0 argumentRanges` 객체 안에 구조 필드를 추가하려 해 sealed wire를
  버전 변경 없이 바꾸는 상태였다. sealed `2.0.0`은 range-only로 고정하고 current structural frontend를
  `2.1.0`으로 올렸다. 새 call payload는 `arguments` 하나만 사용하며 각 원소는
  `range/syntaxKind/literalKind/literalValue/identifier` exact 5필드다. 두 버전 alias와 열린 객체는 거부한다.
- Parser: 모든 frontend가 ordered argument structure를 생산한다. C는 grammar-owned direct string literal과
  identifier만 값으로 공개하고, grouped·복합식은 `expression`으로 닫는다. 인접 C 문자열과 escape를
  결정론적으로 decode한다. Oracle/PostgreSQL은 아직 직접 값 판정 없이 정확한 range의 `expression`만
  생산한다. Parser는 wrapper 의미나 최종 binding을 결정하지 않는다.
- Analyzer: decoder→projection→context가 구조 인자를 단일 truth로 전달한다. wrapper 계약의
  `wrapper + callee_from`을 exact-once 검증하고 첫 인자의 grammar-owned 값이 계약과 맞을 때만 기존 언어
  semantic adapter로 보낸다. 누락·구조 불일치·종류 불일치·blank target은 각각 명시
  `runtime_wrapper_*` dynamic/unresolved로 종결한다. workspace routine과 충돌하는 wrapper 계약도
  fail-closed한다. 유효 target은 기존 internal edge 또는 partial-upload external identity 경로를 그대로 쓴다.
- 실제 재현: `zapamcom10150.c` 43 calls 중 wrapper 2건, `zapamcom13010.c` 340 calls 중 wrapper 10건을
  current Parser로 다시 생성해 Analyzer strict decoder와 classifier에 직접 연결했다. 12건은
  `zngmmcap50000` 3, `zordms0100301` 8, `zordms0100303` 1로 결정됐고 모두 external/preserved,
  `wrapper_unresolved=0`, unique external identity 3이다. wrapper 자체 framework 제외보다 target 변환이 먼저
  적용됨을 함께 검증했다.
- 회귀: Parser 전체는 58 suites, `247 tests`, failures/errors 0, skipped 12다. Analyzer 확대 pytest는
  `373 passed, 2 skipped, 10 subtests passed`, authoritative unittest 전체는 `1195 tests`, skipped 2로
  통과했다. `git diff --check` 오류는 0이다. LLM/API 호출·Neo4j write·새 DB·SKT 재분석은 0회이며,
  Catalog WIP와 출처 미확정 script는 수정·stage하지 않았다.
- 다음 좌표는 import 25건의 binding environment다. 그 다음 LLM 입출력에서 stable/call/source/candidate/
  resolution ID, hash, path, offset/range, grammar reference를 모델 의미 판단에 실제 필요하지 않으면 제거한다.
  exact-once·저장·결과 결합용 ID는 내부에 유지하고, 응답 재결합에 위치만으로 오결합 위험이 실재하는
  구간에만 최소 local key 하나를 허용하며 그 근거와 토큰 비용을 기록한다. 사소한 의미 이득보다 토큰
  손실이 큰 필드도 제거 대상으로 본다. 약 5만 LOC 성능은 GPU 복구 전까지 미검증이다.

## 119. import 25건의 선택 source binding environment 폐쇄 (2026-08-31)

- 원인 확정: 기존 `build_import_resolution_ledger`는 Parser import fact만 받고 선택 source, language,
  module projection, include root를 전혀 받지 않았다. 따라서 active·complete·structured import도 모두
  `insufficient_import_binding_environment` unresolved로 고정했다. sealed 2-file closure에는 C source 두
  개뿐이고 20개 unique target header가 하나도 없으므로 현재 업로드 경계에서의 올바른 실제 종료 상태는
  unresolved가 아니라 external이다.
- 계약과 구현: import 결속을 모델 normalization에서 Step 2의 명시 binder로 분리했다. 환경 미제공과
  완전한 선택 환경을 구분하고 outcome을 `internal|external|excluded|unresolved`로 닫았다. C quoted include는
  현재 파일 디렉터리, quoted/angle include는 봉인된 ordered include root의 exact path만 internal로 승인한다.
  target suffix 후보가 있지만 search path가 없으면 unresolved, 후보 자체가 없으면 external이며 basename
  heuristic은 사용하지 않는다. internal만 `internal_module_ids`를 가지며 C module은 표시용 절대
  `file_path`가 아니라 `code_file_id(source_id)` exact identity로 검증한다.
- ID 경계: occurrence/fact/module ID, range, grammar와 hash는 내부 exact-once ledger·publication closure
  검증에만 유지한다. import binder는 LLM을 호출하지 않고 이 내부 ID를 LLM packet이나 제품 node로
  공개하지 않는다. 따라서 사용자의 ID 제거 원칙과 결속 정확성 양쪽을 보존한다.
- actual: generation `20260831_063541_470a779494a7`의 `zapamcom10150.c` 7건과
  `zapamcom13010.c` 18건을 같은 source/AST closure에서 재생했다. 결과는 external 25,
  unresolved/internal/excluded 0이며 reason은 `target_outside_selected_source_closure` 25다. 두 번의 ledger는
  byte-stable이고 summary SHA-256은
  `7880cbf52b3c1f7a540a69ea46f1d217a1236757c1d43bc6ea01068ec846618e`다.
- 검증: Step 2 확대 `166 passed, 2 skipped`, graph/execution/publication 확대
  `193 passed, 1 skipped, 36 subtests passed`, 전체 pytest
  `1440 passed, 4 skipped, 342 subtests passed`, authoritative unittest
  `1202 tests, skipped 2`다. `git diff --check` 오류는 0이다. LLM/API 호출·Neo4j 접근/write·새 DB·SKT
  재분석은 0회이며 보호된 Catalog WIP와 출처 미확정 script는 수정·읽기·실행·stage하지 않았다.
- 다음 첫 좌표는 LLM 입출력의 정보가치/토큰 감사다. stable/call/source/candidate/resolution ID, hash,
  path, offset/range, grammar reference, 중복 설명을 모델 판단에 실제 필요하지 않으면 제거한다. 내부
  exact-once ID는 모델에 보내지 않고, 순서 결합이 안전하지 않은 지점에만 최소 request-local key를
  허용한다. 그 다음 postprocess/graph/storage/docs를 같은 기준으로 닫는다. 약 5만 LOC 성능은 GPU 복구
  전까지 미검증이다.

## 120. Summary LLM 입출력 정보가치 감사와 ID wire 제거 (2026-08-31)

- 실제 비용/소비: sealed generation `20260831_063541_470a779494a7`의 Summary 17건은 input
  167,159 tokens, output 25,682 tokens였고 응답에 `W<digest>_*` detail value 627개를 요구했다. 이 detail은
  slot completeness 진단 외의 publication/search/product 소비자가 없었으며, 실제 최종 의미 소비는 각
  응답의 `summary|stereotype`뿐이었다. 따라서 미세한 slot별 누락 신호보다 생성·스키마 비용 손실이 훨씬
  크다고 판정했다.
- 새 모델 경계: Framework/DBMS Summary 응답은 닫힌 `summary|stereotype` 정확히 두 필드다. target별
  slot digest가 schema title을 바꾸지 않으며 같은 strategy의 schema는 byte-identical하다. 생산 코드의
  `semantic_slot_wire_ids`, `semantic_fact_wire_bindings`, `semantic_frame_response_model`,
  `normalize_semantic_frame_response`, `slot_specs_json`과 옛 공통 prompt 계약을 consumer 0으로 삭제했다.
- 새 입력: 내부 routine 의무는 `FUNCTION.WHOLE_FUNCTION` 하나다. 모델 구조 입력은 target signature와
  source-order `facts[]`이며 E/W key, stable/fact/occurrence ID, hash, path, range/offset, grammar reference,
  `fact_refs`, `ordered_flow`, `applicable_slots`가 없다. 정확한 source 대조에 실제 필요한 line/lines만 남긴다.
  DATA_OBJECT의 내부 `db:` ID는 모델에서 물리 table 이름으로 투영하고 external source는 key 없는
  `{kind,matched_key,text}` 배열로 별도 전달한다. callee outcome은 signature가 없으면 ID fallback 없이
  fail-closed한다.
- 내부 정확성: canonical evidence/slot ID와 exact-once binding은 internal semantic frame에만 유지한다.
  cycle-unavailable 진단은 callee ID를 노출하지 않는 고정 의미로 만든다. inactive call occurrence는 내부
  구조 장부에는 보존하지만 Summary evidence/prompt에서는 제외한다. external evidence와 cycle dependency는
  whole-summary 의무에 결속되어 누락되지 않는다.
- 계측: 기록된 17건을 새 wire로 결정론 투영하면 response schema는 62,756→10,727 bytes(-82.9%), parsed
  response는 54,335→8,018 chars(-85.2%), structural profile+slot 동적 입력은
  182,634→104,018 chars(-43.0%)다. 새 실제 token 수라고 과장하지 않으며, 이번 slice는 LLM 재호출 없이
  기록·schema·prompt projection으로 측정했다.
- 검증: Summary/Step 7 집중 회귀 `221 passed, 74 subtests`, 전체 pytest
  `1435 passed, 4 skipped, 334 subtests`, authoritative `python -m unittest discover`는
  `1197 tests, skipped 2`다. 첫 `unittest discover -s tests`는 잘못된 top-level 때문에 수집 실패했고
  정본 repo-root 명령으로 재검증했다. LLM/API 호출·Neo4j 접근/write·새 DB·SKT 재분석은 0회다.
- 다음 좌표: 3분할 책임은 그대로다. Summary는 전체 target 흐름만, Rule은 결정론 행의 condition/effect
  의미만, Table은 직접 R/W와 TABLE/COLUMN 의미만 소유한다. 다음 slice는 Rule/Table 및 나머지 LLM
  family를 같은 정보가치 기준으로 감사하되, 위치만으로 안전한 곳은 local key까지 제거하고 오결합 반례가
  실재하는 곳만 최소 key를 유지한다. 약 5만 LOC 성능은 GPU 복구 전까지 미검증이다.

## 121. Rule/Table 의미 wire의 좌표·해시·중복 제거 (2026-08-31)

- 결합 판정: Rule의 condition/effect/row, FunctionTableUsage의 table/access, DBMS batch node는 여러 의미값이
  한 응답에 있어 배열 길이와 순서만으로 값 교환을 탐지할 수 없다. 따라서 local key 자체를 없애지 않고
  `C1/E1/R1/FT1/N1` 연속 순번만 유지했다. 내부 `C415/R423`, node stable ID와 source range는 모델 wire에
  없고 Analyzer map이 응답을 canonical row/node에 다시 결합한다. DBMS의 동일 range hash fallback도 삭제했다.
- schema: Summary·Rule·FunctionTableUsage·TableDescription의 provider JSON schema에서 의미 제약이 아닌
  `title` annotation과 동적 12자리 fingerprint class name을 제거했다. required/properties/
  `additionalProperties:false`와 exact local key는 그대로라 누락·extra·swap 반례를 계속 거부한다.
- FT 입력: `physical_table_id: db:...`를 `table: ...`로 바꾸고 occurrence의 `source_line`은 `line`으로
  축약했다. 빈 `qualified_columns`, access mode와 같은 operation은 key 자체를 보내지 않는다. 봉인 4호출의
  5 occurrence는 모두 이 두 중복 조건에 해당했다.
- 3분할 복구: TableDescription은 더 이상 FunctionTableUsage LLM의 `usage_summary`를 다시 입력으로
  소비하지 않는다. TABLE은 자기 DDL/source 설명과 결정론 직접 READ/WRITE/DML, COLUMN은 자기 source
  설명과 해당 컬럼으로 focus한 원문 text만 받는다. candidate ownership, source function ID와 line은 내부
  감사 장부에 남고 모델 packet에는 없다. 물리 column 이름은 의미 대상이자 exact response binding이라
  유지한다.
- 정보가치 판정: Rule의 full source와 effect `source`는 겉보기 중복이지만 같은 callee를 서로 다른 인자로
  여러 번 호출할 때 E atom의 의미를 정확히 구분한다. 봉인 Rule 420 effect에서 이 결합을 잃는 손해가
  22,492 chars 절감보다 크므로 유지했다. 반대로 nested control range, `IF@line/PFM_CATCH@line` 좌표는
  type/condition/kind와 numbered source에서 복원되어 boolean 관계로 축약했다. 빈 used-symbol/condition
  section은 보내지 않는다.
- 봉인 projection: dynamic packet은 Rule 153,461→149,765 chars(-2.4%), FT 648→343(-47.1%),
  TableDescription 2,345→1,975(-15.8%)다. response schema는 각각 46,541→34,304(-26.3%),
  704→416(-40.9%), 3,182→2,390(-24.9%)다. parsed response는 Rule local key 축약으로
  19,947→19,647 chars(-1.5%)이며 FT/Table 의미 문자열은 바뀌지 않는다. 새 실제 token 수로 과장하지 않는다.
- 검증: 집중 회귀 `290 passed, 104 subtests`, Step7/8 확대 `245 passed, 87 subtests`, 전체 pytest
  `1436 passed, 4 skipped, 334 subtests`, authoritative unittest `1198 tests, skipped 2`다. 실제 LLM/API,
  Neo4j 접근/write, 새 DB, SKT 재분석은 0회다. 보호된 Catalog WIP와 금지 script는 수정·읽기·실행·
  stage하지 않았다.
- 다음 좌표: semantic 3분할 wire 감사는 닫혔다. unknown-project Scan과 residual proposal에는 아직
  모델이 작성하는 pattern `id`와 confirmed-pattern ID가 남아 있다. 다음 slice에서 proposal body와 내부
  stable rule identity를 분리해 Analyzer가 순번 identity를 부여하고, external-format Agent의 rule `name`이
  실제 지속·검증 의미인지 같은 기준으로 판정한다. 약 5만 LOC 성능은 GPU 복구 전까지 미검증이다.

## 122. Scan/residual/external proposal의 모델 작성 ID 제거 (2026-08-31)

- Scan: 모델 response의 raw pattern과 4종 DSL rule에서 `id`를 제거했다. 모델은 regex/operation/DSL
  의미 body만 제안하며 `_proposal_to_scan_result`가 검증 전에 `S1..`/`P1..` 내부 순번을 부여한다.
  known SKILL 정본의 rule ID는 결정론 loader에는 남지만 `read_skill`이 모델에 보여주는 YAML에서는
  제거한다.
- residual: 새 pattern 출력에는 ID가 없다. active confirmed pattern은 의미 body의 `P1..Pn` 요청 map,
  case의 attempted pattern은 해당 순번만 참조한다. stable `pattern_id`, exclusion ID와 regex 중복은
  모델 input에 없다. Analyzer 내부 ledger는 기존 ID로 transform-only 검증 대상을 정확히 결합한다.
- external: 모델 경계를 `ExternalFormatProposal{rules[{pattern}]}`로 줄였다. skill name은
  `external-<extension>-<group ordinal>`, rule name은 `r1..`로 Analyzer가 부여하고 key capture는 `key`로
  고정한다. 검증 tool 결과도 절대 char span 대신 의미 있는 match 길이만 보낸다. persistence path,
  match attribution, exact coverage 정렬과 fingerprint 검증은 그대로다.
- schema: provider-facing 공통 base가 실제 검증 제약이 아닌 `title|description` annotation을 재귀 제거한다.
  실제 필드 이름 `description`은 보존하고 required/properties/enum/pattern/minLength/extra-forbid는 유지한다.
  agent schema는 Scan 9,137→3,353 chars(-63.3%), residual 3,459→1,001(-71.1%), external
  666→335(-49.7%)다. 대표 parsed output은 175→154(-12.0%), 186→163(-12.4%),
  124→58(-53.2%) chars다. 대표 residual 동적 입력은 684→632 chars(-7.6%)다. 봉인 실제 호출이 없어
  실제 token 절감으로 과장하지 않는다.
- 검증: fixture 기대값 1건을 actual `_s`로 교정한 뒤 agents+Step4/5 확대 회귀
  `330 passed, 43 subtests`를 통과했고,
  전체 pytest `1439 passed, 4 skipped, 338 subtests`, authoritative unittest `1201 tests, skipped 2`다.
  실제 LLM/API, Neo4j 접근/write, 새 DB, SKT 재분석은 0회다. 보호 Catalog WIP와 금지 script는
  읽기·수정·실행·stage하지 않았다.
- production 모델 경계 11종과 callsite를 정적 inventory로 다시 닫았다. 남은 path/file index/line은 Agent가
  실제 원문을 선택·검증하거나 사용 발생을 판단하는 데 필요한 tool/semantic selector이며 graph/storage
  identity가 아니다. 다음 좌표는 prompt와 tool 결과의 반복 payload를 같은 영양가 기준으로 감사한다.
  약 5만 LOC 성능은 GPU 복구 전까지 미검증이다.

## 123. Agent 초기 입력·검증 tool 반복 payload 제거 (2026-08-31)

- external-format 최초 user message에서 sample별 `file_index|total_chars|total_lines` inventory를 제거했다.
  이 값과 실제 shape 분포는 어차피 필수 첫 관찰인 `sample_overview`가 전체 sample을 page로 한 번 소유한다.
  verify 결과의 파일 chars 복창도 제거하되 sample 순번, match 수, 대표 key·match 길이·원문, unmatched tail,
  negative 판정은 규칙 품질을 바꾸는 정보라 유지했다.
- `verify_table_regex`의 같은 `(call_regex, offset)` 재호출은 앞선 tool message 전체를 다시 붙이지 않는다.
  `ALREADY_VERIFIED_PAGE`와 다음 offset 또는 다음 단계만 반환한다. 첫 실행 결과와 production matcher cache는
  그대로 유지하므로 재계산도 없다.
- 모든 성공 page에 붙던 부분추출/DDL/op-token 장문 힌트를 제거했다. 현재 page에서 실제 partial entity가
  있을 때만 경계 교정, 완전 추출 DDL 미해소가 있을 때만 범위/패턴 판정, 실제 비어 있지 않은 미인식
  op token이 있을 때만 mapping 교정을 반환한다. 성공·부분·미해소·pagination·동일 page 반복 반례로
  서로 불필요한 힌트가 섞이지 않음을 고정했다.
- 대표 actual string chars는 external 최초 입력 285→115(-59.6%), 동일 verify page 재호출
  204→71(-65.2%), 정상 table verify page 292→89(-69.5%), external verify 167→150(-10.2%)다.
  실제 provider 호출이 없으므로 token 절감으로 과장하지 않는다.
- 검증: 국소 `23 passed`, Agent/Step4 확대 `154 passed, 34 subtests`, 전체 pytest
  `1442 passed, 4 skipped, 338 subtests`, authoritative unittest `1204 tests, skipped 2`다. 실제 LLM/API,
  Neo4j 접근/write, 새 DB, SKT 재분석은 0회다. 보호 Catalog WIP와 금지 script는 읽기·수정·실행·stage하지
  않았다. 다음 좌표는 나머지 filesystem/sample/mining tool page header와 prompt 설명의 반복을 같은
  producer→consumer 기준으로 감사하되, 원문 전수 접근과 pagination closure를 절감 때문에 약화하지 않는다.

## 124. Agent page closure header·tool 설명 단일 소유 (2026-08-31)

- filesystem의 ls/glob/grep/read, external sample overview/range/grep, structure candidate와 code-node listing
  page에서 tool call에 이미 있는 current offset/start/requested range와 body에서 바로 셀 수 있는 returned
  count를 제거했다. `total`과 `next_offset|next_start_line`은 전수 population 종료를 증명하므로 유지했다.
  sample/file selector, source line, candidate index도 page 간 의미 결합에 필요해 유지했다.
- Scan/External system prompt의 `## 도구` 표는 provider가 같은 요청에 보내는 tool schema의 이름·인자·결과
  설명 복창이라 제거했다. workflow의 호출 순서, SKILL/후보 우선, pagination, production verify 의무와
  submit 계약은 system prompt에 그대로 남아 있다. mining page의 매회 고정 “samples 실측 검증 통과”도
  tool schema가 이미 소유하므로 제거했다.
- actual chars는 Scan prompt 6,418→5,698(-11.2%), External prompt 3,974→3,372(-15.1%), 대표
  generic/read/mining header 52→30(-42.3%), 66→40(-39.4%), 69→27(-60.9%)다. 실제 provider 호출이
  없으므로 token 절감으로 과장하지 않는다.
- 검증: 집중 `59 passed, 57 subtests`, 확대 `161 passed, 74 subtests`, 전체 pytest
  `1444 passed, 4 skipped, 340 subtests`, authoritative unittest `1206 tests, skipped 2`다. 실제 LLM/API,
  Neo4j 접근/write, 새 DB, SKT 재분석은 0회다. 보호 Catalog WIP와 금지 script는 읽기·수정·실행·stage하지
  않았다. 다음 좌표는 tool schema 자체의 중복 설명과 raw source excerpt 표현을 감사하되, 제거량이 작고
  판단 손실이 크면 현행을 유지한다.
