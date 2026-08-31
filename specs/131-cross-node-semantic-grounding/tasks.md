# Analyzer 의미 무손실 작업 목록

**Updated**: 2026-08-29

완료 표시는 현재 worktree와 정적 증거가 직접 입증한 범위에만 사용한다.

## A. 완료된 정적 책임 slice

- [x] **A01 Parser 구조 사실과 Analyzer 의미 책임 경계**
  - v2 Parser/Analyzer 계약과 formal boundary tests를 고정했다.
  - Analyzer의 언어별 정규식 재파싱·추측 경로를 제품 해법으로 사용하지 않는다.

- [x] **A02 Summary·Table 입력/출력 노이즈 제거**
  - 내부 ID·hash·range·근거 복창·죽은 질문/confidence 필드를 제거했다.
  - source/symbol/table/callee/external 문맥을 소비 위치에 한 번만 결속했다.
  - Summary·TableDescription의 각 배정 칸은 의미 문자열 하나만 출력한다. 모델 자체
    `status`·부족 사유·`meaning` 포장 객체를 제거하고 Analyzer가 실제 실패 사실로 상태를 판정한다.
  - FunctionTableUsage도 FT key→의미 문자열의 exact root만 사용한다. 반복 `results`·mapped/insufficient
    branch·고정 reason code는 제거하고 빈 의미의 내부 실패 사유는 Analyzer가 만든다.
  - Framework·DBMS prompt가 이 direct-string strict schema와 byte-for-byte 같은 출력 형태를 지시하는지
    회귀로 고정했다. 폐기 wrapper가 prompt에 다시 들어오면 테스트가 실패한다.
  - unknown-project Scan Agent의 도구에 이미 결속된 절대 project root는 모델 user message에서 제거했다.
    선택 source 가시성은 도구 allowlist가 계속 강제하고 inventory·skill·DDL 문맥은 보존한다. glob/grep과
    code-node 조회 사이의 모델-visible 경로도 프로젝트 상대경로로 통일했다.
  - residual table-pattern 입력은 현재 미해결 모집단 전체를 매 라운드에 제공한다. 임의 30건 sample과
    3-round 상한·환경변수를 제거하고, verified progress가 있으면 모집단이 감소하는 동안 계속하며
    무패턴·진전 없음에서만 종료한다. 모델 hard limit을 넘으면 자르지 않고 실패한다.
  - 신규·residual table pattern은 임의 허용 비율 없이 모든 op token 인식, 부분 추출·중의성 0,
    모든 로컬 DDL 해소를 요구한다. 검증된 등록 SKILL만 partial upload의 범위 밖 테이블을 허용한다.

- [x] **A03 LLM 진입점 감사**
  - production 생성 callsite 승인 목록을 정적 테스트로 고정했다.
  - hidden judge/repair와 동일 concern 중복 호출이 없음을 검사한다.
  - provider SDK와 Analyzer runner retry를 모두 0으로 고정했다. 이미 전달된 Summary 실패를 slot 재분할로
    다시 묻는 경로, 210초 application wall timeout, invalid JSON escape 자동 수정도 제거했다.
  - 제거된 Agent call-limit middleware의 소비처 없는 `ROBO_AGENT_SCAN_MAX_CALLS`와
    `ROBO_AGENT_RESIDUAL_MAX_CALLS` 예시 설정도 제거했다.
  - OpenRouter config의 cross-provider `allow_fallbacks`를 false로 고정해 선택 provider 실패가 다른
    serving runtime의 자동 성공으로 바뀌지 않게 했다.

- [x] **A03-1 영속 응답 cache 실행환경 동일성 경계**
  - 모델 이름만 같으면 이전 답을 재사용하던 경로를 제거했다.
  - 실행환경 capability 정본 ref와 SHA-256이 모두 맞을 때만 cache를 허용한다.
  - 실제 필터 적용 메시지와 provider·endpoint·sampling·reasoning·extra body·출력 한도·schema를 key에 넣는다.
  - 의미 없이 중복되던 LangChain 전역 cache 설정을 제거하고 항상 비활성화했다.

- [x] **A03-2 분석 입력 단일 봉인과 동일 bytes 소비**
  - source·AST/diagnostic 대응표·DDL·외부 입력·설정·prompt·계약·정답지·모델 실행환경을 Neo4j client 생성 전에
    `sealed-analysis-inputs.json` 하나로 봉인한다.
  - run manifest는 중복 필드 묶음 대신 단일 봉인 artifact의 ref와 SHA-256만 가리킨다.
  - prompt·LLM 설정·skill·용어집·외부 문서·table linker는 봉인 때 읽은 bytes를 run 전체에서 공유한다.
  - partial upload에서는 선택하지 않은 source를 inventory·봉인·Scan Agent가 읽지 않고, root manifest와
    source가 아닌 실제 외부 입력만 보존한다.
  - 외부 문서 subprocess가 원본 경로를 재개방하던 경로와 run output이 project 입력으로 재유입될 경로를 닫는다.
  - 모델 runtime 정본 부재·hash 불일치는 Neo4j 접속 전에 실패한다.
  - DDL·AST·Framework table linking·context·table description 필수 step 설정 누락은 빈 결과로 skip하지 않고
    공통 `StepConfigurationError`로 실패한다.
  - Framework 함수 의미 분석은 배정 대상 전부가 완료되어야 하며, 죽은 `failed/skipped` 계수나 경고 뒤 부분
    성공 없이 DBMS와 동일하게 불완전 집계를 즉시 실패시킨다.

- [x] **A03-3 partial source population 공개 격리**
  - 전체 population, 서로 다른 partial selection, 같은 partial selection의 scope identity를 구분한다.
  - 동일한 root·strategy라도 `selected_source_ids`가 다르면 active generation pointer를 공유하지 않는다.
  - 선택 순서만 다른 동일 집합은 같은 scope가 되어 반복 실행 안정성을 보존한다.

- [x] **A04 Strategy B Rule/Example 정적 구현**
  - 한 Rule 호출에서 exact effect atom의 업무 의미 문자열과 row 분류 문자열만 받는다. 구조 condition은
    모델이 다시 번역하지 않는다. effect atom별 모델 자체
    status와 `meaning`·`disposition` 포장 객체는 출력하지 않는다.
  - Parser 종료문 식을 Rule 행에 옮길 때 160자 절단 없이 전체 식을 보존한다.
  - Analyzer가 Python·제품 공개를 소유하고 Example은 0-call로 만든다.
  - implementation detail·불충분·호출 실패를 제품 Rule에서 격리한다.
  - 반복 상수 `business_meaning_status`와 복원 가능한 Rule/Example 필드를 제거했다.
  - graph transport가 구조-only 후보를 거부한다.

