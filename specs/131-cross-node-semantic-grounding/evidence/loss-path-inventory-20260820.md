# Analyzer 의미 무손실 전수조사 원장 — 2026-08-20

이 문서는 A131-01~03의 진행 중 증거 원장이다. 문자열 검색 결과를 결함으로 간주하지 않고,
각 후보의 실제 producer → prompt/raw/parsed → validator → graph → API/MCP 배선을 확인한 뒤
분류한다. 고객 원문·식별자·응답 본문은 기록하지 않고 수량과 계약만 기록한다.

## 1. 기준선과 안전 조건

- 제품 코드 기준선: `c216e674cbac43386f9826632097ca2661395d7d`
- 조사 재개 문서 commit: `9d919c4`
- Analyzer와 Parser는 clean이었다.
- Catalog·Fabric·Architect의 기존 사용자 WIP는 읽기 전용으로 보존한다.
- Neo4j 인증, `verify_connectivity`, `SHOW DATABASES` 성공.
- 허용된 `neo4j`, `test`, `framework`, `dbms`, `system`만 존재하고 모두 online이다.
- 조사 중 상위 workspace의 존재하지 않는 `.env`를 지정한 읽기 전용 집계 1회가 빈 비밀번호로
  인증 거절됐다. Analyzer 정본 `.env`로 즉시 `verify_connectivity`와 `SHOW DATABASES`를 다시
  실행해 성공했으며 서비스/자격증명 장애가 아니라 조사 스크립트 경로 오류였음을 확정했다.
- 제품 코드·DB·서비스 변경 없이 정적 배선과 기존 `_runs/framework` 산출물만 조사했다.

## 2. 판정 분류

| 분류 | 판정 기준 |
|---|---|
| 실제 의미 손실 | canonical 의미·근거·의무·진단이 소비자 도달 전에 잘리거나 다른 prose로 대체됨 |
| 안전한 실행 분할 | 모든 canonical ID와 exact evidence가 보존되고 exact-once 병합·완전성 검증됨 |
| 표시 전용 preview | canonical 값은 별도 보존되고 UI·로그·오류 표시에만 축약값을 사용함 |
| 검색 파생 제한 | graph truth는 보존되지만 검색/embedding/query 파생물의 recall만 제한됨 |
| 명시적 degraded | 누락 범위와 원인이 canonical 상태/finding에 연결되고 정상 결과로 위장하지 않음 |
| 미확정 | 실제 caller/consumer 또는 결합 경로 판정이 남음 |

## 3. 현재까지 확인된 실제 배선

### F-001 — semantic-frame meaning leaf 600자

- producer/schema: `pipeline/models/semantic_frame.py`의 sufficient/partial `meaning`이
  `max_length=600`을 wire schema에 강제한다.
- actual prompt: 기존 Framework run의 유효 `code_summary` 417건 모두 response schema에
  같은 `maxLength`가 포함됐다.
- raw/parsed 실측: 2,059개 slot 중 길이 600인 `meaning`이 28개, 590 초과가 31개였다.
  status는 sufficient 2,037개, partial 22개였다.
- validator/consumer: parsed frame이 projection/finalization을 거쳐 CodeNode extra와 graph
  semantic slot 생산자로 이어진다. `graph/nodes/code_node.py`가 JSON으로 저장하고
  `search/corpus/node_detail.py`가 다시 `SemanticFrame`으로 검증한 뒤 MCP `node_detail(frame)`가
  내보낸다. 경계 도달은 정상 sufficient도 포함하므로 degraded 표지가 없다.
- current Framework graph: analyzer-owned 16,862개, semantic frame node 519개(모두 schema valid),
  slot 2,363개 중 meaning 길이 600인 slot 25개다. prompt 경계 28개는 20개 target에 있었고
  ID 대조 결과 17개 target의 25개 leaf는 graph에서도 그대로 600이었다. 나머지 3개 target의
  `GUARDS_AND_BRANCHES` leaf는 모델 응답 끝 공백 1자를 정상화 시 `strip`해 600→599가 됐다
  (partial 2, sufficient 1). 즉 28↔25 차이는 의미 문자의 탈락이 아니라 trailing whitespace
  canonicalization이며, 25개 실제 의미 leaf가 상한에 닿았다는 결함 판정은 그대로다.
- 전체 prompt parsed leaf는 code summary 2,059, code table 98, component 18, module pre 23,
  module summary 46, table/column description 160으로 합계 2,404개다. module-pre 23개는 intent만
  소비하고 frame을 버리며 component 18개는 합성 frame을 node로 저장하지 않는다. 나머지
  `2,404 - 23 - 18 = 2,363`이 current graph slot 수와 정확히 일치한다.
