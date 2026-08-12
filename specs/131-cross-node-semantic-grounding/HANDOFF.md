# spec 131 — Analyzer 의미 정확도 현재 작업 정본

> 누적 일지가 아니라 새 세션이 현재 사실·미해결·다음 행동을 복구하는 단일 정본이다.
> 과거 수치와 이력은 canonical 11/10/09/08 및 `evidence/`가 소유한다. 현재 사용자 지시,
> 현재 코드, actual logs, 실행 결과가 문서보다 우선한다.

## 0. 새 세션 복구 관문

모든 Python 명령 전에 `$env:PYTHONIOENCODING='utf-8'`을 설정하고 다음 원문을 순서대로 전부 읽는다.

1. `C:\Users\roede\.codex\memories\principles.md`
2. `C:\Users\roede\.codex\memories\manual.md`
3. `C:\Users\roede\.codex\memories\speckit.md`
4. `.specify/memory/constitution.md`
5. `docs/handovers/analysis-performance-validation-canonical/04-검증방법.md`
6. `docs/handovers/analysis-performance-validation-canonical/11-핵심압축-인계정본.md`
7. `docs/handovers/analysis-performance-validation-canonical/08-단일인계-정본.md` §12
8. 이 spec의 `HANDOFF.md`, `spec.md`, `plan.md`, `tasks.md`,
   `evidence/semantic-review-40.md`
9. 필요할 때 canonical 10의 수치·이력

문서 계보는 `11 > 10 > 09 > 08`이다. 현재 규칙·확정 사실·정정·미해결·다음 행동은 이 문서가
소유한다.

## 1. 목표와 절대 이해 계약

Analyzer는 원문을 읽고 다음 전체 흐름을 사람이 이해하기 쉬운 자연어로 생성해야 한다.

```text
입력 → 검증 → 조건별 처리 → CALL/RW → 반환·외부 상태 변화 → transaction
```

모델 입장에서 다음에 모호함이 없어야 한다.

- 무엇을 해야 하고 목표가 무엇인가.
- 각 입력값은 무엇이며 어느 생산자의 사실인가.
- ID·원문 좌표·relation으로 어떤 사실끼리 연결되는가.
- 그 사실로 무엇을 판정할 수 있고 무엇은 판정하면 안 되는가.
- 어떤 우선순위와 절차로 판단해 어떤 schema 결과를 내야 하는가.
- 어떤 규칙·금지사항·자기점검을 준수해야 하는가.

최대 효율은 핵심 지침과 예시를 삭제하는 최소 토큰화가 아니다. 입력 의미, 관계, authority,
needs_context, 충돌 처리, 나쁨/좋음 예시와 자기점검을 보존하고 동일 규칙 반복과 concern에 무관한
payload만 제거한다. 봉인40 의미 목표는 약 90% 이상이며 fabrication/contradiction은 평균과 별도로
각각 0건을 목표로 한다.

## 2. 고정 결정과 범위

1. B — RULE·원문 좌표·CALL/RW·TABLE/COLUMN·sample 근거 전달을 기준으로 유지한다.
2. A — Python 관계식은 KEEP이다. spec131에서 수정하거나 재실험하지 않는다.
3. SUMMARY·RULE·EXAMPLE·TABLE·COLUMN 전체가 감사 범위다.
4. 결정론 코드는 구조·좌표·소유권·CALL/RW·전달·검증만 담당한다. 이름·패턴·정규식으로 업무
   의미를 만들지 않는다.
5. TABLE/COLUMN은 물리 이름을 번역하지 않는다. DDL/comment/code usage/sample로 입증되는 역할만
   설명한다.
6. 실제 입력으로 의미를 확정할 수 없으면 `needs_context`가 정답이다. source 근거가 있는데
   prompt까지 전달되지 않아 빈 값이면 제품 결함이다.
7. `COMPUTE`, `TAGSN`은 근거가 충분했던 omission이며 수정됐다. `ADBA_VAL`, `ETC`만 현재
   근거 부족 needs_context다.
8. 표본 결과를 RULE 전체나 다른 corpus·언어·DBMS로 일반화하지 않는다.

Architect는 Analyzer 완료 전 실행하지 않는다. 후속에서는 실제 Simplified/Detailed 경로와
`ROBO_CLUSTER_MCP_URL=http://127.0.0.1:15502/robo/mcp` 계약을 따르며 `accept/apply`는 호출하지
않는다.

## 3. 집행 규칙

### 3.1 원인 추적