- [x] **A05 실행 산출물의 spec 밖 이관**
  - `_runs` 8,713파일과 `_artifacts` 164파일을 Analyzer 루트로 이동했다.
  - notation experiment 9파일을 `_artifacts`로 이동했다.
  - audit 47파일, archive 22파일, seal script를 역할별 위치로 이동했다.
  - 활성 spec은 현재 SDD 7파일과 비추적 Python cache 1개만 남겼다.

- [x] **A06 현재 정적 회귀**
  - 실제·Neo4j integration·봉인 Strategy A 테스트를 제외해 `1,283 passed`, `5 skipped`,
    `351 subtests passed`.
  - prompt hash 17개 byte-identical, `git diff --check` 통과.

## B. 남은 구현·감사

- [x] **B01 언어 adapter와 중립 resolution core 완결성 감사**
  - C·Java·Python·SQL capability와 binding/type/linkage 반례별 producer→consumer를 닫는다.
  - partial external, standard/framework exclusion, ambiguous/dynamic 회계 잔차를 확인한다.
  - [x] C adapter는 구조 사실을 후보로만 정규화하고 neutral core가 visibility·type·linkage와 최종
    internal/external/ambiguous를 결정하도록 분리했다.
  - [x] C 선언 순서와 lexical scope/shadowing 계산을 neutral core에서 C adapter의 candidate별
    `linkage_visibility`로 이동했다. 다른 class scope의 visible Java형 후보를 core가 C 규칙으로 다시
    탈락시키지 않는 formal 반례를 통과했다.
  - [x] Java·Python Parser가 callable/call lexical scope와 grammar component를 생산하도록 보강하고
    Parser 집중 회귀 `35 passed`를 통과했다.
  - [x] Python 대문자 호출을 constructor/variable type으로 승격하던 대소문자·initializer regex
    추측 producer를 제거했다. 대문자 함수·소문자 callable class 반례와 Analyzer capability 회귀를
    통과했다.
  - [x] exact callable identity registry가 아닌 C/Java/Python stdlib JSON과 Oracle package-name
    목록을 제거했다. 검증된 framework skill 계약만 별도 주입하며 이름만으로 표준 라이브러리를 추측하지 않는다.
  - [x] 소비처 없이 항상 빈 값이던 `ImportScopePlan.scopes`와 wrapper를 제거하고 canonical import
    occurrence ledger를 실제 소비 위치에서 직접 만든다. import binding이 생긴 것처럼 보이던 우회 이름은 없다.
  - [x] Java의 receiver 없는 같은 enclosing type 직접 호출은 ANTLR scope와 parameter arity만으로 후보를
    만들고 neutral core가 최종 internal/external을 결정한다. 선언 순서가 뒤인 method, overload arity,
    다른 class 동명이인, receiver·constructor 반례를 통과했다.
  - [x] Python adapter는 binding-site 사실이 없는 현재 이름 일치로 내부 CALLS를 만들지 않고 모든 call을
    언어별 dynamic 사유로 exact-once 종료한다.
  - [x] Python function/lambda parameter 이름·원문 범위·lexical scope를 Parser `parameter` 사실로 완전
    회계하고, Analyzer가 같은 scope의 parameter collision을 `python_parameter_binding`으로 닫는다.
    Parser는 최종 binding/type을 만들지 않고 이 내부 사실은 LLM에 보내지 않는다.
  - [x] Python assignment·annotated/augmented assignment·for·with·except·comprehension target을 Parser
    `binding_target` 구조 사실로 exact-once 회계한다. Analyzer는 문법 tree로 단순 이름·unpacking과
    attribute/subscript mutation을 구분하고, 안전한 function/lambda 지역 이름 충돌만
    `python_local_binding_target`으로 닫는다. comprehension은 가시성 환경이 완결될 때까지 승격하지 않는다.
  - [x] Python `global|nonlocal`에 나열된 각 이름을 Parser `scope_directive` fact로 exact-once 회계한다.
    Analyzer는 같은 function/lambda scope의 명시 지시를 지역 target보다 우선하며 실제 외부·상위 binding은
    추측하지 않고 `python_global_binding|python_nonlocal_binding` dynamic으로 닫는다.
  - [x] 기존 Python import entry에 lexical `scopePath`를 보강해 별도 중복 fact 없이 결속 이름을 해석한다.
    명시 import 충돌은 `python_import_binding`, wildcard는 `python_wildcard_import_binding`으로 닫으며 sibling
    function import는 보이지 않는 반례를 통과한다. 실제 module/member 연결은 아직 승격하지 않는다.
  - [x] Java static import는 Parser의 정확한 type path·member·wildcard와 Analyzer CodeNode의 owner ID 및
    callable modifier tree를 결합한다. 명시적/와일드카드 import의 사용자 static method만 연결하고 instance
    method·다른 source import·이름 전용 후보는 연결하지 않는다.
  - [x] 일반 Java constructor callable fact를 기존 함수 분석 단위 `METHOD` AST node와 exact-once로 투영하고,
    같은 type/package 또는 정확한 type/namespace import로 보이는 업로드 정의만 arity에 맞춰 연결한다.
    같은 줄의 같은 이름 overload는 단일 줄 일치로 가정하지 않고 Parser occurrence 순서로 한 번씩 결합한다.
    범위 밖 정의는 external이며 receiver type은 구조 환경 없이 추측하지 않는다.
  - [x] Java record를 기존 `CLASS`, compact constructor를 기존 `METHOD` 분석 단위로 보존해 callable fact와
    exact-once 투영한다. 암시적 parameter가 callable 범위 밖 record header에 있으므로 0-arity로 꾸미지 않고
    compatibility를 `unknown`으로 넘기며, 정확한 owner type의 유일 정의만 neutral core가 연결한다.
  - [x] qualified Java constructor의 각 type identifier range를 Parser가 구조 사실로 생산한다. Analyzer는
    전체 component path가 업로드 candidate owner type과 정확히 같을 때만 연결하며 import·파일명으로 우회하지 않는다.
  - [x] Java `this.method()`의 receiver range가 정확히 `this`인 폐쇄 사례는 같은 owner type의 method 후보로
    해석한다. 그 밖의 receiver expression은 type environment 없이 연결하지 않고 dynamic으로 보존한다.
  - [x] Python comprehension의 가장 바깥 iterable call은 enclosing scope, element·filter·후속 iterable call은
    comprehension scope로 보존한다. Analyzer는 target comprehension scope가 call 경로에 실제 포함될 때만
    지역 결속으로 적용해 `[x() for x in x()]` 양쪽 호출을 구분한다.
  - [x] Python `del` target을 Parser `delete_target`으로 회계한다. 단순 이름 삭제는 compile-time local
    결속으로 적용하고 attribute/subscript 삭제는 기존 grammar tree 판별로 제외한다.
  - [x] Java·Python의 현재 Parser symbol/type/import 구조 사실을 모두 소비한다. Parser fact와 봉인 환경만으로
    target을 증명할 수 없는 receiver dispatch·Python runtime binding은 누락이나 이름 추측 없이 dynamic으로
    exact-once 종료한다.
  - [x] Oracle·PostgreSQL call/callable producer와 SQL adapter를 구조 사실로 닫는다.
    - [x] Oracle call/callable은 qualified name component, DB link, argument, package scope를 Parser fact로
      생산하고 Analyzer Oracle adapter가 identifier 규칙·구조 arity·partial external을 해석한다.
    - [x] PostgreSQL nested PL/pgSQL 좌표를 전체 원문 좌표로 rebasing하고 같은 neutral core 경로에 합친 뒤
      DBMS runtime의 legacy AST CallLinker를 제거한다.
  - [x] standard-library는 봉인 version의 exact callable identity registry가 준비되기 전 이름으로
    분류하지 않는다. 현재 workspace 밖 호출은 제품 CALLS 없이 external 장부로 보존한다.

