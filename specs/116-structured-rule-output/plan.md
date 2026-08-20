# plan 116 — Rule·Example 구조화 출력 v2 구현 계획

> **현행 보정(2026-08-05):** 아래 §1~§18 전체는 구현 과정의 기준선·실험·당시 실행 계획을
> 보존한 역사 기록이다. 이후 실측에서 값·객체 추적, arbitrary Python leaf의 결정론 의미 비교,
> 내용 재요청, 후보 한 건의 전체 호출 실패가 원칙/정확도/성능에 맞지 않아 폐기됐다.
> 현재 재개 순서는 `specs/131-cross-node-semantic-grounding/history/handovers/06-재개절차와-현재상태.md`,
> 책임 경계는 `02-현재설계와-책임경계.md`, 폐기 목록은 `05-spec116-정정표.md`가 명시한다.
> 이 문서의 과거 체크와 명령형 문장을 현재 구현 지시로 되살리지 않는다. 현재 계획은 바로
> 아래 §0만 권위로 사용한다.

spec: `specs/116-structured-rule-output/spec.md`

operational handoff: `specs/131-cross-node-semantic-grounding/history/handovers/README.md`

## 0. 현재 실행 계획 — 2026-08-05

1. cache OFF·concurrency64·Neo4j `test` 조건을 고정한다. 18분대는 현재 허용선이고 11~15분은
   정확도 비협상 추가 개선 목표다.
2. 현재 지배 병목은 Rule output 생성이다. compact regex의 `extra` 1→0 차이를 source/DDL/evidence와
   grader로 직접 판정하기 전에는 production에 넣지 않는다.
3. Rule 의미 선택은 LLM이 branch별 cond/then/extra/nl 구조로 수행한다. 결정론 코드는 좌표·소속·
   제한 문법·격리·graph/Example 파생만 맡고, 값/객체 추적·상태값 의미 추론·언어별 의미 변환을 하지 않는다.
4. 모델 후보 오류는 좌표별 quality finding으로 격리한다. transport/운영/결정론 불변식 실패만 fatal이다.
5. 제한 replay와 직접 의미 대조를 통과한 범용 compact wire만 구현한 뒤 전체 unittest와 prompt hash를
   실행하고 공식 cold를 한 번 측정한다.
6. 승인 조건은 wall 18분대 이하, trusted >=4,016, obligation4,100, unavailable0이다.
7. 승인 뒤 CALL/RW/RETURN/Rule-branch/Example 전수 검증, 코드노드20개+모든 테이블 직접 의미 검증,
   Stage8 delta를 수행한다.
8. 성능 후보를 더 적용하기 전에 cross-service 구조 결함 TA-151을 처리한다. ANTLR grammar가 아는
   RETURN/CALL/ASSIGNMENT/종료/조건 구문은 `antlr-code-parser` listener/AST JSON이 생산하고,
   analyzer는 AST-only로 소비한다. 실제 지원 언어의 source↔AST 완전성 0-delta 뒤 legacy scanner를
   삭제하며 parser/analyzer fallback을 병존시키지 않는다.
9. 반복 종료선은 handoff `01` §6을 따른다. 같은 원인의 prompt 변형, 변경 없는 cold, 고정 row 수
   미세조정을 무한 반복하지 않는다.

## 1. 현재 흐름 → 목표 흐름 (실코드 확인 2026-08-03)

### 현재 (관련 배선만)

- 분기표: `pipeline/quality_gates/branch_inventory.py` `build_inventory()` →
  `BranchInventory.branch_index()`(문자열). 주입: `pipeline/steps/step7_analyze_code/control_prompt.py`
  `control_index()` → `three_split_llm_fanout.py` `_build_user_values()["branch_index"]`.
  complement 행은 "직접 대응 decision @N의 여집합; 외곽 decision @M **조건도 상속**" —
  else-if 체인에서 형제 선행 조건의 **부정**이 아니라 "상속"으로 반대 표기(1단계 대상).
  종료문(return/PFM_CATCH) 행 없음. JSON 실체화 없음(프롬프트 문자열로만 존재).