- 600자 도달은 code-summary 28개뿐이다. 다른 rule별 최대는 table insight 229, component 91,
  module pre/module summary 111, table/column description 143이었다. 공용 schema 결함은 모든 rule에
  존재하지만 기준 실행의 실제 경계 도달은 code-summary에 국한된다.
- 유입 commit: `cd9d63b` (2026-08-12).
- 현재 판정: **실제 의미 손실 계약**. 숫자 증설이 아니라 fact obligation 분해와 exact closure가 필요하다.

### C-001/C-002 — missing context/questions/rejection reason 개수 절단

- projection: `semantic_frame_projection.py`가 중복 제거 뒤 `missing_context[:8]`,
  `questions[:15]`를 반환한다.
- rejection: `mark_semantic_frame_insufficient`가 reason을 중복 제거 뒤 `[:8]`로 줄이고
  그 결과를 모든 slot에 적용한다.
- graph/API: `summary_missing_context`, `questions`가 CodeNode extra에 저장되고 graph의 Question 및
  node-detail/search/API 계열 소비자로 전달된다.
- wire schema도 legacy/summary/table 계열에 같은 8/15 상한을 반복한다.
- current Framework graph: slot status sufficient 2,338, partial 24, insufficient 1이며 slot별
  missing-context 최대값은 8이다. Question 66개와 HAS_QUESTION 66개가 있다.
- prompt parsed 기준 non-empty missing-context는 code slot 22개/항목 82개(8개 도달 slot 4),
  table slot 2개/항목 5개, module slot 1개/항목 1개다. projection의 node별 dedupe/상한 뒤 graph
  Question 66개가 됐으므로 진단 축약이 실제 consumer 경계까지 이어졌다.
- 유입: projection 상한 `cd9d63b`, rejection 상한 `8e1aee3`.
- 현재 판정: **실제 진단 의미 손실**.

### C-003 — linked external evidence 8건/6000자

- `external_evidence.py`는 정렬된 linked chunk를 8건 또는 누적 6000자에서 중단/skip한다.
- skip은 로그만 남고 반환 evidence의 sufficiency, missing-context, finding에 연결되지 않는다.
- step6 context가 이 subset을 받아 summary/table prompt의 `target_linked_external_context_json`에
  넣으므로 LLM 입력 근거가 된다.
- 기존 Framework `code_summary` prompt 418개는 external evidence가 모두 빈 배열이고, current
  Framework graph에도 ExternalChunk/REFER_TO가 0개라 이번 실행 영향은 없다.
- 유입: `30828f7`.
- 현재 판정: **실제 근거 손실 설계, 기준 실행 미발동**.

### C-004 — Framework Catalog discovery 최대 1000 table

- Analyzer `discover_datasource_step.py`는 Catalog에 `max_tables=1000`, `sample_limit=5`를 전달한다.
- Catalog는 `truncated=true`와 함께 이름을 실제로 `[:max_tables]`로 줄인다.
- Analyzer는 warning 뒤 받은 subset만 정상 merge/store하고 canonical incomplete identity를 남기지 않는다.
- DBMS는 이 eager discovery가 아니라 reference-scoped `resolve_data_objects_step`을 사용한다.
- 현재 APAS Framework run은 table description 7개라 1000 경계에 도달하지 않았다.
- current Framework graph table은 7개 전부 TABLE이며 view는 0개다.
- 유입: `bc699ee`.
- 현재 판정: **Framework 구조/의미 손실 설계**. `sample_limit=5`는 별도 sampling 분류를 계속 조사한다.

### C-005/C-009a — DBMS parent source compaction

- `source_compaction_threshold_tokens` 기본값은 12,000이다.
- `range_body.py`는 자식이 있는 큰 DBMS parent의 exact child source를
  `[CHILD_SUMMARY_CANDIDATE ...]`와 `child.summary`로 교체한다.
- `batch_prompt_values.py`의 summary prompt는 이 `body_for`를 사용한다.
- validator의 `model_visible_raw_body`도 같은 child raw 구간을 제거하므로 omitted evidence를
  validator가 다시 볼 수 없다.
- Framework context는 평면이라 이 경로의 직접 대상이 아니다. `_runs/dbms` actual run은 아직 없다.
- 원형 compaction은 `6956d02`, 현재 12,000-token gate/semantic contract 결합은 `d498608` 계열이다.
- 현재 판정: **summary-of-summary에 의한 실제 의미 손실 설계**.

### C-009b — callee summary를 caller의 권위 입력으로 사용

- batch와 fanout prompt의 `callee_interfaces_json`은 callee ID, prose summary, evidence status,
  missing context만 전달하고 callee semantic fact/slot/evidence closure는 전달하지 않는다.
- caller summary LLM이 이 prose를 cross-node 의미 입력으로 사용한다.
- 기준 Framework code-summary prompt 418개 중 139개가 이 packet을 받았고 callee interface 항목은
  합계 521개였다. 따라서 기준 실행에서도 넓게 발동한 경로다.