틀린 좌표 하나를 같은 full node ID로 끝까지 연결한다.

```text
source → parser AST → Analyzer context/결정론 조립
→ 실제 저장된 system/user/schema → raw/parsed
→ normalizer/validator/finding → graph/API/MCP
```

- prompt template만 보고 전달됐다고 판정하지 않는다. 실제 `llm-prompts/*.jsonl`을 연다.
- source에 있으나 actual prompt에 없으면 context 생산·전달 결함이다.
- 입력과 지침이 충분하지만 raw가 틀리면 모델 한계다.
- raw는 맞지만 저장값이 틀리면 schema/normalizer/storage 결함이다.
- graph는 맞지만 API/MCP가 빠뜨리면 소비 projection 결함이다.
- 결과가 이상하면 제품보다 checker·gold·좌표계·집계 단위를 먼저 확인한다.
- 한 증상만 땜질하지 않고 생산자와 모든 소비자를 넓게 감사한다.

### 3.2 입력·prompt·출력 경계

- concern은 `TASK → OUTPUT CONTRACT → INPUT GUIDE/authority → relation → DECISION → EXAMPLES
  → SELF CHECK → variable INPUT` 순으로 읽힌다.
- SUMMARY에는 전체 흐름의 effective CALL/RW를, TABLE usage에는 해당 node의 exact direct R/W만
  전달한다. 모델이 direct/effective 범위를 이름으로 추측하게 하지 않는다.
- GPU는 업무 의미 해석만 담당한다. 좌표·조건·직접 효과·CALL/RW·소유권·순서는 결정론 구조
  사실로 제공하고 GPU가 재선택하게 하지 않는다.
- 공통 system/static prefix는 byte-identical이고 가변 evidence는 user suffix에 둔다.
- 특정 corpus 식별자·함수명·코드값·정답을 prompt/schema/validator에 하드코딩하지 않는다.
- GPU 출력량과 자율성은 최소화하되 의미를 결정론 로직으로 대체하지 않는다.

### 3.3 실패·검증·안전

- 운영/결정론 계약 실패는 `log_error + stream error + raise`한다. 조용한 빈 성공을 금지한다.
- LLM 품질 실패는 좌표·raw·evidence·reason을 finding에 남기고 trusted 파생에서 격리한다.
- 완료에는 전체 자동 테스트, 결정론 전수 검사, 봉인 표본 직접 의미 판정이 모두 필요하다.
- 기존 dirty WIP, 보존 DB와 logs는 사용자 자산이다. `reset/checkout/clean`, 범위 밖 되돌리기,
  대량 삭제, 기본 DB 초기화, 기존 격리 DB·output root 덮어쓰기를 금지한다.
- Analyzer 재분석은 공통 원인 수정, 검사기, preflight, 새 격리 DB, 별도 output root를 확정한 뒤
  Framework/DBMS에 필요한 최소 횟수만 수행한다.
- 근본 구조 제거, 광범위 계약 변경, 복구하기 어려운 삭제, 정확도 맞교환은 실행 전에 보고한다.

## 4. 현재 구현된 의미 계약

### 4.1 입력 구조와 source budget

- 배치 묶음 상한 `1,500 tokens`과 개별 source 의미 상한을 분리했다.
- 개별 원문은 `12,000 tokens`까지 그대로 전달한다. 이보다 큰 경우에만 원문을 무조건 버리는
  요약이 아니라 비권위 자식 후보를 보조 근거로 사용한다.
- 현재 DBMS corpus에서는 중간 크기 코드 노드 11개가 실제 원문 전달로 복구됐고 12,000을 넘는
  노드는 0개다. `1,500`은 의미 손실 기준이 아니라 배치 크기 기준이다.
- full node ID가 의미 identity이고 source range는 locator다. 같은 range의 nested node를
  range-only key로 합치지 않는다.

### 4.2 concern별 구조화

- SUMMARY는 source와 함께 effective CALL/RW, callee 계약, transaction을 전체 흐름 관점으로 받는다.
- RULE은 parser 소유 `when`, 원문 좌표, direct effect, CALL/RW/TABLE 근거를 받는다.
- continuation RULE의 `when`은 구조 근거로 완전히 유지하되, 자연어가 상속 조건만 열거하다 직접
  효과를 누락하지 않도록 `narrative_focus=[]`로 표시한다. 일반 분기는 현재 판별 조건만 focus다.