- 출력 모델: `pipeline/models/code_analysis_output.py` `RulesExamplesOutput`
  (rules max 48 / examples max 8 / questions max 15). `RuleItem.statement`=자연어,
  `_require_closed_semantic_text`가 statement·given/when/then 괄호짝 검사(원문 리터럴
  인용 오탐 64건 — 0단계 대상).
- 호출: `llm/runners/yaml_llm_runner.py` `run_yaml_rule()` —
  `with_structured_output(response_model, include_raw=True)`. **실경로(2026-08-03
  MockTransport 주입 확정)**: 실전송은 `response_format json_schema strict`이고, 검증
  실패·절단 응답은 fallback이 아니라 **ainvoke가 ValidationError를 raise** →
  `_write_error_record`가 response=None을 기록하던 것이 raw 소실의 실경로(핸드오프
  주장이 옳았음 — rerun6 증거 일치). 0단계 수리: ValidationError `input` 원문 복원
  (`_extract_partial_content`) + RepetitionLoopAborted partial + parsing_error 표면화.
- 프롬프트: `llm/prompts/framework/code_rules_examples.yaml`(extends `_common.yaml`) +
  `llm/prompts/common/rule_evidence_contract.md` — 자연어 지시 약 6.8천자.
  해시 감시: `llm/prompts/prompt_hashes.json` + `scripts/check_byte_identical.py` + CI.
- 게이트: `pipeline/quality_gates/grounding.py` `check_grounding()` — **측정 전용**
  (`_measure_grounding`, 산출물 불변). 자연어 statement에서 코드값을 역추출해 분기와 대조.
- 재시도: transport만 제한 재시도(`llm/runners/transport_retry.py`), concern 동일 요청 2회
  (`pipeline/steps/step7_analyze_code/concern_retry.py`).
- enrichment·저장: `rw_table_filter.py`(affected_tables 결정론) →
  `example_mapping_builder.py`(Rule/Example/Question 노드 생성) → graph write.

### 목표

spec §3 배선. 핵심 이동:

1. 분기표가 **정답지**로 승격(종료문 행·부정 보존·case 정규화·run별 JSON 실체화).
2. 출력 계약이 자연어 statement → **JSON 봉투 + 파이썬 leaf**(cond/then 문자열, nl 표시 전용).
3. 채점이 측정 전용 grounding → **행 단위 결정론 채점기**(echo·뺄셈·ast·모순·창작·어휘)
   + 행 단위 재요청(사유 구체 명시, 상한 3).
4. 대형 함수는 **출력 배칭**(입력 통째 + 할당 행 + echo).
5. Neo4j Rule/Example 스키마 통째 교체.

## 2. 새 모듈 소유권 (헌법 VIII — Constitution Check '주의' 해소)

| 모듈 | 위치 | 이유 |
|---|---|---|
| 파이썬 leaf 어휘·F() 규약·effect 함수 목록의 단일 정의 | `pipeline/models/rule_expression.py` (신규) | 헌법 III: OPS/action의 정의처 한 곳. Pydantic 스키마(`code_analysis_output.py`)와 프롬프트 생성·채점기가 모두 여기서 import |
| 채점기(ast 정규화·행 대조·모순·창작) | `pipeline/quality_gates/rule_grading.py` (신규) | 채점은 생산 공정의 일부(행 재요청이 그 자리에서 결과를 소비)라 pipeline 소유. 화면·API 는 채점 **결과 데이터**(`fn.extra["rule_grading"]`)를 소비하지 이 코드를 import 하지 않는다. **승격 기준**: "재분석 없이 저장된 규칙을 독립 재채점"하는 기능이 필요해지는 시점에 pipeline 밖 소유로 이동 검토 — 그 전 선제 이동은 추측 설계라 금지 |
| 값·객체 추적기 | `pipeline/quality_gates/value_tracking.py` (신규) | 결정론 사실 생성 — branch_inventory와 형제 |
| 행 배칭·재요청 오케스트레이션 | `pipeline/steps/step7_analyze_code/` 내부 (concern_retry 형제) | 호출 흐름 소유자는 step7. quality_gates를 import하는 방향(역방향 금지) |
| 분기표 JSON 실체화 | `branch_inventory.py` 확장 + run_dir 기록은 step7 호출부 | 생성은 inventory, 기록 위치는 실행 소유자 |

의존 방향: `models` ← `quality_gates` ← `steps/step7`. 역방향 import 금지.