- callee prose는 semantic frame role meaning을 newline join하며 같은 문자열을 dedupe한 legacy projection이다.
  graph에는 frame이 남아도 다음 caller LLM은 그 frame이 아니라 이 projection만 본다.
- prose handoff 원형은 초기 계열에 있었고 현재 ID/status packet은 `d498608`에서 들어왔다.
- 현재 판정: **summary-of-summary 실제 의미 손실 설계**.

### C-010 — module pre-analysis의 잘린 signature를 다음 LLM 문맥으로 사용

- Framework module pre prompt는 module당 member signature를 최대 80개만 넣고 omitted count를
  표시한다. 이 호출의 semantic frame은 버리고 자연어 intent 한 줄만 남긴다.
- 기존 run 23호출 중 16호출이 상한에 닿았고 생략 signature 합계는 2,568개, 단일 module 최대
  생략은 849개였다. 생성 intent 23개는 모두 다음 418개 code-summary prompt에 module context로
  주입됐다.
- prompt 계약은 header만 intent의 권위 근거라고 쓰지만 signature도 같은 입력에 있고, 반환 intent에는
  source/evidence closure가 없다. 따라서 단순히 80을 없애는 대신 signature를 권위 입력에서 제거하거나
  member obligation/ref closure로 바꿔야 한다.
- 유입: `_MAX_SIGNATURES`는 `6956d02`.
- 현재 판정: **중간 LLM의 불완전 문맥을 권위 입력으로 재사용하는 실제 손실 설계**.

### C-016 — Agent tool-result 삭제와 대화 summary-of-summary

- 모든 structured agent는 `ContextEditingMiddleware`로 누적 tool 결과를 50,000 token에서 정리하고
  마지막 3개만 유지한다. 75,000 token에서는 같은 LLM의 `SummarizationMiddleware`가 대화 이력을
  자연어 요약으로 치환한다.
- 최종 ScanResult/ExternalSkill이 지워진 원근거나 그 요약에서 만들어질 수 있고 exact evidence ID/closure
  회계가 없다. 상한 도달은 최종 `analysis_status`에 연결되지 않는다.
- 기준 Framework run은 registered skill을 결정론으로 선택해 Scan Agent LLM 0회였고 ExternalChunk도
  0개다. 유일 agent usage인 call-review는 입력 2,928/output 246 token이라 임계 미도달이다.
- 최초 계열 유입: `ContextEditingMiddleware` `c4363f5`, 현재 stack의 두 middleware는 `30828f7` 계열.
- 현재 판정: **summary-of-summary 실제 손실 설계, 기준 실행 미발동**.

### C-017 — unknown external format의 부분 일치를 파일 전체 분석으로 승인

- proposal agent가 실제로 열람/검증하는 positive file은 앞 3개, negative는 앞 5개다. 구조 마이너는
  후보 6개만 반환하고 skill rule 수도 최대 12개다.
- 최종 `validate_external_skill`은 모든 positive/held-out 본문에 규칙을 실행하지만, 파일·구조별
  coverage가 아니라 전체 매치가 하나 이상인지만 요구한다. 적용 시에도 chunk가 하나 이상이면
  ProjectFile 전체를 `analyzed`로 바꾼다.
- 따라서 일부 record shape만 잡은 skill이 미포착 잔차 없이 완료로 승인될 수 있다. preview 4,000자와
  range/grep 표시 상한 자체보다 이 전수 coverage 부재가 근본 원인이다.
- 유입: 외부 agent 표본 `30828f7`, 구조 마이너 `5175a3d`.
- 현재 판정: **실제 external 구조/근거 손실 설계, 기준 실행 미발동**.

### C-018 — unknown/custom Scan Agent 탐색 표본을 완전한 ScanResult로 승인

- unknown project의 scanner seed는 확장자 상위 20, category별 50, manifest 앞 60줄이고 agent 도구는
  directory list 500, glob 200, grep 80, routine 50, read 50줄/range 11줄 상한을 갖는다.
- 제안된 regex는 전 source에 재적용해 오제안은 검증하지만, agent가 보지 못해 제안하지 않은 다른
  wrapper/pattern의 미발견 의무는 회계하지 않는다. completed ScanResult가 전체 pattern coverage를
  주장할 수 있다.
- 기준 Framework run은 known skill contract를 source/DDL 전수 검증해 LLM 없이 선택했으므로 미발동이다.
- 현재 판정: **unknown/custom 경로의 실제 탐색 누락 설계**. tool 출력 상한은 preview이지만 최종
  completeness obligation이 없어서 결합 시 손실이다.

### C-019~C-021 — 고정 hop/길이로 canonical link를 조용히 중단