- [x] **B02 atomic generation 공개 감사**
  - [x] final LangGraph state의 모든 제품 producer field와 streamed call/import ledger 동일성을 검사한다.
  - [x] 14개 닫힌 제품 stage를 dependency order로 조립하고 실행기가 각 batch를 쓴 뒤 한 번만 공개한다.
  - [x] operation·property owner·endpoint·record hash를 DB 독립 계획에서 검증하고, publish transaction에서
    전체 제품과 write ledger를 다시 읽은 뒤 publication-version CAS로 pointer를 전환한다.
  - [x] 제품 node 13종 모두 `(analysis_scope_id, generation_id, stable_id)` 유일성 제약을 가진다.
  - [x] 실패 generation 정리는 `ANALYZER_OWNED`에 의존하지 않고 같은 scope/generation의 새 제품 node도
    제거한다. 이전 active generation은 삭제하지 않고 superseded로 보존한다.
  - [x] pipeline step 내부 Neo4j 직접 writer, 옛 graph closure/helper, 고아 `HAS_HEALTH` 관계를 제거했다.
  - [x] 집중 정적 회귀 `79/79`와 incomplete-stage가 CAS 전에 실패하는 반례를 통과했다.
  - [x] 기존 `test` DB의 UUID 격리 scope와 단일 transaction에서 opt-in rollback integration을 실행했다.
    transaction 안에서 active generation·14 stage·제품 record·call ledger를 확인하고 rollback 뒤 잔존 0건을 확인했다.
  - [x] SearchSegment에 저장하는 전체 text와 embedding vector의 입력 bytes를 일치시켰다. 2,000자 뒤 의미를
    자르던 embedder·설정을 제거하고 provider가 단건을 거부하면 축약 없이 분석을 실패시킨다.

- [x] **B03 남은 저장소 정리**
  - [x] 활성 소비처가 0인 `target/c081-staged-snapshot` 저장소 복제본 1,690파일·15,355,353바이트와
    과거 HTML 렌더링 확인용 PNG를 제거해 Analyzer `target` 노이즈를 닫는다.
  - [x] specs 001~130과 현재 archive/audit의 소비처를 분류했다. 현재 계약 131의 7개 파일만 남기고
    과거 spec은 Git history로, 비추적 archive/audit 72파일은 로컬 산출물로 제거했다.
  - [x] 비추적 `__pycache__`, 소비처 없는 실험 script, obsolete compatibility 파일과 로컬
    `.codegraph`/`.pytest_cache`를 보호 목록과 대조해 제거했다.
  - [x] 금지된 `scripts/recompute_strategy_a_notation.py`는 읽기·수정·실행·stage하지 않았다.

- [x] **B04 constitution 재작성**
  - Parser/Analyzer 경계, Strategy B, no-cap/no-skip, exact-once, atomic publish, 범위 밖 보호를 현재 spec과
    같은 용어로 고정한다.
  - 기존의 전체 graph 삭제 전제와 범위 밖 Architect 경계를 제거하고 헌법 2.0.0으로 정리했다.

- [x] **B05 최종 고해성사 HTML 재작성**
  - 현재 Analyzer만 원문부터 graph/API까지 단계 순서로 설명한다.
  - 모든 실제 LLM prompt의 완전한 입력·출력 shape와 각 필드 존재 이유를 보여 준다. 모델-visible
    W/E/FT/C/E/R/줄범위 key는 실제 요청 전용 결합 key로 기록하고 내부 canonical ID와 혼동하지 않는다.
  - 과거 목표 함수·가짜 경로·모델이 Python을 작성한다는 설명을 제거한다.
  - 현재 파일은 59,087 bytes, 14개 순차 단계, LLM 경계 8종이며 SHA-256은
    `e6e9ee0daf97b37b67bd7dad74c2ede576c892a28c0bad7e3cfd26db721b1cc6`다.

- [x] **B06 전체 자동 회귀**
  - 실제 LLM·SKT·Neo4j 제품 쓰기 없이 현재 정적 구현 전체를 한 번 검증했다.
  - `1461 passed, 4 skipped, 310 subtests passed in 83.76s`; 새 실패 좌표 0건이다.

- [x] **B07 무시되던 API 경로 호환 필드 제거**
  - `/analyze`가 실제로 쓰지 않던 `project_root`, `ddl_dir`를 요청 모델에서 제거했다.
  - 알 수 없는 요청 필드는 조용히 버리지 않고 `extra_forbidden`으로 거부한다.
  - HTTP 경계 집중 회귀 `3 passed`; 실제 LLM·SKT·Neo4j 쓰기 0회다.

- [x] **B08 Search 질의 재작성 LLM 경계 정규화**
  - 숨은 raw HTTP·수동 JSON 부분 복구·실패 시 원질의 우회 경로를 제거했다.
  - 모델 출력은 용어집 근거 `source_text → canonical_term` 목록 하나뿐이며, 사용되지 않던
    `intent`와 모델 작성 `rewritten`을 제거했다. 최종 병기는 Analyzer가 안정된 순서로 조립한다.
  - Search production 모델 진입점은 구조화 단일 호출 1곳으로 inventory에 고정했다.
  - 집중 회귀 `63 passed, 27 subtests passed`와 Search/inventory `29 passed, 2 subtests passed`;
    실제 LLM·SKT·Neo4j 쓰기 0회다.

- [x] **B09 AnalysisContext table 사실의 명시 필드화**
  - `data_object_usages`와 실제 소비되는 `unqualified_identifier_references`를 `extra`에서 이름이 드러나는
    필드로 옮겼다. exact table 배정은 `direct_reads|direct_writes`와 exact usage 발생이 모두 맞아야 한다.
  - usage가 없을 때 direct 목록을 그대로 믿던 옛 테스트 호환 경로와 Context에 복사만 되던 evidence
    version·unresolved column·unresolved table 세 값을 제거했다.
  - 집중 회귀 `119 passed, 15 subtests passed`; 실제 LLM·SKT·Neo4j 쓰기 0회다.