## 3. 계약 양끝 (생산자/소비자)

| 계약 | 생산자 | 소비자 | 이번 작업에서 |
|---|---|---|---|
| 분기표 JSON | branch_inventory | 프롬프트(user), 채점기, run 산출물 | 1단계 신설 — 스키마를 spec §5로 고정 |
| rules 출력 봉투 | LLM(스키마 강제) | 채점기 → enrichment → example_mapping_builder | 3단계 교체. 자연어 statement 소비자는 저장소 전체 grep으로 전수 수정 |
| Rule/Example Neo4j 스키마 | example_mapping_builder | (후일) Architect/frontend — **호환 제약 없음**(사용자 확정) | 6단계 통째 교체. `graph/schema/rel_types.py` 관계명 확인 |
| 프롬프트 해시 | prompt_hashes.json | CI byte_identical | 3단계에서 갱신(사유 기록) |
| catalog HTTP 계약 | — | — | 접촉 없음(헌법 Cross-Service: 내부 변경) |

## 4. Constitution Check (spec §12 표 — plan 시점 재확인)

위배 0. '주의' 4건의 plan 답:
- **II 분기 0**: 새 스키마·프롬프트는 framework/dbms 각 strategy yaml과 모델 클래스 교체로만.
  step 코드에 `if strategy` 추가 금지 — 기존 fanout의 Strategy 선언 경로 유지.
- **III 단일 진실**: `rule_expression.py`가 어휘 정의처. 프롬프트 본문의 어휘 절은 코드
  생성이 아니라 검증으로 고정(테스트가 yaml 어휘 절 == 모듈 정의 대조 — yaml은 사람이 읽는
  정본 유지, drift는 테스트가 차단).
- **V KV캐시**: 새 system은 framework `_common.yaml` extends 구조 유지, 배칭 할당은 user
  꼬리에만. 해시 갱신 1회 + `check_byte_identical` 통과 증거.
- **VII 호출방식**: 헌법 1.3.1에 따라 `run_yaml_rule`의
  `with_structured_output(include_raw=True)` 경로는 유지하되, 배포 config 기본과 출력계약 YAML
  override가 채널을 선언한다. 2026-08-04 fresh exact replay에서 framework summary의
  json_schema+xgrammar quote/공백 교착이 재현되고 strict function call은 통과했다. 반대로 24행
  RULE function schema는 서버 grammar가 거절했다. 따라서 배포 기본은 json_schema,
  framework `code_summary`만 검증된 function_calling override다. canonical schema는 deep copy,
  resolved channel은 cache key, raw/Pydantic/fail-closed는 공통 계약이다.

## 5. 마이그레이션·롤백

- 기존 그래프 데이터 마이그레이션 없음 — 매 run이 전체 재구축(헌법 Cost&Safety)이고
  스키마는 통째 교체 확정. 6단계 완료 전까지 재분석을 실행하지 않으므로 중간 상태 없음.
- 롤백 = git revert 단위. 각 단계가 독립 커밋(사용자 커밋 지시 시)이 되도록 슬라이스 유지.
- dirty 132는 선행 WIP — 이번 작업 파일과 겹치는지 단계마다 `git status`로 확인, stage 금지.

## 6. 검증 방법 (단계 공통)

1. 단위: 해당 모듈 unittest (`.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -t .`
   — `-t .` 필수, 기준선 147/147).
2. 반증: spec §14 오류주입 케이스.
3. 회귀: 전체 unittest + `check_byte_identical`(프롬프트 접촉 단계).
4. 실측(3·4단계): harness py1 재현(신 계약 20/20·ast_fail 0) + b200급 배칭 1회.
   소량 API 호출 허용, 대량이면 사용자 통지. **전체 재분석은 승인 전 금지.**
5. 종료 감사: diff 재독, spec §12 표 갱신, 원칙·헌법 위배 점검, 범위 밖 발견은 tasks 편입.

## 7. 역사 기록 — 당시 현행 실행 계획과 책임 경계 (2026-08-04)

현재 흐름은 `분기 inventory → required_rule_rows → 호출별 동적 required-key schema →
strict structured output 1회 → canonical 모델 정규화 → AST/어휘/행 채점 1회 → 결정론
EXAMPLE/그래프 파생`이다.