- RULE 자연어는 보통 240자 이내를 목표로 하되 hard cap은 600자로 분리했다.
- TABLE usage는 node가 직접 소유한 access만 받으며, COLUMN은 full column ID별 code usage,
  DDL/comment/sample을 받는다.
- EXAMPLE은 accepted RULE의 구조 조건·효과에서 결정론 합성하며 빈 outcome을 만들지 않는다.
- TABLE/COLUMN 최종 결과는 `sufficient|partial|insufficient`와 `missing_context`를 함께 저장한다.

### 4.3 응답 경계와 소비자

- 모델이 코드 literal의 `\0` 같은 JSON 비표준 escape를 복사했을 때 완전한 응답 내부의 잘못된
  string escape만 복구한다. truncated JSON은 여전히 fail-closed다. raw 응답은 보존한다.
- graph TABLE/COLUMN은 `analyzed_description`, `semantic_evidence_status`,
  `semantic_missing_context`를 저장한다.
- `node_detail(view=gwt)`는 direct TABLE/COLUMN 조회에서도 `description`, `evidence_status`,
  `missing_context`를 노출한다. source `description`이 있으면 Analyzer 설명보다 우선한다.
- code node의 기존 `summary`, `summary_evidence_status`, `summary_missing_context` 계약은 유지한다.

## 5. 현재 검증 결과

### 5.1 최종 격리 run

- Framework DB: `spec131fw1520260812`
- Framework run의 대용량 임시 output은 검증 종료 후 정리했다. 직접 판정은
  `evidence/semantic-review-40.md`와 보고용 최소 사본에 보존한다.
- Framework: SUMMARY 124, RULE 2,049, EXAMPLE 1,747, TABLE 22, COLUMN 153,
  finding 0, duplicate coordinate 0, structural preflight diff 0.
- Framework COLUMN: sufficient 146, partial 5, insufficient 2, empty 2, missing_context 7.
- DBMS DB: `spec131db920260812`
- DBMS run의 대용량 임시 output은 검증 종료 후 정리했다. 최종 수치와 직접 판정은
  `evidence/semantic-review-40.md`가 소유한다.
- DBMS: SUMMARY 479, graph RULE 68, TABLE 16, COLUMN 122, EXAMPLE 39, finding 0.
- DBMS COLUMN: sufficient 37, partial 7, insufficient 78, empty 85.

실패한 Framework final12의 원인은 CLI가 존재하지 않는 DB를 자동 생성하지 않는다는 preflight
경계였고 제품 의미 실행 전 중단됐다. 원인 기록만 유지하고 임시 output은 정리했다.

### 5.2 봉인40 직접 판정

| 판정 | 수 |
|---|---:|
| exact | 37 |
| needs_context | 2 |
| omission | 1 |
| contradiction | 0 |
| fabrication | 0 |

정답으로 인정되는 `exact + needs_context`는 `39/40 = 97.5%`다. 유일한 omission은 DBMS
`PRC_CALC_RDD01DD_B2EN2_TEST` summary가 단계별 handler와 transaction은 정확히 설명했지만
초기화 오류용 바깥/root handler를 언급하지 않은 것이다. 이는 fabrication이나 contradiction이
아니다.

`COMPUTE`는 계산 부호 역할, `TAGSN`은 필터·조인·그룹 기준이 실제 근거로 채워졌다.
`ADBA_VAL`, `ETC`는 직접 사용 근거가 없어 빈 설명과 insufficient/missing_context가 정답이다.

### 5.3 자동·actual delivery 검증

- 전체 회귀: `1062 passed, 5 skipped, 388 subtests passed`.
- prompt byte 원장: `28/28 OK`.
- Framework judge: finding/leak/vague/truncation/unmatched/orphan/conditional-missing 모두 0.
- actual prompt의 continuation R607은 full `when`, `narrative_focus=[]`, direct `cart_clear` effect를
  모두 포함했고 graph 자연어도 직접 효과와 결과 저장을 보존했다.
- 실제 MCP probe에서 `COMPUTE`는 description+sufficient, `ADBA_VAL`은 빈 description+
  insufficient+missing_context로 반환됐다. 테스트 뒤 Analyzer 임시 프로세스와 15502 listener는
  종료했다.

## 6. 증거 위치