- C-019: `view_expansion.py`는 view→view 재귀를 깊이 3에서 빈 결과로 종료한다. cycle visited set이
  이미 있으므로 숫자 상한은 유한 catalog 전체 closure를 대신할 수 없다. 유입 `bc699ee`.
- C-020: external wrapper resolver는 exact pass-through wrapper 승격을 2 hop만 수행한다. 더 깊은
  wrapper chain은 REFER_TO 후보를 만들지 않는다. 유입 `aa6c9d7`.
- C-021: external target index는 reference value가 240자를 넘으면 빈 hit로 돌린다. oversize unresolved
  상태나 finding은 없다. 유입 `aa6c9d7`.
- current Framework graph는 TABLE 7개, view 0개, table-to-table R/W 0개, ExternalChunk/REFER_TO 0개라
  세 경로 모두 기준 실행에는 영향이 없다.
- 현재 판정: **실제 canonical relationship 손실 설계, 기준 실행 미발동**.

### C-022/C-023 — model-visible source projection과 UTF-8 ignore

- C-022: summary prompt의 source는 exact raw가 아니라 `project_model_source_literals`를 거친다.
  quote 종류를 없애고 escape를 설명 문자열로 바꾸므로 서로 다른 raw literal이 같은 projection이 될
  수 있다. AST profile이 보완 근거지만 전체 literal expression 의무가 없고 exact source가 모델에 없다.
  유입 `d498608`.
- 결정론 반례 두 쌍(escape 표기 대 같은 설명 문자열, single-quote 대 double-quote 동일 content)을
  함수에 직접 넣었고 두 쌍 모두 같은 projection을 만들었다. 따라서 이름과 달리 역변환 가능한
  lossless projection이 아님은 재현됐다.
- C-023: `SourceFileCache.lines()`는 UTF-8 decode에 `errors="ignore"`를 사용해 invalid byte를 상태나
  finding 없이 제거한 뒤 source/링커/validator가 같은 손상 문자열을 공유한다. `git blame` 유입
  `30828f7`.
- 기준 corpus source 23개는 strict UTF-8 decode가 모두 성공해 C-023은 미발동이다. C-022 projection은
  실제 prompt에 사용됐으나 봉인 의미 판정 전에는 개별 오의미를 주장하지 않는다.
- 현재 판정: C-022/C-023 모두 **실제 source evidence 손실 설계**.

### C-024~C-027 — AST/reference/source 경계의 계속 진행

- C-024: inheritance REFERENCES/INIT_BY 탐색은 ancestor depth 3에서 남은 frontier를 버린다. cycle set과
  finite module graph가 있는데도 incomplete 상태를 남기지 않는다. 유입 `bc699ee`.
- C-025: 손상/비객체 AST JSON은 `read_antlr_json`이 빈 `ParseResult`로 바꿔 해당 파일 노드를 0개로
  만들고 run은 계속한다. `parsed_files`는 호출 횟수로 증가해 성공 회계에서도 구분되지 않는다.
  최초 계열 유입 `1d0a504`.
- C-026: ontology 미등록 AST type은 warning과 앞 5개 sample만 남기고 그 node 자체는 생략한다. 자식은
  보존하지만 wrapper condition/effect의 canonical incomplete 상태가 graph에 없다.
- C-027: source read 실패는 빈 source를 주고 warning만 남긴다. prompt가 `source_status=read_failed`를
  보기는 하지만 slot을 자동 insufficient로 내리거나 finding으로 연결하는 validator 계약이 없다.
- 기준 Framework log에는 read_antlr_json/unknown/source_read/code_text/path fallback 관련 event가 모두
  0건이다.
- 현재 판정: C-024/C-025는 **실제 구조/관계 손실**, C-026/C-027은 **불충분한 degraded 처리**.

### C-028/C-029 — column projection과 source→AST closure 부재

- C-028: DBMS Oracle column projection이 SQL tokenizer 예외를 잡아 빈 identifier tuple로 바꾼다.
  parser-qualified column은 별도로 보존되지만 sole-table/DDL-unique/derived-output column evidence는
  finding 없이 빠지고 table/column semantic prompt도 그 빈 projection을 받는다.
- C-029: step2는 AST directory의 JSON만 순회하고 source file 집합과 parser diagnostic의 hash/status를
  run invariant로 대조하지 않는다. source에 대응 AST가 없거나 stale AST여도 그 file은 분석 모집단에서
  사라진 채 parsed-files 회계가 성립한다.
- 기준 corpus는 source/AST/diagnostic이 각각 23개이고 filePath one-to-one, diagnostic status EXACT 23,
  source SHA-256 mismatch 0, invalid AST 0이라 C-029는 미발동이다. `_runs/dbms`가 없어 C-028 실제 영향은
  아직 측정할 수 없다.