- [x] **B10 TABLE/COLUMN 최종 의미 응답의 물리 식별자 복창 제거**
  - LLM이 반환해도 제품 결합에는 쓰이지 않던 table `db|schema|name` 값을 response schema에서 제거했다.
    Analyzer는 봉인된 TABLE 사실의 물리 식별자를 최종 의미에 직접 결합한다.
  - column은 `[{name, slots}]`가 아니라 입력 DDL column 이름을 exact required key로 한 번만 쓰는
    `{name:{slots}}` map으로 바꿔 누락·추가를 schema에서 거부하고 별도 `name` 값 복창을 없앴다.
  - 실제 packet에 없는 옛 `direct_operations|table_usages|candidates|focus_text`를 설명하던 prompt를
    현재 `observed_operations|usage_meanings|usage_evidence|source.text` 계약과 일치시켰다.
  - 집중 회귀 `101 passed, 125 subtests passed`; prompt hash 18개 byte-identical이다. 실제 LLM·SKT·
    Neo4j 쓰기 0회다.

- [x] **B11 미지 외부 형식 Agent 응답과 저장 skill 선택 직선화**
  - 모델 응답을 실제 소비되는 `name`과 `rules(name,pattern,key_group)`만 가진
    `ExternalSkillProposal`로 축소했다. 소비처가 없던 `explanation|provenance|value_group`, 미검증
    prefilter였던 `content_signatures`, Analyzer가 이미 소유한 `extensions` 복창을 제거했다.
  - Analyzer가 현재 학습 확장자를 proposal에 직접 결합한다. 확장자 없는 skill은 현재 run에서만 쓰며
    reusable catalog 저장을 거부한다.
  - 같은 확장자의 저장 skill이 여러 개면 미검증 표식으로 먼저 고르지 않고, 현재 원문 전체·negative
    sample에 production 검증기를 실행해 정확히 하나만 통과할 때 채택한다.
  - 확대 회귀 `215 passed, 1 skipped, 77 subtests passed`; prompt hash 18개 byte-identical이다.
    실제 Agent·LLM·SKT·Neo4j 쓰기 0회다.

- [x] **B12 residual table-pattern 모델 경계와 라운드 누적 직선화**
  - 모델 응답은 production 검증기가 실제 소비하는 `table_access_patterns|ddl_transform_override`만
    반환한다. 비어 있는 제안 자체로 충분한 `no_pattern_reason` prose는 제거했다.
  - 저장 skill이 이미 일치하면 모델 호출 전에 반환하므로 도달할 수 없던 `matched_skill` prompt 입력을
    제거했다.
  - 두 번째 라운드부터 initial scan pattern뿐 아니라 앞선 라운드에서 검증을 통과한 pattern도 모두
    `confirmed_patterns`로 전달해 같은 제안 반복과 조기 no-progress 종료를 막았다.
  - step5·Agent boundary·LLM inventory 확대 회귀 `226 passed, 31 subtests passed`; prompt hash 18개
    byte-identical이다. 실제 Agent·LLM·SKT 재분석·Neo4j 제품 쓰기 0회다.

- [x] **B13 의미 프롬프트의 임의 출력 상한 제거**
  - residual, Framework/DBMS TABLE 설명, Framework TABLE usage, DBMS Summary/TABLE usage batch에
    따로 있던 2,000·5,000·6,000 output-token override를 제거했다.
  - 각 호출은 봉인된 model runtime config의 실제 출력 한계를 사용한다. required population을 모두
    정확히 한 batch에 배정하는 입력 분할과 3분할 병렬 실행은 그대로 유지한다.
  - 모든 production YAML prompt가 별도 `max_tokens`를 만들지 못하는 회귀를 추가했다. LLM 경계 테스트가
    필수 locale 없이 실행되던 6개 기존 실패는 테스트가 locale을 명시하도록 고쳤고 제품 fallback은 없다.
  - LLM·prompt·Agent·관련 의미 경계 확대 회귀 `169 passed, 67 subtests passed`; prompt hash 18개
    byte-identical이다. 실제 LLM·SKT 재분석·Neo4j 제품 쓰기 0회다.

- [x] **B14 ScanProposal 실패 prose 제거와 성공/실패 상태 배타화**
  - 모델이 작성하지만 검증 분기에 쓰이지 않던 `no_match_reason`을 ScanProposal에서 제거했다. 빈 규칙
    proposal은 Analyzer가 `table_access_status=unavailable`과 고정 사유로 변환한다.
  - 최종 ScanResult에서 completed table access와 실패 사유가 동시에 존재할 수 없도록 계약을 닫았다.
    실제 pattern 검증 실패 사유는 기존 production verifier가 계속 소유한다.
  - 실제 response schema가 `ScanProposal`인데 user message가 `ScanResult`를 요구하던 명명 불일치를
    바로잡고 회귀로 고정했다.
  - Scan→table linking→context 확대 회귀 `360 passed, 73 subtests passed`; prompt hash 18개
    byte-identical이다. 실제 Agent·LLM·SKT 재분석·Neo4j 제품 쓰기 0회다.

- [x] **B15 소비처 없는 Rule 표현 실험 모듈 제거**
  - 생산 참조가 0건이고 전용 테스트에서만 살아 있던 `rule_semantic_relation.py`와 전용 테스트를
    제거했다. 실제 Strategy B 생산 경계는 `rule_meaning_frame.py` 하나만 남는다.
  - 제거 심볼 참조 0건, Rule/LLM 경계 집중 묶음 `27 passed, 9 subtests passed`.

- [x] **B16 Summary 단일 호출 경로 직선화**
  - 실제 한 번 호출하면서도 fragment 분할처럼 보이던 한 개짜리 batch·반복·병합·조건부 response schema를
    제거했다. Framework와 DBMS 모두 전체 슬롯을 `_run_frame_summary` 한 번으로 처리한다.
  - Step 7·LLM 경계 확대 회귀 `255 passed, 133 subtests passed`; 실제 LLM·SKT 재분석·Neo4j 쓰기 0회다.

- [x] **B17 Summary 지시 계약 동기화와 수동 prompt hash 목록 제거**
  - Framework/DBMS Summary prompt의 과거 bounded/fragment/조건부 출력 지시를 실제 단일 response schema와
    일치시켰다. 모델은 전체 슬롯·summary·stereotype을 한 번에 정확히 한 번 출력한다.
  - 소비처 0건인 `llm/prompts/prompt_hashes.json`을 제거했다. 실행 봉인은 실제 prompt tree bytes를
    `SealedAnalysisInputs.prompt_contracts`에서 직접 계산한다.
  - prompt/response/seal 집중 회귀 `72 passed, 46 subtests passed`.