- 봉인 목록: `evidence/sealed-sample-40.json`
- 현재 직접 판정: `evidence/semantic-review-40.md`
- 전체 계약 matrix: `evidence/semantic-contract-matrix.md`
- 모집단 inventory: `evidence/semantic-population-inventory.md`
- COLUMN usage 근거: `evidence/column-usage-grounding.md`
- 구조 preflight: `scripts/preflight_structural_obligations.py`
- run judge: `scripts/judge_run.py`
- prompt 원장 검사: `scripts/check_byte_identical.py`
- MCP projection probe 결과: 이 HANDOFF의 5.4 판정에 반영했으며 임시 logs는 정리했다.

## 7. 현재 상태와 다음 행동

Analyzer 의미 생성 계약과 Architect Strategy B 소비 계약은 현재 표본에서 모두 닫혔다.
Architect spec055는 Analyzer 최종 보존 DB를 read-only API로 열고, 별도 Architect DB
`spec055b20260812`에서 실제 Simplified/Detailed 경로를 검증했다.

- 공용 evidence packet은 이전 단계가 실제 성공 저장한 `node_detail(view="gwt")` 중 nodeId별 가장
  풍부한 한 건을 중복 제거해 Plan/Define/Tactical에 전달한다. 업무 의미를 결정론 코드로 만들지 않는다.
- packet은 세 함수 기준 40,433 bytes, RULE 118, CALL/RW 65, TABLE 3, COLUMN 27을 보존했다.
  실제 sample row는 이 payload에 0개였고 값을 만들지 않았다.
- Simplified `PRO-004`: Command 3, GWT 9, 함수 3/3, Plan 296.242초.
- Detailed `PRO-003`: Command 3, GWT 12, 함수 3/3, Tactical 165.962초; consolidate 전후 12/12 보존.
- 21 GWT 직접 source 대조에서 fabrication 0, contradiction 0. 이 수치는 세 함수 표본에만 적용한다.
- 후속 단계의 동일 nodeId MCP 재조회는 0회였고 저장 provenance를 재사용했다.
- RULE ref는 별도 DB 경계에서도 `parentId`, 실제 source file/rule line, statement,
  `coordinateOnly:true`로 보존되고 직접 TABLE READ refs도 유지된다.
- `accept/apply`는 0회다.
- Architect 전체 회귀: `1362 passed, 2 skipped, 3 warnings`.
- Architect 구현/검증 커밋: `bf4ded37b09753d1159c430e1215c8fbe1b1ea60` (`main`, origin push 완료).

새 공통 결함, 표본 확대, 다른 언어 검증 요구가 없다면 Analyzer나 Architect를 다시 실행하지 않는다.
Architect 상세 증거는 `robo-architect/specs/055-grounded-gwt-context/verification.md`와 다음 최소
보존 파일이 소유한다.

- `D:\work\robo\docs\작업보고내용\2026-08-12-evidence\architect\simplified-proposal-final.json`
- `D:\work\robo\docs\작업보고내용\2026-08-12-evidence\architect\detailed-proposal-final.json`
- 같은 폴더의 `simplified-manifest.json`, `detailed-manifest.json`

## 8. 종료 환경

- Analyzer 구현 기준점: `d4986087100275adf40f4150ed86bc82e6dc2892`; 이 HANDOFF 상태 교체도
  `main`/origin에 함께 반영한다.
- Analyzer `.env`: `ROBO_NEO4J_DATABASE=neo4j`; 변경하지 않았다.
- 기존 사용자 WIP와 모든 보존 DB/run log를 유지했다. 기본 DB를 초기화하거나 기존 격리 DB를
  덮어쓰지 않았다.
- 검증을 위해 시작한 Architect 8501, Analyzer 15502, Neo4j 7474/7687, Docker Desktop은 종료했다.
  종료 실측에서 8401도 listener가 없었다.
- Analyzer/Parser/Architect의 기존 WIP와 최종 Architect 변경은 각각 commit/push했고 remote divergence는
  0/0으로 확인했다. `accept/apply`는 수행하지 않았다.

## 9. 종료 판정

- actual prompt 이해 계약: 충족.
- 구조 obligation/finding: 0.
- 봉인40: 97.5%, fabrication 0, contradiction 0.
- 전체 회귀와 prompt byte: 통과.
- graph→API/MCP 의미·근거 상태 전달: 실제 응답 통과.
- Architect Simplified/Detailed 소비·GWT 생성·RULE/TABLE 좌표 운반: 세 함수 표본 통과.
- Architect 최종 GWT: 21/21 직접 source 정합, fabrication 0, contradiction 0.
- 측정하지 않은 전체 언어·전체 DBMS 의미 정확도는 주장하지 않는다.