- 내용 누락·schema·채점 실패는 다시 묻지 않고 좌표와 원인을 남겨 호출 전체를 실패시킨다.
  transport 오류의 제한 재시도만 공통 runner가 소유한다.
- 값·객체 상태 추적은 생산자와 소비자 모두 제거한다. LLM에는 AST/ontology/DDL/R/W/callee
  반환 표현식처럼 정적으로 입증된 구조 사실만 준다.
- `rule_response_schema.py`가 호출별 framework/DBMS 고정키 모델을 만들고 canonical 모델로
  정규화한다. `rule_row_batching.required_rule_rows`가 할당 의무의 단일 진실이며,
  `rule_grading`은 schema 우회 빈 결과까지 항상 뺄셈한다.
- response cache는 실제 canonical JSON schema와 resolved structured-output channel을 키에
  포함한다. 같은 클래스 이름이나 같은 프롬프트라도 할당 좌표 또는 전송 채널이 다르면 다른
  요청이다.
- 다음 실행 슬라이스는 전체 회귀와 hash → 응답 캐시 false·동시성 64의 `test` DB 콜드 run
  → 콜/단계/Neo4j 계측 → CALL/R/W/RULE/EXAMPLE 전수 대조 → 모든 LLM 속성 기계 검사 →
  코드 20개와 모든 테이블 직접 의미 판정 순서다.
- 직전 콜드는 residual raw pattern이 SKILL의 자연어 비-TABLE 계약을 위반해 production
  검증에서 조기 중단됐다. 다음 실행 전에 spec §42~§44를 우선한다: 비-TABLE 형식을 SKILL
  소유의 폐쇄 DSL로 만들고 linker/residual의 공통 금지 영역으로 전달하며, hydrate된
  handler/API 필드 소실과 framework drop 사후 정화를 함께 제거한다. 각 슬라이스는 red
  반증 → 생산자/소비자 구현 → 표적/전체 회귀 뒤에만 다음 콜드로 넘어간다.
- TA-42~TA-45 후 scan Agent가 `"[Collapse]"` 내용으로 submit 없이 종료한 새 실패는
  TA-46으로 처리한다. registered SKILL은 그 자체의 구조화 access/exclusion/DDL 계약을
  production verifier로 판정해 정확히 한 후보면 LLM 없이 선택한다. unknown만 custom LLM
  탐색을 쓰고 다중 후보는 fail-closed한다. 같은 verdict를 finalize에 전달해 중복 스캔하지
  않는다.
- TA-46 콜드에서 matched SKILL의 비-TABLE exclusion을 residual이 다시 TABLE pattern으로
  제안한 실패는 TA-47로 처리한다. known SKILL의 access/exclusion/DDL 계약은 runtime LLM이
  확장하지 못하게 닫고 기존 완전 not-found를 unresolved로 보존한다. unknown/custom만
  residual 제안과 production 검증을 사용한다. 이 경계 구현과 전체 회귀는 완료했으며,
  실제 corpus의 0콜·잔여 무손실은 다음 콜드 run에서 검증한다.

24행 동적 schema의 직렬화 크기 25,414 bytes는 측정 사실이지만, 실제 토큰·서버 문법 컴파일·
벽시계 영향은 아직 모른다. 콜드 run의 사용량과 지연으로 판정하고 정확도를 낮추는 압축은
채택하지 않는다.

## 8. 최신 실패 이후 실행 슬라이스 (2026-08-04)

1. TA-49를 먼저 구현한다. source/linked-external 근거에서 언어중립 domain-term candidate
   inventory를 한 번 만들고, prompt와 동적 summary schema가 같은 inventory를 소비한다.
   framework는 한 target의 optional fixed-key map, DBMS는 required node-key map 안의 노드별
   optional fixed-key map으로 강제한다. normalization 뒤 기존 fail-closed validator까지 같은
   candidate 좌표를 대조한다.
2. TA-49의 red 반증은 최신 cold 실패와 동일하게 실제 call identifier가 free term으로
   들어오는 현재 모델을 먼저 실패시킨다. source 정상 후보·external-only 후보·빈 후보와
   node key 누락/과잉을 함께 잠근 뒤 구현한다. 특정 실 corpus 이름을 fixture나 prompt에
   넣지 않는다.