- [x] **B18 중복 Neo4j 개별 writer 계층 제거**
  - 실제 제품 쓰기 소비자 0인 `graph/nodes`, CALLS 외 옛 `graph/edges`, `graph/writers`와 전용 테스트를
    제거했다. 중요한 검증은 현재 publication·구조 모델 계약으로 옮겼다.
  - 실제 `link_parser_calls_step`가 쓰는 `graph/edges/calls.py`와 원자적 product publication 경로는
    유지했다.
  - 확대 회귀 `130 passed, 1 skipped, 26 subtests passed`; 실제 LLM·SKT 재분석·Neo4j 쓰기 0회다.

- [x] **B19 소비처 없는 logging 별칭 파일 제거**
  - 소비자 0인 `labels.py`, `log_api.py`, `run_file_handler.py`를 제거했다. 실제 logger·run handler·NDJSON
    구현은 `runtime_logger`, `ndjson_event_stream`, `processing_events`에 단일 경로로 남는다.
  - 관련 회귀 `44 passed, 17 subtests passed`; 제거 심볼 참조 0건이다.

- [x] **B20 DBMS Summary의 죽은 batch 응답 계약 제거**
  - 실제 노드별 Summary 호출에서 소비되지 않던 `BatchSummaryOutput`과 `response_model` 전달을 제거하고,
    코드 설명을 현재 3분할 계약에 맞췄다. Rule/TABLE keyed batch는 유지한다.
  - Step 7 전체 `210 passed, 82 subtests passed`; 제거 심볼 참조 0건이다.

- [x] **B21 DBMS 형제 Summary의 불필요한 직렬 대기 제거**
  - 같은 ready batch의 독립 Summary를 기존 run 공유 LLM 슬롯 안에서 병렬 worker로 예약한다. 새 상한이나
    별도 semaphore는 없고 입력·응답·exact-once 계약은 동일하다.
  - capacity 2 동시 실행과 capacity 1 fail-fast를 함께 고정했다. Step 7 전체
    `211 passed, 82 subtests passed`; GPU wall time은 미검증이다.

- [x] **B22 Agent의 제품 무관 Todo 도구와 상태 경로 제거**
  - Scan/외부 형식 결과에 소비되지 않는 `write_todos` 도구, Todo middleware, `todo_update`/`todos.jsonl`
    진단 경로를 제거했다. 탐색·검증·submit과 비용/ToolEvent/cache middleware는 유지한다.
  - 관련 회귀 `99 passed, 29 subtests passed`; Agent boundary `45 passed, 15 subtests passed`;
    제거 참조 0건이다.

- [x] **B23 현재 실행에서 무효인 deepagents 보정 의존 제거**
  - 새 HumanMessage로만 시작하는 실제 Agent 실행에서 절대 작동하지 않는
    `PatchToolCallsMiddleware`와 이를 위한 `deepagents` 전체 직접 의존을 제거했다.
  - Agent·public import 회귀 `78 passed, 32 subtests passed`; production 참조 0건이다.

- [x] **B24 의미 호출 직전 외부 원문 전달 경로 직선화**
  - 연결 검증용 외부 메타 전체를 임시 prompt 사전으로 복제했다가 text만 꺼내던 경로를
    `external_source_text_by_id`로 바꿨다. 소비자 0인 옛 prompt serializer도 제거했다.
  - 최종 prompt의 배정 key/exact text는 동일하고 Step 6·Step 7 회귀
    `243 passed, 82 subtests passed`를 통과했다.

- [x] **B25 공식 분석 CLI의 실행 입력 계약 복구**
  - 전체 회귀가 찾은 삭제 모듈 import를 현재 partial-upload/봉인 검증 입력까지 포함한 실행 profile로
    닫고, 제거된 Todo event의 CLI 분기도 제거했다.
  - 전체 자동 회귀 `1434 passed, 4 skipped, 319 subtests passed`; 실제 분석·DB write는 0회다.

- [x] **B26 소비처 없는 로컬 실험·생성 쓰레기 제거**
  - 소비자 0인 `_artifacts` 173파일·9,585,509 bytes와 생성 cache·빈 디렉터리를 제거하고
    `_artifacts` 재등장을 layout 회귀로 금지했다.
  - 마지막 봉인 검증이 실제 소비하는 `_runs`는 보존했다. 전체 자동 회귀
    `1434 passed, 4 skipped, 319 subtests passed`다.

- [x] **B27 임의 상한·절단·skip 생산 경로 감사**
  - pagination·exact-once batch·오류/로그 표본·cache 용량과 실제 population 절단을 구분했다.
  - Analyzer 자체의 의미 population을 조용히 자르는 경로는 0건이다. Catalog discovery의 eager
    `max_tables`만 외부 완전성 한계이며 현재는 partial 공개 전 fail-closed한다.

- [x] **B28 여러 파일 정답지·baseline 전체 tree 봉인**
  - 실행 profile의 validation ref가 단일 파일뿐 아니라 전체 디렉터리를 받을 수 있게 했다.
  - 디렉터리는 정렬된 상대 경로와 모든 원본 bytes로 canonical tree SHA-256을 만들고 파일별 ledger를
    봉인한다. 하위 파일 하나의 변경, 빈 디렉터리, root 이탈, 기대 hash 불일치는 실행 전에 실패한다.
  - profile·봉인 집중 회귀 `7 passed, 4 subtests passed`, 전체 자동 회귀
    `1435 passed, 4 skipped, 319 subtests passed`다. 실제 LLM·SKT 재분석·Neo4j 쓰기는 0회다.

- [x] **B29 Summary 모델 wire의 내부 evidence 명칭 제거**
  - canonical evidence와 좌표는 Analyzer exact-once ledger에 유지하되 모델용 최소 구조 투영은
    `facts`, 칸별 연결은 `fact_refs`로 명명했다. 모델은 내부 evidence ID·occurrence ID·producer identity를
    받지 않으며 각 구조 사실은 한 번만 전달된다.
  - 하위 함수와 외부 원문 입력도 실제 역할이 드러나는 `linked_callee_outcomes`와
    `linked_external_sources`로 통일했다. 호환 alias는 남기지 않았다.
  - TABLE/COLUMN의 실제 사용 원문도 `direct_usage_sources`로 명명하고, prompt loader의 고변동 section을
    `user_template_dynamic_inputs`로 통일했다. Summary·Table·prompt 집중 회귀
    `94 passed, 132 subtests passed`, 전체 자동 회귀 `1435 passed, 4 skipped, 319 subtests passed`다.
    실제 LLM·SKT 재분석·Neo4j 쓰기는 0회다.

- [x] **B30 Neo4j 구형 Analyzer 자산 일회 정리와 실행 중 migration 제거**
  - 기존 `neo4j/test/framework`에서 정확히 확인한 구형 Analyzer 노드·제약·인덱스만 일회 제거하고,
    현재 `framework` 제품과 Fabric/Catalog 자산은 보존했다. 새 DB와 Docker는 사용하지 않았다.
  - `ensure_schema`의 DB 전체 탐색, 동적 구형 제약 판별, 반복 `DROP`을 제거하고 현재
    generation-scoped 제약의 `IF NOT EXISTS` 선언만 남겼다.
  - 그래프 회귀 `73 passed, 1 skipped, 12 subtests passed`, 전체 자동 회귀
    `1435 passed, 4 skipped, 319 subtests passed`를 통과했다. 실제 LLM·SKT 재분석은 수행하지 않았다.