- 현재 판정: C-028은 **불충분한 degraded 처리**, C-029는 **실제 source→AST 구조 손실 설계**.

### C-030 — call-cycle 내부 callee 의미를 prompt에서 제거

- summary dependency planner는 SCC를 깨기 위해 cycle 내부 CALL dependency를 schedule에서 제거한다.
  `AnalysisContext.is_in_cycle`은 설정하지만 실제 summary prompt packet에는 이 상태나 suppressed callee
  obligation을 넣지 않고, 아직 summary가 없는 callee를 목록에서 그냥 제외한다.
- caller exact source에는 call site가 남지만 callee contract가 필요한 의미는 missing-context로 강제되지 않아
  sufficient 결과가 가능하다. 해결은 prose 선후행이 아니라 SCC의 exact interface obligation을 함께
  물질화하고 canonical ID로 병합하는 방식이어야 한다.
- 기준 Framework step7 start는 code node 416, summary dependency edge 517을 기록했으며 in-cycle 값은
  0이라 logger에서 생략됐다. 기준 실행 영향은 없다.
- 현재 판정: **실제 cross-node 의미 손실 설계, 기준 실행 미발동**.

### C-031 — Catalog lineage fallback이 저장되지 않는 edge를 정상 state로 반환

- DBMS post-processing의 Catalog lineage는 code source 해소 실패/중의를
  `__catalog__:<source>` ID로 바꾼다. 그러나 해당 virtual CodeNode를 만들지 않고
  `upsert_reads_writes`는 기존 `ANALYZER_OWNED` source만 MATCH하므로 Neo4j에는 edge가 생기지 않는다.
  state에는 edge가 남아 graph/state가 갈라진다.
- table FQN 누락이나 relation normalization 실패도 flow를 그냥 `continue`하며 per-flow incomplete
  finding/회계가 없다. 경로 중의 warning은 있어도 최종 semantic/lineage 상태와 연결되지 않는다.
- 이 단계는 code semantic frame 생성 뒤 실행돼 누락/추가 lineage가 해당 frame의 table obligation을
  갱신하지도 않는다. `_runs/dbms`가 없어 actual 영향은 미검증이다.
- 유입: Catalog lineage step과 `__catalog__` fallback 모두 `30828f7`.
- 현재 판정: **실제 Neo4j relationship 손실 및 불충분한 degraded 처리**.

### C-032 — 실패·중단·진행 중 partial graph를 정상 소비자가 읽음

- `execute_run`은 active-run slot을 선점한 뒤 `wipe_graph`로 현재 Analyzer 소유 graph를 즉시
  전부 삭제한다. 이후 step1의 TABLE부터 step2 AST, step3~5 관계, step7 semantic frame,
  step8 aggregate/embedding을 같은 공개 database에 단계별로 바로 upsert한다.
- run 성공은 NDJSON `complete` event와 run directory의 quality summary/log에만 남는다. graph에는
  run ID/epoch, building/completed/failed 상태, expected/actual closure, active completed generation을
  나타내는 canonical manifest가 없다. 최초 `manifest.json`도 started 입력 정체성만 기록하며 성공·중단·
  실패 상태로 갱신되지 않는다.
- 파이프라인 예외는 usage/finding을 요약하고 re-raise하지만 이미 쓴 graph를 rollback/wipe하지 않는다.
  사용자 stop은 실패로도 취급하지 않고 partial graph를 그대로 둔다. REST 검색은 분석 registry와 별개로
  동시 실행을 허용한다.
- `search.corpus.load_corpus`는 EMBED_META fingerprint와 EMBEDDED count만 검사하고 run completion은
  검사하지 않는다. embedding은 전체 workflow의 마지막 단일 단계가 아니라 health metrics와 같은
  post-processing 병렬 group에 있어, embedding 완료 뒤 sibling 실패가 나면 search도 partial graph를
  열 수 있다. embedding 전 실패라도 MCP `node_detail`은 ID만 있으면 completion guard 없이 반환한다.
- 정적 판정 뒤 Neo4j를 다시 인증하고 `SHOW DATABASES`를 재확인했다. 허용 DB 5개는 모두 online이었다.
  current Framework graph에서 property key/label에 `run`, `complete`, `epoch`, `generation`, `manifest`가
  들어간 Analyzer-owned node를 직접 조회한 결과 모두 0개여서 canonical completion/epoch node가 없다는
  배선을 실제 graph에서도 확인했다. 개별 CodeNode의 `analysis_status`는 node concern 상태일 뿐 run
  publish 상태가 아니다.
- 따라서 새 run 시작 직후에는 직전 검증 graph까지 사라지고, 실행 중/중단/실패 graph의 존재하는 subset이
  정상 source of truth처럼 API/MCP에 노출된다. 이는 timeout·fatal·사용자 stop이 누락 범위와 원인을
  canonical graph 상태에 연결하지 않는 실행 원자성 결함이다.