3. TA-49 표적/전체 회귀와 prompt hash를 통과한 뒤 TA-48의 source preparation 단일화를
   구현한다. candidate verifier 전체 판정과 ambiguity fatal은 유지하고 immutable read/lexed
   views만 공유한다.
4. 응답 캐시 false·동시성 64·신선 parser corpus·`test` DB로 전체 cold를 다시 실행한다.
   성공하기 전 조기 실패 wall time은 목표 달성 자료로 쓰지 않는다. 단계/agent별 tokens,
   latency 분포, in-flight, scan, Neo4j query/index를 함께 계측한다.
5. 성공 graph에 대해 결정론 전수 정답지와 모든 LLM 속성 기계 검증, 고정 20 code node와
   모든 table의 직접 의미 판정을 수행한다. 새 결함은 다음 TA로 즉시 편입하고 같은 루프를
   반복한다.

## 9. TA-49/TA-50 응답 경계 정정 (2026-08-04)

- TA-49 후보별 optional field 방식은 실제 대상 schema 15.4KB로 비대해 폐기했다. 좌표 typed
  map은 이후 strict function tool 변환이 동적 key를 전부 금지하는 결함이 확인돼 TA-62에서
  `[{key, meaning}]` 최대 10개 평탄 배열로 교체했다. human evidence token index는 summary
  입력에 한 번만 주고 key enum에는 짧은 span 좌표만 둔다. exact term/evidence는 코드가
  immutable inventory에서 파생한다.
- 실제 서버는 compact map을 문법 강제했지만 streaming SDK chunk의 `parsed` Pydantic 타입
  경고가 재현됐다. TA-50에서 provider 경계는 canonical JSON Schema dict, runner 내부는 원래
  Pydantic model validation으로 분리한다. 반복 방어·usage·raw evidence·cache fingerprint는
  그대로 유지하고 parse/validation 실패를 한 번의 fail-closed 결과로 기록한다.
- TA-50 표적/실서버 warning 0을 먼저 통과한 뒤 TA-49 전체 회귀·hash를 닫고 TA-48로 이동한다.
- TA-49 후보 재감사에서 활성 코드 atom 전체 허용 모순이 드러났다. TA-51로 ontology identifier
  문법과 문자열 마스킹에서 구조 식별자를 exact 제외하고, 사람용 주석·문자열·외부 span만
  selection 후보로 남긴 뒤 TA-49 전체 회귀를 닫는다.
- TA-51 후 28건 실측에서 n-gram term 문자열 schema가 p95 26.2KB까지 커졌다. TA-53은 span을
  절단하지 않고 evidence token 표 1회 + 짧은 좌표 key enum으로 표현해 schema 중복만 제거한다.
  summary 외 concern에는 이 입력을 보내지 않는다.
- TA-49~TA-53 구현 뒤 전체 792 tests, prompt hash 31과 실제 OpenAI-compatible 좌표 schema
  warning 0을 통과했다. 28건 합산 schema+evidence는 median 3.1KB/p95 9.3KB/max 12.1KB다.
  cold token/latency와 직접 의미 정확도는 아직 미검증이므로 TA-48 뒤 실제 run에서 판정한다.
- TA-48은 source read/live lexing/C view만 불변 준비 객체로 프로젝트당 한 번 만들고 모든
  registered candidate에 공유한다. root/file scope가 다르면 거절하며 후보별 의미 판정은
  그대로다. TA-54의 silent lexer fallback도 제거했다. 전체 793 tests와 정적 검증 후 cold로 간다.

## 10. 구조화 채널 증거와 재생 경계 (2026-08-04)

- runner가 config와 rule에서 resolve한 실제 채널을 schema 구조화 호출의 성공·cache·실패
  record metadata에 기록한다. 평문 호출은 `null`로 구분한다.
- exact replay는 기록 schema의 fingerprint와 기록 채널을 함께 요구한다. 과거 evidence에 채널이
  없으면 현재 config를 역사처럼 대입하지 않고, 운영자가 근거를 갖고 `--method`를 명시해야 한다.
- 채널은 이미 response-cache key에 포함되므로 evidence와 cache가 같은 책임 경계를 공유한다.
- 표적 58/48 tests, 전체 808 tests, compileall, prompt hash 31, diff-check를 통과했다. 실제 cold
  record의 rule별 채널과 안정성은 다음 실행에서 검증한다.