- [x] **B31 현재 제품 graph를 직접 읽는 검색·상세조회 계약 복구**
  - 구형 `FUNCTION/TABLE/RULE/id/graph_owner/EMBEDDED/EMBED_META` 조회를 제거하고 현재
    `CodeNode/Table/Rule/Example/stable_id/SearchSegment` 제품을 직접 읽게 했다.
  - 공개되지 않는 `AFFECTS_TABLE` 소비와 중복 `rule_examples` 투영을 제거했다. 상세조회는 구조화 Rule과
    Example을 한 번만 반환하고 Table 문맥은 공개 READS/WRITES에서 읽는다.
  - 벡터 인덱스는 scope/generation additional property와 Cypher `SEARCH` in-index filter를 사용한다.
    기존 framework DB에서 함수 Rule 3건, Table Column 14건, corpus 17/17, dense top-5를 직접 확인했다.
  - 검색 집중 회귀 `45 passed, 25 subtests passed`, 전체 자동 회귀
    `1430 passed, 4 skipped, 340 subtests passed`다. 실제 LLM 호출·SKT 재분석·새 DB·Docker는 0회다.

- [x] **B32 Summary·nested Rule·failure·finding·inventory 책임 경계 폐쇄**
  - whole-function `summary`와 중복되던 `FUNCTION.RESPONSIBILITY/INPUT_ROLE` detail 칸을 제거하고,
    detail 칸이 0개여도 전체 numbered source로 Summary 한 번을 생성한다.
  - direct effect 없는 nested parent 15행을 모델 경계 밖 `implementation_detail`로 종결한다. 기존
    `unverified` 13행은 모두 이 집합에 포함되고, 나머지 2행은 원래도 모델이 제외한 행이다.
  - 모델 실패는 model-owned 행에만 `unavailable`을 적용하고 결정론적 제외를 보존한다. `excluded`는
    exact-once ledger에 남되 `blocking_total`에서 제외한다.
  - current TABLE/COLUMN wire와 sealed historical Rule response schema를 읽는 inventory로 164 = 149 + 15,
    current missing/extra 0을 확인했다. 변경 모듈 집중 회귀 `89 passed, 17 subtests passed`, Step 7 확대
    회귀 `226 passed, 82 subtests passed`; LLM 호출·SKT 재분석·Neo4j write·새 DB·Strategy A 재실행은
    0회다.

- [x] **B33 Summary detail 627개와 request-local ID wire 제거**
  - [x] sealed 17개 Summary의 기존 input/output/schema 비용과 실제 downstream 소비를 계측한다.
  - [x] 모델 응답을 `summary|stereotype` 두 필드로 닫고 per-fact `W<digest>_*` detail map을 제거한다.
  - [x] 구조 입력을 source-order fact 배열로 바꿔 `E<digest>_*`, `fact_refs`, `ordered_flow`,
    `applicable_slots`를 제거한다. source와 구조를 맞추는 실제 line 좌표는 유지한다.
  - [x] 내부 semantic frame은 whole-summary 의무 하나로 결합해 exact-once와 missing-context 상태를
    유지한다.
  - [x] Framework/DBMS 같은 계약, no-detail-key schema, 토큰/byte 감소와 전체 회귀를 검증한다.
  - sealed 기록 17건의 627 detail value는 최종 소비자가 없었다. 기록 기준 schema bytes는
    62,756→10,727(-82.9%), parsed response chars는 54,335→8,018(-85.2%), 구조 동적 입력은
    182,634→104,018 chars(-43.0%)로 투영된다. 실제 LLM 재호출 없이 계약·기록으로 계측했다.
  - 전체 pytest `1435 passed, 4 skipped, 334 subtests`, authoritative unittest `1197 tests,
    skipped 2`; LLM/API·Neo4j·새 DB·SKT 재분석은 0회다.

- [x] **B34 Rule/Table 및 남은 의미 wire의 ID·중복 제거**
  - [x] sealed Rule/FunctionTableUsage/TableDescription의 입력·schema·출력 비용과 downstream 소비를
    필드별로 계측한다.
  - [x] Rule의 line 기반 C/R key와 DBMS range/hash node key를 짧은 연속 순번 key로 바꾸고 내부
    canonical row/condition identity와 결정론적으로 재결합한다.
  - [x] dynamic response schema title의 fingerprint와 의미 없는 title annotation을 제거한다.
  - [x] FunctionTableUsage 입력에서 내부 `db:` 접두사, 빈 `qualified_columns`, access mode와 같은
    operation을 제거하되 exact table/access 결합은 FT key로 유지한다.
  - [x] TableDescription이 FunctionTableUsage LLM prose를 다시 소비하는 경로를 제거하고 DDL/source,
    직접 R/W/DML, 컬럼 집중 원문만 소비하게 한다. 컬럼 원문의 candidate ownership·line은 모델에
    보내지 않는다.
  - [x] positional swap·누락·extra·빈 optional·batch key 충돌 반례와 전체 회귀를 검증하고 실제
    LLM/API·Neo4j·새 DB·SKT 재분석 없이 절감량을 기록한다.
  - sealed projection의 packet은 Rule 153,461→149,765 chars(-2.4%), FT 648→343(-47.1%),
    TableDescription 2,345→1,975(-15.8%)다. schema는 각각 46,541→34,304(-26.3%),
    704→416(-40.9%), 3,182→2,390(-24.9%)다. Rule effect source는 동일 callee의 서로 다른 인자를
    atom에 결합하는 의미 정보라 유지했다.
  - 전체 pytest `1436 passed, 4 skipped, 334 subtests`, authoritative unittest `1198 tests,
    skipped 2`; LLM/API·Neo4j·새 DB·SKT 재분석은 0회다.

- [x] **B35 Scan/residual/external proposal ID 제거와 schema annotation 축약**
  - [x] Scan의 raw/DSL pattern ID를 모델 schema에서 제거하고 Analyzer가 `S/P` 내부 순번을 부여한다.
  - [x] residual 출력 pattern ID와 입력의 stable `pattern_id`를 제거하고 confirmed/attempted 결합에만
    `P1..Pn` request-local 순번을 사용한다. SKILL exclusion과 model-visible SKILL YAML의 내부 ID도
    제거한다.
  - [x] external-format 출력의 skill name, rule name, key_group을 제거한다. Analyzer가 확장자 그룹 순번과
    rule 순번을 부여하고 capture 이름은 의미 계약 `key`로 고정한다. 검증 tool은 절대 char span 대신
    match 길이만 반환한다.
  - [x] 모든 provider-facing response schema에서 prompt와 중복되는 title·description annotation을
    제거하되 closed/required/enum/pattern/길이 제약을 보존한다.
  - [x] production 모델 경계 11종과 callsite를 정적 inventory로 닫고 Search glossary schema까지 같은
    compact strict base를 적용한다. 원문 선택·검증에 필요한 path/file index/line은 tool/semantic selector로
    유지하며 graph/storage identity와 구분한다.
  - agent response schema는 Scan 9,137→3,353 chars(-63.3%), residual 3,459→1,001(-71.1%),
    external 666→335(-49.7%)다. 대표 parsed output은 각각 12.0%, 12.4%, 53.2% 줄었고 대표 residual
    동적 pattern/exclusion/case 입력은 684→632 chars(-7.6%)다. 실제 token으로 과장하지 않는다.
  - 전체 pytest `1439 passed, 4 skipped, 338 subtests`, authoritative unittest `1201 tests,
    skipped 2`; LLM/API·Neo4j·새 DB·SKT 재분석은 0회다.