- 해결은 임시 DB 생성이 아니라 같은 허용 DB 안의 run epoch/generation staging, expected/actual closure와
  completed marker를 원자적으로 publish하고 모든 consumer가 completed generation만 읽는 계약이어야 한다.
- current graph는 기존 완료 run의 수량과 맞지만, 제품상 failure injection으로 partial 공개를 재현하는 red
  test는 아직 실행하지 않았다. `wipe_graph` 실행 경계의 유입은 `0f54111`이다.
- 현재 판정: **실제 canonical graph 손실/혼합 및 불충분한 degraded 처리 설계**.

## 4. 최초 후보 C-006~C-015 종결 분류

| ID | 경로 | 확인된 사실 | 최종 분류 |
|---|---|---|---|
| C-006 | `branch_inventory._ast_termination_rows` | exit 보조 표기만 160자; deterministic Rule cond/then은 전체 AST condition/RETURN/assignment/call effect 사용 | **표시/중복 prompt preview**. 해당 effective AST owner의 exact source/profile이 정본이며 parent가 child effect를 재소유하지 않음 |
| C-007 | `llm/embedder._truncate_oversized` | canonical text를 embedding 전에 현재 `max_chars=2000`으로 절단; graph truth는 별도 보존 | **검색 파생 제한**. 검색 recall degraded이며 canonical graph 의미 손실은 아님 |
| C-008 | summary role 최대 5 | 5는 닫힌 target-role taxonomy; actual 417건 중 398건이 5 slot이고 28 leaf가 600 경계 도달 | **taxonomy 자체는 안전**, role 내부 fact obligation/closure 부재와 600자 leaf는 **실제 의미 손실**. F-001 근본 설계로 통합 |
| C-011/C-012 | unknown external scan/miner sampling | C-017로 통합 | closed |
| C-013 | human glossary 상한 | payload/term/alias/string 초과는 whole-input `ValueError`, 절단 없음 | **fail-closed 입력 계약** |
| C-014 | agent sampling/max-calls/max-rounds | residual은 원래 잔여 전건을 보존, call-review 미열거 항목은 유지 | scan/external만 C-017/C-018; 나머지는 **명시 degraded/안전** |
| C-015 | timeout/retry/cache/batch/concurrency | retry/cache/batch/semaphore의 exactness 확인 | concern 단위 finding/exact split/실패 전파는 **안전 또는 명시 degraded**. run 전체 공개 원자성 결함은 C-032 |

## 5. Rule/Example owner 중간 판정

- Framework Rule/Example owner는 routine, DBMS owner는 effective AST CodeNode라는 현재 계약을 유지한다.
- Rule/Example은 LLM 0이며 `rule_prompt_evidence`가 전체 parser condition과 statement effect를
  canonical ref로 물질화한다.
- `termination_rows.text[:160]`는 Rule의 `cond`/`then` producer가 아니다. 따라서 C-006만으로
  Rule/Example 손실이라고 단정한 기존 위험 문구는 정정 대상이다.
- 같은 문자열은 summary branch index에도 들어가지만 effective AST target 자신의 source/profile이 정본이고,
  parent는 child 내부 effect를 재소유하지 않는다. 따라서 이 160자 표기 자체는 Rule/Example/summary 정본
  producer가 아닌 preview로 판정한다. DBMS child prose 치환 문제는 C-005가 별도로 소유한다.

## 6. 안전/preview/search/degraded로 분리된 항목

- **안전한 실행 분할**: DBMS sibling token batch는 oversized singleton도 버리지 않고 모든 node key를
  exact-once 검사한다. Framework deadline/repetition split은 slot ID별 재귀 분할 후 required ID를
  exact merge한다. embedding request split도 입력 순서/결과 수를 검증한다.
- **cache/retry**: YAML response cache key는 exact prompt/model/max-token/structured method/schema를 포함하고
  load 시 schema 재검증한다. transport retry는 usable partial이 없는 transient failure만 같은 요청으로
  최대 3회 재호출한다. Neo4j retry는 transient/service unavailable만 동일 query로 재실행하고 끝내 실패하면
  raise한다.
- **표시 전용 preview**: regex 검증 sample 25/표시 20, match text 90, coverage residue text 80,
  NDJSON pattern 197, health top 20, 오류 목록 앞 N건, artifact filename 80은 canonical 원장/결과와 별도다.
- **검색 파생 제한**: embedding 2,000자, cluster/anchor/support/evidence/judge Top-N, query glossary alias/term
  상한은 graph truth를 바꾸지 않고 검색 recall/선택 목록만 제한한다. 정확도 주장이 아니라 search degraded다.