## 11. SGLang function-tool local-ref 경계 (2026-08-04)

- canonical Pydantic schema와 provider 전송 schema를 구분한다. 전자는 증거·사후검증 소유자이고,
  후자는 function-tool grammar 호환을 위한 의미 동일 wire 표현이다.
- 공용 structured-output binder가 deep copy 뒤 local RFC 6901 ref를 inline한다. 호출부나 특정
  domain model에 예외를 넣지 않는다.
- 누락·외부·순환 ref와 sibling keyword는 fail-closed한다. canonical schema를 변경하거나 제약을
  완화하지 않는다.
- 동일 cold 실패 record의 원형 거절/inline 성공 역실험과 exact replay를 통과했다. 전체 회귀와
  동적 후보 전건 cold 안정성은 다음 게이트다.

## 12. 큰 문자열 enum 제거와 모델 한계 종료선 (2026-08-04)

- framework/DBMS 공용 domain-term inventory는 모든 후보에 source 순서의 연속 정수 ID를 붙인다.
  prompt에는 evidence와 `[id, 좌표]` 전량을 주고 schema는 `candidate_id` 정수 범위만 강제한다.
- normalizer가 ID를 immutable candidate의 key/term/evidence로 복원한다. 후보 절단, invalid drop,
  이름 패턴 예외, 내용 재시도는 금지한다.
- 동일 실패 record의 단 한 번 역실험이 기존 canonical 의미로 round-trip했다. 구현 표적 회귀 뒤
  전체 unittest/compile/hash/diff를 통과하고 concurrency 64 cold로 실제 안정성을 판정한다.
- 동시 cold에서도 구조 강제가 실패하면 더 이상 prompt/schema 변형을 반복하지 않는다. 로그와
  실패 record로 모델/서빙 한계를 명시하고 사람 제공 glossary 계약 또는 자동 추출 명시 종료를
  선택한다. 의미 정확도를 낮추는 임의 후보 상한은 채택하지 않는다.

## 13. 세 번째 cold 실패 뒤 원칙 수리 (2026-08-04)

- 일반 external text file category는 ontology registry가 소유하고 binary adapter capability와 분리한다.
- code/module semantic schema는 모든 출력 필드를 required+extra-forbid로 만들고 strategy별 stereotype
  enum을 ontology에서 파생한다. 누락/unknown의 빈값 coerce는 제거한다.
- concurrency 64 stream idle만 기존 transport retry 대상으로 추가하고 callback tail을 실패 evidence에
  남긴다. 내용·schema·반복 오류의 무재시도는 유지한다.
- 전체 821 tests, compileall, prompt hash 31, diff-check 뒤 cache false/concurrency 64 cold를 다시
  실행한다. 성공 run 전에는 벽시계 목표나 정확도 완료를 주장하지 않는다.

## 14. 네 번째 cold의 자동 용어 종료선 (2026-08-04)

- 정수 ID의 동시 cold에서도 동일 ID 중복이 나와 Pydantic 경계가 fail-closed했다. 내용 재시도와
  추가 prompt/schema 시행착오는 중단한다.
- 임의 상한 축소·중복 drop/merge는 하지 않는다. 사람 제공 glossary를 권위 입력으로 받는 일반
  계약을 현행 config/CLI/graph 소비자에 맞게 설계하고, 없으면 domain term 자동 생성만 명시적으로
  비활성화한다. 나머지 summary 필드는 그대로 엄격하게 분석한다.
- 먼저 TA-71의 전체 response schema required/extra 경계를 수리하고 회귀한다. 이어 TA-72를
  구현·검증한 뒤에만 다음 cache-false/concurrency-64 cold를 실행한다.

## 15. 자동 용어 제거와 사람 glossary 경계 (2026-08-04)

- 입력은 기존 코퍼스 구조 안의 `analysis/glossary.yaml` 한 곳으로 고정한다. CLI/API 모두
  `Codebase.corpus_root`를 전달하고 executor가 graph mutation 전에 한 번만 strict load한다.