- [x] **B36 Agent 초기 입력·검증 tool 반복 payload 제거**
  - [x] external 최초 message의 sample별 chars/lines inventory를 제거하고 필수 `sample_overview`가 크기·줄 수·
    shape 분포를 한 번 소유하게 한다. verify는 파일 크기를 복창하지 않는다.
  - [x] table regex 동일 page 재호출은 cached report 전체를 재전송하지 않고 stop/next 행동만 돌려준다.
  - [x] table regex 성공 page의 장문 실패 힌트를 제거하고 실제 partial/unresolved/op-token 문제에만 해당
    교정을 반환한다.
  - [x] 대표 chars는 external 최초 입력 285→115(-59.6%), 동일 verify page 204→71(-65.2%), 정상
    table verify page 292→89(-69.5%), external verify 167→150(-10.2%)다. 실제 token으로 과장하지 않는다.
  - [x] 국소 `23 passed`, Agent/Step4 확대 `154 passed, 34 subtests`, 전체 pytest
    `1442 passed, 4 skipped, 338 subtests`, authoritative unittest `1204 tests, skipped 2`; 실제
    LLM/API·Neo4j·새 DB·SKT 재분석은 0회다.

- [x] **B37 Agent page closure header·tool 설명 단일 소유**
  - [x] filesystem/sample/mining/code-node page에서 입력 current offset/start/requested range와 본문
    returned count 복창을 제거한다.
  - [x] `total`과 `next_offset|next_start_line`, sample/file selector와 numbered source는 전수 closure 및
    의미 결합에 필요하므로 유지한다.
  - [x] Scan/External prompt의 도구 표를 제거하고 tool schema가 이름·인자·결과 설명을 한 번 소유하게
    하되 workflow의 호출 순서·검증·pagination 의무는 유지한다.
  - [x] Scan prompt 6,418→5,698 chars(-11.2%), External 3,974→3,372(-15.1%), 대표 generic/read/mining
    header는 52→30(-42.3%), 66→40(-39.4%), 69→27(-60.9%)다. 실제 token으로 과장하지 않는다.
  - [x] 집중 `59 passed, 57 subtests`, 확대 `161 passed, 74 subtests`, 전체 pytest
    `1444 passed, 4 skipped, 340 subtests`, authoritative unittest `1206 tests, skipped 2`; 실제
    LLM/API·Neo4j·새 DB·SKT 재분석은 0회다.

- [x] **B38 provider tool description 영양가 감사와 raw excerpt 보존 판정**
  - [x] 실제 OpenAI-compatible wire 기준 baseline을 계측한다: Scan 7,870 chars 중 description 5,371,
    External 3,002 chars 중 description 1,754다. parameter schema title/description은 provider 변환에서
    이미 제거되므로 가짜 절감 대상으로 삼지 않는다.
  - [x] system workflow와 겹치는 단계·예시·운영 팁·실패 열거를 제거하고 purpose, root/selected-source
    경계, page closure, production verifier 의미만 tool description에 남긴다.
  - [x] source line, match 원문, key·match 길이와 unmatched-tail 좌표는 규칙의 과대/과소 매치를 판단하는
    유일 근거이므로 작은 문자 절감을 위해 제거하지 않는다. explicit representative excerpt 표시는 유지한다.
  - [x] 실제 provider wire는 Scan 7,870→3,873 chars(-50.8%), External 3,002→2,026(-32.5%)이고
    description만은 각각 5,371→1,493(-72.2%), 1,754→804(-54.2%)다. source/match excerpt 직접 반례와
    필수 marker를 고정했다. focused `72 passed, 57 subtests`, 확대 `167 passed, 74 subtests`, 전체 pytest
    `1447 passed, 4 skipped, 340 subtests`, authoritative unittest `1209 tests, skipped 2`; 실제
    LLM/API·Neo4j·새 DB·SKT 재분석은 0회다.

- [x] **B39 production 11종 최종 provider request·비용 봉인**
  - [x] Scan/External system prompt의 사고 설명·중복 경고·예시를 제거하되 source 전수 접근,
    pagination, known-SKILL 우선, production verifier, negative/held-out, 정확히 한 번 submit 계약은
    유지한다.
  - [x] 11종 경계를 실제 prompt renderer·provider response schema·OpenAI-compatible Agent tool
    serializer로 한 inventory에 직렬화하고, 모델 계약 key/section에 stable ID·hash·절대 path·range/
    offset·grammar/candidate/resolution 장부가 없음을 fail-closed로 고정한다. 원문 안의 동명 문자열과
    tool의 의미 selector인 상대 path/file/line은 오탐하지 않는다.
  - [x] 봉인 SKT 1,503줄에서 실제 필요한 family와 logical invocation 수를 기존 immutable usage ledger와
    현재 call graph로 대조한다. 현재 공식 단가, 입력·출력 예상치, 보수적 상한과 실행별 budget 설정을
    실제 호출 전에 기록하며 이 task에서는 유료 호출하지 않는다.
  - [x] focused→확대→전체 pytest→authoritative unittest, diff/status/보호 WIP 감사를 통과한 뒤
    SDD·최종 HTML·핸드오프와 commit/push를 동기화한다.