- **명시적 degraded/fail-closed**: human glossary와 document adapter hard limit는 부분 결과가 아니라 입력/파일
  전체를 거절한다. residual 30개 sample/3 round는 최종 미해결 원래 case를 전부 보존하고 agent 실패 finding을
  남긴다. call-review top 300은 미열거 call을 보존하며 실패 시 전부 보존한다.
- **샘플 evidence**: Catalog `sample_limit=5`는 canonical table identity가 아니라 관측 row snapshot이다.
  이를 table 전체 분포로 일반화하지 않는 prompt/validator 계약은 별도 의미 봉인에서 확인한다.

## 7. timeout/retry/cache/batch/concurrency 현재 값

- active analysis pipeline: configured max concurrency 64와 LLM concurrency 32의 `min`으로 run 공유
  LLM slot 32개를 만들며, DBMS batch 1,500 tokens, source compaction 12,000 tokens,
  residual sample 30/max round 3이다. external document/text file load는 별도 semaphore 8이다.
- `big_context_threshold_tokens=1,000` 설정은 production consumer가 0개라 현재 의미/실행 경로가 아니다.
  `ast_budget_splitter.py`도 tests 외 caller가 0개인 dead helper라 현행 결과 분할로 분류하지 않는다.
- active agent: 공용 agent output 8,000 tokens, Scan max call 60, External Format max call 16,
  context editing 50,000 tokens/summary trigger 75,000 tokens/최근 tool result 3개 유지,
  call-review max call 3/drop ratio 50%.
- YAML wall timeout은 Framework/DBMS 210초. output max-token은 code summary 6,000,
  table insight 5,000/6,000, module pre 500, module summary 2,000, component 200,
  table description 5,000, residual 2,000이다.
- active embedding은 concurrency 8, request char budget 700, timeout 180초, max retry 6,
  per-text max chars 2,000이고 embed step은 enabled다. Neo4j는 최대 5회 exponential retry,
  base backoff 2초다. Catalog HTTP timeout은 120초다.
- response cache는 enabled, LRU sweep 상한 512 MiB이며 run 비용 차단은 USD 20이다.
- document adapter는 64 MiB/file, output 2,000,000 chars, 500 pages, 100 sheets,
  sheet당 10,000 rows, 전체 250,000 cells, subprocess 120초를 넘으면 파일 전체를 failed/unanalyzed로
  격리하고 부분 Markdown을 채택하지 않는다.
- 기존 Framework run 567 LLM calls의 response-cache hit는 0이었다.
- output limit/deadline/repetition이 usable partial을 정상 의미로 채택하지 않는 배선을 확인했다.
  Framework summary는 deadline/repetition 때 role ID로 exact split하고 singleton 실패는 해당 node
  `summary unavailable` finding으로 격리한다. table insight도 unavailable finding, DBMS batch ModelOutputError도
  batch 전 node finding으로 흐른다. transport 외 failure와 key invariant 실패, cost budget 초과는 run을
  중단한다. 이 명시 처리 자체는 안전/degraded지만 이미 공개 graph에 쓴 결과의 원자성은 C-032가 소유한다.
  제품 변경 전 failure-injection red test로 고정한다.

## 8. 검증 상태

### 유입 commit closure

| 항목 | 최초/현재 경계 유입 commit |
|---|---|
| F-001, C-001, C-008 | frame/leaf·projection `cd9d63b`; 현재 target-slot 5 gate `d47f172` |
| C-002 | rejection reason projection `8e1aee3` |
| C-003, C-007 | external evidence/embedding 경계 `30828f7` |
| C-004, C-006 | Catalog discovery/AST branch preview `bc699ee` |
| C-005, C-009a | child-summary compaction 원형 `6956d02`; 현재 grounded packet `d498608` |
| C-009b | 현재 callee interface packet `d498608` (prose handoff는 이전 계열에서 계승) |
| C-010 | module signature 80 `6956d02` |
| C-013 | human glossary fail-closed input `1aef9a3` |
| C-014 | call-review sample `30828f7`; residual loop `bc699ee` |
| C-015 | transport retry `67a5ac8`; response cache `3046cf9`/atomic `2cf662e`/LRU `999fd16`; DBMS batch `d498608`; shared priority slot `d47f172`; current wall timeout `e04d4d5`; Neo4j retry 계열 `30828f7` |
| C-016 | ContextEditing/Summarization stack `30828f7` |
| C-017 | external agent sample `30828f7`; structure candidate cap `5175a3d` |
| C-018 | Scan Agent/tool limits `30828f7` |
| C-019 | view depth `bc699ee` |
| C-020/C-021 | wrapper hop/value length `aa6c9d7` |
| C-022 | model source projection `d498608` |
| C-023 | UTF-8 ignore `30828f7` |
| C-024/C-026 | inheritance depth/unknown AST reporting `bc699ee` |
| C-025/C-029 | AST-only 모집단 원형 `1d0a504` 계열; source↔AST↔diagnostic closure는 현재도 부재 |
| C-027 | source-read warning `c557efc`, code-text audit `79831ff`, prompt status `d498608` |
| C-028 | 현재 column tokenizer fallback `d498608` |
| C-030 | current SCC suppression/packet `d498608` |
| C-031 | Catalog lineage/fallback `30828f7` |
| C-032 | run-start public graph wipe `0f54111` |