- summary schema는 모든 의미 필드를 계속 required로 유지하며 `domain_terms`만 required empty array로
  강제한다. 후보 생성·후보 prompt payload·정규화·전역 식별자 필터를 삭제해 토큰과 책임을 함께 줄인다.
- step8 glossary는 handoff와 LLM을 기다리지 않고 검증된 사람 입력을 기존 검색 graph shape로 바로 쓴다.
  자동 synthesis prompt/model/fanout과 per-node graph field/codec은 삭제한다.
- manifest에는 glossary 경로(코퍼스 상대), supplied, schema version, SHA-256, 항목 수만 기록한다.
  의미검증 exporter는 per-function 자동 용어를 제거하고 전역 human glossary를 별도 packet으로 내보낸다.
- 단위·전체 회귀 뒤 cache false/concurrency 64 cold를 실행한다. 성공 전에는 latency·graph 정확도·검색
  소비 동작을 완료로 판정하지 않는다.

## 16. 역사 기록 — 2026-08-04 재개 구현 계획

1. `llm.runners`가 raw를 받은 뒤의 schema/parse/repetition/length 실패를 공용 typed
   model-output finding으로 분류한다. transport/budget/metering/evidence 실패는 기존 예외로 둔다.
2. step7 framework/DBMS의 각 concern은 model-output finding을 좌표에 귀속해 보존하고 독립 concern을
   계속한다. 정상 sibling만 trusted 결과로 넘긴다. downstream Example Mapping은 accepted rules만
   소비한다.
3. `rule_grading`은 ontology 기반 source-condition compiler가 만든 canonical Python AST와 후보 AST를
   대조한다. compiler가 지원하지 않는 shape는 false 판정 대신 unverified를 반환한다.
4. run usage/manifest 또는 별도 summary artifact가 accepted/rejected/unverified/unavailable count와
   좌표를 집계한다. completed와 completed-with-findings를 사람이 구분할 수 있어야 한다.
5. 오류주입 테스트는 sibling 보존, batch별 계속, no content retry, fatal transport, example 격리,
   framework/DBMS 대칭, run 집계를 먼저 고정한다. 이후 전체 회귀/hash를 거쳐 cold를 실행한다.

Constitution Check: IV 1.3.2의 no-silent-failure는 finding을 숨기지 않는 것과 whole-run abort를
구분한다. I는 결정론 의미 비교와 Example 격리, III는 ontology 단일 진실, V는 prompt bytes 불변,
VI/VII는 구조화 채널과 no-content-retry를 유지한다. 서버/모델/timeout 설정은 바꾸지 않는다.

## 17. 역사 기록 — 13행 batch 반증 뒤 잔여 성능 계획

1. 최신 두 cold의 usage/finding을 같은 분모로 비교한다. 13행 batch는 unavailable 감소 계약으로
   유지하되 wall 개선 수단에서는 제외한다. API cached token 0과 총 input token을 서버 KV hit/miss로
   해석하지 않고, 저장 prompt byte와 서버 로그 reuse를 별도 관측으로 둔다.
2. 이미 실행한 자유서술 단일화 실험을 원본 응답으로 판정한다. 간단/상세 단일 문자열과 자유
   5-slot이 거절 경로·상태→callee 매핑을 생략하거나 한 글자 채움/1,500-token 소진을 만들었으므로
   이 경로와 summary 뒤 원시 deterministic fact 목록을 붙이는 경로를 폐기한다.
3. branch inventory, 직접 CALL, READ/WRITE, source return에서 언어 비종속 좌표 목록을 만든다.
   기존 RULE fixed-key `nl`은 R666에서 인접 분기 조건을 끌어온 실오류가 확인됐으므로 권위 구조로
   쓰지 않는다. 재사용하더라도 label 후보일 뿐이며 cond/effect 골격은 결정론 코드가 소유한다.
4. 좌표를 property key로 고정한 동적 closed schema를 제한 실험한다. 모델은 exact condition/
   effect 문장을 다시 만들지 않고 `purpose`와 권위 식별자·호출·데이터 효과의 사람용 label만
   채운다. 공용 renderer가 결정론 부정/연산자/값→결과 매핑을 보존해 목적·분기/상태·호출 역할·
   데이터 효과·반환 결과 순서의 상세 summary를 만들고 그 동일 의미를 embedding에 제공한다.
   추가 LLM 호출, 누락 폴백, runtime 값/객체 추적, 이름/언어 특화는 넣지 않는다.