- [ ] **B40 post-refactor SKT 1,503줄 유료 최종 검증 1회**
  - [x] 실행 모집단을 `zapamcom10150.c` 244줄 + `zapamcom13010.c` 1,259줄로 고정하고 source·DDL·
    gold tree·baseline·runtime config·profile을 실행기가 새 run에서 다시 봉인하게 한다. 생성 의미 호출은
    Summary 17 + FunctionTableUsage 4 + Rule 17 + TableDescription 2의 40건만 허용한다.
  - [x] 생성 LLM 외에 OpenAI `text-embedding-3-small` SearchSegment 호출도 provider usage를 버리지 않고
    같은 `usage.jsonl`과 run `CostBudget`에 기록한다. 공식 단가 $0.02/M input을 가격표에 등록하고 usage
    누락·비정상 token·미등록 모델은 성공으로 승격하지 않는다.
  - [ ] 사용자가 기존 생성 비용 $0.360159와 embedding 소액을 포함한 `ROBO_BUDGET_RUN_USD=0.50`
    실행 임계를 확인한 뒤에만 진행한다. 직전 Neo4j 인증/SHOW DATABASES에서
    `neo4j|test|framework|dbms|system`만 online인지 확인하고 다르면 즉시 중단한다.
  - [ ] `ROBO_LLM_CONFIG=gpt54_mini_openai`, response cache OFF, 봉인 profile로 정확히 한 번 실행한다.
    budget/transport/schema/quality 실패를 retry·fallback·축소 재질의로 우회하지 않는다.
  - [ ] 새 usage의 생성 family 40건과 embedding request/token/비용, baseline diff, exact-once closure,
    partial-upload external, 86·94행과 Rule↔Example 직접 의미를 판정하고 SDD·HTML·handoff를 갱신한 뒤
    검증 commit을 push한다.

## C. 작은 실제 SKT 검증

- [x] **C01 작은 SKT Rule 의미 직접 판정**
  - Python syntax와 업무 의미·Rule↔Example 동일성을 분리 판정했다.
  - 첫 실패는 10150의 86·94행 조건 의미가 오류 효과와 반대로 생성된 좌표다. 응답 순서와 지시 보강만으로
    막히지 않았고 framework predicate의 반환 의미도 계약에 없었다. 검증되지 않은 condition 자연어 생성
    경로를 제거하고 구조 condition과 업무 effect만 제품에 공개해 같은 실제 좌표를 닫았다.

- [x] **C02 봉인 SKT 2파일 1,503줄 실제 생성 검증**
  - `zapamcom13010.c` 1,259줄과 `zapamcom10150.c` 244줄만 분석한다.
  - 13010의 직접 caller/callee는 파일 하나만으로 총 제한을 넘으므로 함께 넣지 않는다.
  - 10150의 분석 범위 밖 사용자 호출로 partial-upload `external` 종료를 검증한다.
  - 단계별 wall time, queue, token, cache와 병목 원인을 기록한다.
  - 임의 cap·truncation·skip으로 선택 파일 내부 required population을 줄이지 않는다.
  - generation `20260829_000846_8fc97de13e9a`, 19.3초, 논리 호출 40·캐시 적중 38,
    함수 17·Rule 145·Example 100·SearchSegment 17로 기존 framework DB에 공개했다.

- [x] **C03 선택 범위 전수 회계와 직접 의미 판정**
  - 선택 source baseline diff, exact-once completeness/property ledger, 새 실패 좌표를 먼저 확인한다.
  - 자동 회귀·결정론 전수 회계·봉인 직접 의미 판정 3층을 모두 통과한다.
  - 최종 generation `20260829_005638_ac5f8999f742`: Rule 후보 164행 = 공개 114 +
    구현 상세 제외 39 + 의미 불충분 11, 누락·중복 0. Example 95건은 Rule 구조 조건·업무 효과와
    byte 불일치 0건이다. 86·94행은 구조 조건과 오류 효과만 공개하며 검증되지 않은 자연어 조건은 없다.

- [x] **C04 제품 규모 성능 미검증 명시**
  - GPU 복구 전에는 약 5만 LOC 전체 재분석을 수행하지 않는다.
  - 선택 1,503줄 결과를 전체 SKT 처리 시간이나 처리량으로 일반화하지 않는다.

- [ ] **C05 Catalog 전체 테이블 pagination 계약 연결**
  - Catalog가 snapshot/cursor/deterministic page/exact-once closure를 제공하기 전에는
    `truncated>0` datasource를 제품 합격으로 승격하지 않는다.
  - 계약 제공 뒤 Analyzer는 모든 page를 누락·중복 0으로 병합하고 기존 DDL 합집합·sample 의미를
    유지한다. 기존 Catalog WIP는 이 작업에서 수정하거나 stage하지 않는다.

- [ ] **C06 GPU 복구 후 약 5만 LOC SKT 최종 성능·전수 회계**
  - 모든 관련 수정과 C05 외부 계약 연결 뒤 전체 tree로 봉인한 같은 required population과 정답지를
    마지막에 한 번만 분석한다.
  - baseline diff, 단계별 wall time·queue·token·KV cache·publication, exact-once 누락·중복, 새 실패 좌표를
    함께 판정한다. 임의 cap·truncation·skip·fallback으로 시간을 줄이지 않는다.

- [x] **C07 runtime wrapper call 12건의 Parser→Analyzer binding 폐쇄**
  - sealed Parser IR `2.0.0`의 range-only replay는 유지하고 current `2.1.0`의
    `arguments[{range,syntaxKind,literalKind,literalValue,identifier}]` exact schema를 소비해 실제 target을
    검증한다. `argumentRanges`에 값을 숨겨 싣거나 두 버전 필드의 alias를 허용하지 않는다.
  - wrapper 계약은 이름 집합으로 축약하지 않고 `wrapper + callee_from`을 exact-once 검증한다. 유효한
    grammar-owned target만 기존 언어 adapter로 보내며 구조 누락·종류 불일치는 명시 unresolved로 닫는다.
  - 이름·괄호·문자열 정규식 추측 없이 exact occurrence를 internal/external/excluded/unresolved 중 하나로
    종결하고 nested call과 partial-upload 반례를 함께 검증한다.

- [x] **C08 import 25건의 binding environment 폐쇄**
  - [x] 환경 미제공과 완전한 선택 source/module 환경을 별도 상태로 표현한다.
  - [x] C quoted include는 포함 파일의 디렉터리, angle/quoted include는 봉인된 ordered include root로만
    internal을 승인한다. basename·suffix 후보만으로 결속하지 않는다.
  - [x] 선택 source closure에 target suffix 후보가 있되 search path가 불충분하면 unresolved, 후보가 전혀
    없으면 external, 비활성 import는 excluded로 exact-once 종결한다.
  - [x] outcome을 `internal|external|excluded|unresolved`로 닫고 internal만 module ID를 소유하게 한다.
    occurrence/fact/module ID는 내부 ledger에만 유지하며 LLM packet·제품 node로 공개하지 않는다.
  - [x] no-environment, local/internal, include-root/internal, suffix-only/unresolved, outside/external,
    invalid/computed, inactive, duplicate 반례와 봉인된 실제 25건을 검증한다.

## D. 인도

- [x] **D01 최종 문서 동기화와 diff 감사**
- [x] **D02 보호 WIP 제외 staged allowlist 확인**
- [x] **D03 검증된 Analyzer 책임 slice commit/push**
- [x] A02-3 Residual table-pattern LLM packet에서 소비되지 않는 case/file/line/source/coordinate 상태를 제거하고, 정확한 줄번호 원문을 읽지 못하면 모델 호출 전에 fail-closed한다. (`18 passed, 9 subtests passed`)