- 검증됨: 기준선/DB 상태, F-001 actual schema·경계 수량, 위 정적 producer/caller 배선.
- 검증됨: 각 F/C 경계의 유입 commit 축. "부재" 결함은 해당 owner가 생긴 최초 계열과 현재까지
  closure가 추가되지 않았음을 함께 기록했다.
- 미검증: DBMS actual run, sealed 직접 의미 판정, 새 계약/red test.
- 아직 제품 정확도나 완료를 주장하지 않는다.

### 2026-08-20 계약 고정 및 첫 red test

- `semantic-frame-v1`에 canonical `meaning`/`missing_context`/`Question` 무상한 계약,
  evidence-anchor obligation 분할, 자연어 중간 요약 권위 금지, completed generation publish 계약을 고정했다.
- 제품 변경 전에 다음 두 red test를 실행했고 `2 failed`를 재현했다.
  - `test_semantic_leaf_has_no_arbitrary_character_or_diagnostic_cap`: provider schema에
    `meaning.maxLength=600`과 `missing_context.maxItems=8`이 남아 실패.
  - `test_projection_and_rejection_preserve_every_distinct_diagnostic`: 24개 distinct obligation이
    projection 전에 wire validator의 `maxItems=8`에서 거부되어 실패.
- 이 기록 시점에는 아직 제품 수정 후 회귀나 실제 graph 재실행을 검증하지 않았다.
- 첫 책임 슬라이스 구현 후 같은 두 테스트는 `2 passed`로 전환됐다. semantic frame leaf,
  legacy summary/batch/table projection model, provider-visible summary schema에서 임의 600/8/15 상한을
  제거했고 whitespace/dedup 정상화와 sufficient의 empty-missing 불변식만 유지했다.
- 관련 step7/schema/API 집중 회귀는 `231 passed, 134 subtests passed`, `git diff --check`는 오류 0이다.
  이 결과는 해당 leaf/diagnostic 보존 슬라이스만 검증하며 전체 의미 정확도나 graph 실행 합격 근거가 아니다.

### 2026-08-20 FUNCTION obligation 및 Framework exact closure

- 제품 변경 전 red test 4축을 고정했다.
  - 500 assignment와 70 call fact가 profile에는 있으나 target-only `PROCESSING_EFFECTS`의
    `evidence_refs`에는 하나도 귀속되지 않는 실패.
  - summary slot 30개가 `_MAX_TARGET_SUMMARY_SLOTS=5` gate에서 거부되는 실패.
  - 같은 role fragment 두 개가 canonical source 11→12가 아니라 slot hash 12→11 순서로 투영되는 실패.
- 공통 planner는 parameter, 의미가 필요한 RULE, source CALL, DATA_OBJECT, 효과 STATEMENT, RETURN을
  canonical evidence anchor별 FUNCTION obligation slot에 정확히 한 번 귀속한다. Rule/Example 자체는
  계속 결정론 LLM 0이며 FUNCTION guard leaf만 RULE exact fact의 의미 projection을 소유한다.
- Framework는 전체 obligation을 먼저 확정한 뒤 assigned profile/source packet의 실제 추정 token을
  기존 실행 분할 예산 1,500에 맞춰 분할한다. 총 slot 수/문자 수는 제한하지 않는다. 각 packet은
  assigned refs만 가진 profile과 좌표 source line, 분할 전 target UTF-8 SHA-256을 포함한 reversible
  `source-closure/v1` JSON이다. quote/backslash literal 왕복과 refs/line exact set을 테스트했다.
- deadline/repetition뿐 아니라 length 실패도 multi-slot이면 canonical ID 반분 exact merge하며 singleton
  실패는 기존처럼 unavailable이다. projection은 role 다음 source 좌표 순이며 좌표 없는 interface만
  stable slot ID로 tie-break한다.
- Framework summary 권위 packet의 `module_intent`, callee summary, codebase prose는 제거하고 assigned
  CALL fact만 전달한다. 다만 DBMS는 같은 planner 결과를 받지만 기존 batch prompt/source compaction을
  아직 사용하므로 C-005/C-009의 DBMS closure는 다음 슬라이스이며 완료로 판정하지 않는다.
- step7 전체 회귀는 `212 passed, 114 subtests passed`, pipeline 전체+MCP 집중 회귀는
  `793 passed, 1 skipped, 286 subtests passed`; graph/DB 변경은 0이다.