5. 대표 20노드에서 required 좌표 누락 0, 원본 SOURCE/branch table과 직접 의미 동일, 검색 식별자·
   상태코드 보존, output token/latency를 비교한다. 기존 독립 summary/search보다 하나라도 나쁘면
   제품 코드에 넣지 않는다. 통과한 뒤에만 framework/DBMS, graph/search/callee, evidence, prompt
   hash와 전체 회귀를 갱신하고 같은 cache-off/concurrency64/test cold로 11~15분을 판정한다.
6. 4노드 required-label probe는 키 누락과 폭주를 없앴지만 범용 지침에서 리터럴 의미 추측을
   반복해 좌표별 업무 label을 trusted 사실로 쓰는 가설을 반증했다. parser가 제공하지 않는
   `subject/operator/literal` 의미를 C/SQL/Python 문자열에서 제품 코드가 다시 파싱하지 않는다.
   다음 구현은 모델의 의미 헤더 1콜과 branch inventory 전체 행의 exact renderer를 결합한다.
   `cond/then` 및 EXAMPLE은 계속 정본에서 파생하고 RULE별 LLM NL 호출은 제거한다. 사람용 설명은
   목적 뒤에 모든 정확 좌표를 구조화해 보여주며, 검색도 같은 구조의 식별자·값·관계를 사용한다.
7. 의미 헤더도 `purpose`, 닫힌 ontology `stereotype`, `questions`로 제한한다. 함수 logical_name과
   상태코드 해석은 모델 출력에서 제거한다. 테이블·컬럼·변수의 표시 의미는 DDL/source 선언/사람
   glossary의 명시 근거가 있을 때만 병기하며, 없으면 exact 식별자를 유지한다. 코드값은 항상 원문을
   보존하고 명시 mapping이 없으면 `정상/실패/유선/무선` 같은 뜻을 자동 부여하지 않는다.

## 18. 역사 기록 — 당시 최종 실행 순서 (2026-08-04)

이 절은 §17의 `결정론 cond/then 소유`, `RULE별 LLM 호출 제거`, `3-field 의미 wire`를 폐기한다.

1. 현재 부분 이행을 한 계약으로 정상화해 producer/schema/prompt/normalizer/consumer/graph/search/
   callee/test가 같은 wire를 쓰게 한다. 이 전에는 full cold를 실행하지 않는다.
2. 최신 성공 cold의 실제 저장 자료를 concern·target·행 수·prompt bytes·input/output token·latency로
   분해한다. 60/120/180초 tail, 반복 출력, 동일 SOURCE 중복, batch, semaphore 점유, Neo4j query를
   집계해 벽시계 지배 원인 순위를 확정한다.
3. 가장 큰 지배항부터 제거한다. 우선 후보는 동일 SOURCE를 받는 summary/rules/insight의 평탄 fixed-key
   통합, 응답 evidence 복창 제거, canonical summary 단일 재사용이다. semaphore는 64를 유지하고
   Neo4j는 query timing이 병목을 증명할 때만 수정한다.
4. RULE은 LLM이 branch별 `cond/then/nl`을 채우고 EXAMPLE은 accepted Rule에서 파생한다. 결정론
   raw-source materializer의 최종 Rule 생산과 값/객체 추적을 제거한다.
5. 대표 20노드에서 주 에이전트가 동일 입력으로 모든 Rule/Example과 table/callee/output 의미를 직접
   대조한다. 정보 부족이면 범용 evidence를 보충하고 GPU만 실패하면 prompt/schema를 한 번만 보정한다.
6. 표적·전체 회귀 뒤 cache=false/concurrency64/Neo4j `test` cold를 실행한다. 11~15분 미달이면 새
   log의 다음 지배항만 수정해 반복한다. 정확도 하락·언어 특화·조용한 실패는 개선으로 인정하지 않는다.
7. 목표 시간 또는 실측 모델/서빙 한계 뒤 CALL/RW/RETURN, RULE↔branch, EXAMPLE↔RULE 전수 대조와
   코드노드 20개+모든 테이블의 모든 LLM 속성/비AST 관계를 직접 검증하고 8단계 delta를 보고한다.
