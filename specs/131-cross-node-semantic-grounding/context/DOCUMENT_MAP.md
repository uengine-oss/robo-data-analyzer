# Analyzer 문서·증거 지도

**Status**: Active index
**Updated**: 2026-08-20
**Rule**: 이 지도는 위치와 역할만 소유한다. 각 문서의 내용·수치·결론은 원문이 소유한다.

## 1. 상태 표기

| 상태 | 뜻 |
|---|---|
| `ACTIVE` | 현재 작업에서 직접 적용하는 정본 |
| `CURRENT` | 계속 교체 갱신하는 현재상태 단일 진실 |
| `HISTORY` | 당시 판단과 실패 교훈을 보존하되 현재 사실로 자동 사용하지 않음 |
| `EVIDENCE` | 실제 prompt/raw/parsed/result/판정 원본. 요약보다 우선 |
| `EXPERIMENT` | 특정 조건의 비교 실험. 제품 기본 경로로 자동 승격하지 않음 |
| `DEFERRED` | 가치와 증거는 보존하지만 현재 범위에서 실행·수정하지 않음 |
| `SUPERSEDED` | 현재 진입점으로 사용하지 않으며 새 진입점 포인터만 따름 |
| `REVIEW` | 현재 코드·새 모델·새 무손실 계약과 다시 대조해야 함 |
| `RESEARCH` | 외부 1차 자료와 당시 적용 판단. 배포 변경 시 다시 확인 |
| `TOOL` | 실행 가능한 검증/봉인 도구. 사용 전 현재 계약과 코드를 재확인 |

`HISTORY`, `EXPERIMENT`, `SUPERSEDED`는 삭제 대상이라는 뜻이 아니다. Git 이력만으로 대체하지 않고
현재 경로에 원문을 그대로 보존한다.

## 2. 현재 Codex 작업 정본

| 경로 | 상태 | 소유 범위 |
|---|---|---|
| `specs/131-cross-node-semantic-grounding/README.md` | `ACTIVE` | 읽기 순서와 문서 라우팅 |
| `.specify/memory/constitution.md` | `ACTIVE` | Analyzer 장기 헌법 |
| `specs/131-cross-node-semantic-grounding/ABSOLUTE_RULES.md` | `ACTIVE` | 현재 사용자 지침을 반영한 불변식 |
| `specs/131-cross-node-semantic-grounding/CURRENT_STATUS.md` | `CURRENT` | 현재 환경·발견·미해결·다음 행동·충돌 |
| canonical `04-검증방법.md` | `ACTIVE` | 3층 검증과 원인 추적 절차 |
| spec 131 `spec.md`·`plan.md`·`tasks.md` | `ACTIVE` | 다음 구현의 요구·설계·완료 게이트 |
| spec 131 `HANDOFF.md` | `HISTORY` | 2026-08-12~20 상세 설계·실행 계보 |

## 3. canonical handoff 계보

경로: `specs/131-cross-node-semantic-grounding/history/handovers/`

| 문서 | 상태 | 읽는 목적 |
|---|---|---|
| `00-새세션-시작프롬프트.md` | `SUPERSEDED` | 과거 세션 복구 방식 확인 |
| `01-절대계약과-금지.md` | `HISTORY` | 현재 절대규칙이 생긴 근거와 과거 제약 |
| `02-현재설계와-책임경계.md` | `HISTORY` | 결정론/LLM 책임 경계의 설계 계보 |
| `03-성능실측과-원인.md` | `HISTORY` | 구 모델·구 파이프라인 성능과 병목 실측 |
| `04-검증방법.md` | `ACTIVE` | 헌법 IX가 지정한 검증 절차 |
| `05-spec116-정정표.md` | `HISTORY` | spec 116의 낡은 문구를 읽을 때의 정정표 |
| `06-재개절차와-현재상태.md` | `HISTORY` | 2026-08-05 상태 스냅숏 |
| `07-AST-구조사실-생산자계약.md` | `HISTORY` | Parser→Analyzer 구조 사실 계약의 계보 |
| `08-단일인계-정본.md` | `HISTORY` | 08 시점의 압축 인계와 실패 계약 |
| `09-nl품질-지침반증-인계정본.md` | `HISTORY` | NL 지침 실험과 반증 수치 |
| `10-검증체계-정립-인계정본.md` | `HISTORY` | 검증 체계·프롬프트 개편 상세 이력 |
| `11-핵심압축-인계정본.md` | `HISTORY` | 전략 A/B, 절대규칙 계보, 실패 교훈의 최신 역사 원문 |
| `README.md` | `ACTIVE` | 이 폴더 내부의 역사 문서 안내 |

역사 권위 계보 `11 > 10 > 09 > 08`은 **역사 안에서만** 유효하다. 현재 규칙과 현재상태는 각각
`ABSOLUTE_RULES.md`, `CURRENT_STATUS.md`가 우선한다.

## 4. spec 116~131 계보와 현재 처분

아래 표는 spec 원문을 지우지 않고 **보존할 계약 / 후속에서 대체된 계약 / 아직 닫히지 않은 축**을
분리한다. `보존`도 현재 코드와 재검증 없이 합격했다는 뜻은 아니다.

전체 `specs/*` 공유 정본 무결성 원장은 `FULL_SPEC_INVENTORY.tsv`다. 현재 124개 spec 디렉터리,
521개 파일, 5,924,465 bytes를 디렉터리별 tree SHA-256으로 고정했다. self-reference인 원장 2개와
Git 제외 로컬 `_runs/_artifacts`는 이 수치에서 제외한다. 이는 001~131 누락 방지 기준선이지
의미 검토 완료 선언이 아니다. 이번에 계약 계보를 직접 판정한 범위는 116~131이며, 001~115는
현재 코드 생산자·소비자 조사에서 참조가 발견될 때 해당 원문을 열어 후속 대체 관계를 판정한다.

| spec | 원래 책임 | 보존할 핵심 | 대체·미완료·현재 처분 |
|---|---|---|---|
| 116 | Rule·Example 구조화 출력과 폭주 대응 | obligation 전수, transport retry만, 좌표별 finding, parser 구조 소유, 실패 실측 | §1~80 대부분은 역사. 임의 Python leaf, content retry, source 재탐색, 당시 batch/cap은 폐기. tasks `147완료/76미완료`라 완료 정본 아님 |
| 117 | DBMS Rule row owner와 unit 배분 | routine branch table 1회, 각 row exact-one unit, 잔차·중복 0 | LLM Rule 요청은 spec 131의 결정론 Rule로 대체. 당시 DB/재실행 지시는 역사. tasks `7/5` |
| 118 | 조건 없는 controller와 PL/SQL 분기 coverage | conditionless role, ELSIF/EXCEPTION/EXIT AST·평면 complement 구조 | 옛 cold/DB 상한과 별도 Rule NL 검증은 역사. tasks `7/4`; 후속 결과를 이 파일 체크박스만으로 승격하지 않음 |
| 119 | DBMS DML direct effect 배선 | parser/linker R/W와 DML effect의 좌표·owner 연결 | 모델이 then을 고르는 경로는 결정론 Rule로 대체. COMMIT/FETCH 등 잔차와 tasks `8/3` 보존 |
| 120 | callee summary dependency 순서 | 실제 `ctx.callees` 수신자 기반 의존, 구조 의존과 call 의존 분리, cycle 표면화 | tasks `6/4`. child LLM summary가 상위 canonical 의미를 대신하는 현재 경로는 무손실 감사 대상 |
| 121 | nested-control Rule 의미 | child effect를 parent direct에 복사하지 않는 owner 경계 | 사람용 Rule prose 생산은 Rule LLM 0 계약으로 대체. final DBMS 재판정 미체크 `1` |
| 122 | TABLE 의미 evidence 권위 | COMMENT ON/일반 주석 권위 분리, direct R/W table만 의미 대상, 이름 번역 금지 | table LLM 의미 완전성은 final 재판정 미체크 `1`; 현재 cap·근거 전달 감사에 포함 |
| 123 | DBMS 명백한 leaf의 결정론 summary | 정확히 증명되는 구조 사실은 LLM 0, 같은 apply/storage 경로 사용 | `114/460` 완료 이력은 당시 corpus 한정. 자연어 의미를 결정론 템플릿으로 고정한 범위가 현재 책임 경계와 맞는지 재감사 필요 |
| 124 | callee summary 복제·이름 추론 차단 | exact direct-callee copy finding, prompt-only 수리 1회 기각, content retry 금지 | 구 모델·구 summary wire의 역사. semantic-frame validator와 겹침/중복 여부 재감사 필요 |
| 125 | Analyzer→Architect GWT evidence | 좌표·CALL/RW·Rule·sample provenance 요구와 과거 실패 증거 | `DEFERRED`. 현재 Architect 수정·실행 없음. `T011` 미완료이며 과거 2026-08-12 완료 문구는 현재 합격 근거 아님 |
| 126 | 공통 DataObjectUsageEvidence | schema/link/alias/qualified column과 exact source owner, unresolved 보존 | Framework producer-first 전환과 저장 replay가 미완료(`7/2`). 현재 Parser/Catalog/Fabric 경계 감사에 포함 |
| 127 | 전략 A/B 교차 검증 | 같은 봉인/gold로 비교, B 좌표·근거 기준, A Python 관계식 KEEP/실험 | tasks `9/0`은 당시 실험 완료. 현재 모델·semantic-frame 합격으로 자동 승격하지 않음 |
| 128 | 좌표 기반 순차 effect owner | entry/decision/continuation exact-one, 비구조 jump finding | 구조 계약은 보존. 당시 격리 DB 실행 방식은 폐기; 현재 고정 DB만 재사용 |
| 129 | DBMS CALL site truth | exact callee/argument/source identity, callee summary와 callsite 책임 분리, dynamic unresolved | 한 줄 중립 자연어를 결정론화한 범위는 현재 “구조 vs 의미” 경계에서 재감사 |
| 130 | DBLINK 원격 CALL identity | remote identity·external·local schema 비창작·quoted 반례 | 현재 구조 계약으로 보존하되 parser current AST와 graph에서 다시 전수 확인 |
| 131 | semantic-frame·무손실 감사 | 현재 `ABSOLUTE_RULES`, `CURRENT_STATUS`, contract, 원문→MCP 사슬 | `ACTIVE`. 600자 상한과 추가 후보 때문에 과거 완료 폐기. tasks 현재 `35완료/8미완료`; Architect 항목은 보류 |

체크 수는 문서 정리 시점의 기계 집계이며 의미적 완료 판정이 아니다. 특히 앞 spec의 미체크 항목이
후속 spec에서 수행됐을 수 있어도 원문 evidence와 현재 코드를 대조하기 전에는 임의로 체크하지 않는다.

## 5. 핵심 evidence 원문

전체 파일 무결성은 `EVIDENCE_INVENTORY.tsv`가 소유한다. canonical handoff, spec127, spec131
contract/evidence, spec-local raw 실험 3개 트리, 날짜별 작업보고를 합쳐 226개 파일·11,423,640 bytes를
파일별 SHA-256으로 고정했다. 이 원장은 원문 변형·누락을 찾기 위한 것이며 evidence의 타당성이나
현재성을 자동 승인하지 않는다.

| 경로 | 상태 | 소유하는 증거 |
|---|---|---|
| `specs/127-cross-strategy-evidence-validation/evidence/strategy-a-v3-results.jsonl` | `EXPERIMENT` | Strategy A v3 행 단위 실측 |
| 같은 폴더 `strategy-a-v3-summary.json` | `EXPERIMENT` | A v3 집계 |
| 같은 폴더 `strategy-b-semantic-review-20.json` | `EVIDENCE` | Strategy B 직접 의미 판정 |
| spec 127 `gold/semantic-sample-20.json`, `semantic-gold-20.json` | `EVIDENCE` | 사전 봉인 표본과 기대값 |
| spec 131 `contracts/semantic-frame-v1.md` | `ACTIVE` + `REVIEW` | 현행 wire 계약. 임의 길이·개수 상한과 현재 producer/consumer 정합 감사 대상 |
| spec 131 `evidence/actual-validation-20260812.md` | `HISTORY` + `EVIDENCE` | 작은 실제 표본과 당시 Architect E2E. 임시 DB/포트 방식과 전체 완료 주장은 현재에 승격하지 않음 |
| `apas-ast-regeneration-20260819.md` | `EVIDENCE` | APAS 23 source hash, old/new AST delta, LABEL/GOTO exact 해소 |
| `column-usage-grounding.md` | `EVIDENCE` + `REVIEW` | CodeNode→COLUMN 의미 후보 생산·전달 사슬. 현재 무손실·중간 LLM 감사 대상 |
| `example-long-predicate-revalidation-20260819.md` | `EVIDENCE` | 500자 상한으로 누락된 Example 12건의 원인과 무손실 수리 |
| `nl-producer-consumer-inventory.md` | `EVIDENCE` + `REVIEW` | 자연어 producer/schema/validator/storage/consumer 영향표. 현재 코드와 delta 갱신 필요 |
| `ownership-and-batching-20260818.md` | `EVIDENCE` + `REVIEW` | FW routine/DBMS effective AST owner와 concern별 batch. 8월 19일 Rule LLM0 override 포함 |
| `sealed-apas-semantic-sample-40-20260819.json` | `EVIDENCE` | APAS actual 이전 봉인40 ID·선정 계약. 결과를 보고 교체 금지 |
| `sealed-rule-slice-20260819.md` | `EXPERIMENT` + `EVIDENCE` | Rule 의미 실험 봉인과 loader 좌표계 오판 정정. 현재 Rule LLM0 이전 실험 |
| `sealed-sample-40.json` | `HISTORY` + `EVIDENCE` | 2026-08-12 shopmall/rwis 봉인40. 새 모델·새 APAS 표본과 혼합 금지 |
| `semantic-contract-matrix.md` | `EVIDENCE` + `REVIEW` | 의미 종류별 input/output/validator/consumer matrix. Rule 열은 8월 19일 override가 우선 |
| `semantic-generation-design.md` | `HISTORY` + `REVIEW` | Qwen3.6 기준 최소 wire/replay 설계. 5필드·600자·새 모델 차이를 현재 감사 |
| `semantic-population-inventory.md` | `HISTORY` + `EVIDENCE` | 8월 11일 graph/prompt 모집단과 DBMS COLUMN blank 122. 현재 결과 아님 |
| `semantic-review-40.md` | `HISTORY` + `EVIDENCE` | 8월 12일 사전 봉인40 직접 의미 판정. 현재 모델/현재 semantic-frame 합격 근거 아님 |
| `structured-output-primary-research.md` | `HISTORY` + `RESEARCH` | 당시 SGLang/XGrammar 1차 자료와 배포 관측. 새 서버 build에서 재확인 필요 |
| `summary-completeness-root-cause-20260819.md` | `EVIDENCE` + `REVIEW` | no-op/unused/output-only와 3-slot 과압축의 실제 원인. 5-role 구현도 현재 무상한 계약으로 재검토 |
| `seal_semantic_sample.py` | `TOOL` + `REVIEW` | 봉인 표본 생성기. 선정 규칙·중복·결과 열람 전 고정 여부를 다음 검증 전에 확인 |
| `D:\work\robo\project\robo-data-analyzer\specs\131-cross-node-semantic-grounding\_artifacts\history-experiments\structured-output-20260803\` | `EXPERIMENT` | 구조화 출력 초기 실측 원본 |
| `D:\work\robo\project\robo-data-analyzer\specs\131-cross-node-semantic-grounding\_artifacts\history-experiments\single-call-20260804\` | `EXPERIMENT` | batch/단일호출/usage 원본 |
| `D:\work\robo\project\robo-data-analyzer\specs\131-cross-node-semantic-grounding\_artifacts\history-experiments\coordinate-template-20260804\` | `EXPERIMENT` | 좌표 템플릿 실험 원본 |
| `D:\work\robo\docs\작업보고내용\2026-08-12-evidence\` | `EVIDENCE` + `DEFERRED` | 당시 Analyzer 40 판정과 Architect 산출물 |

`history/handovers/`가 canonical handoff의 실제 단일 위치다. 과거
`D:\work\robo\docs\핸드오프\분석시간-정확도-검증-정본` Junction은 2026-08-20 제거했고,
워크스페이스 `docs/핸드오프/README.md`에는 이 spec을 가리키는 링크만 남겼다.

## 6. 작업보고의 역할

`D:\work\robo\docs\작업보고내용\`은 화면 공유와 설명을 위한 날짜별 보고다. 7월 30일~8월 20일
보고서는 당시 결과를 보존하지만, 현재 코드·현재 모델·현재 검증 상태를 소유하지 않는다. 보고서의
완료 문구가 현재 `CURRENT_STATUS.md` 또는 실제 실행과 충돌하면 역사 보고로만 읽는다.

## 7. 읽는 방법

1. 현재 행동은 `README → ABSOLUTE_RULES → CURRENT_STATUS → 04`로 정한다.
2. 현재 발견의 근거가 필요할 때만 `CURRENT_STATUS`가 가리키는 source/evidence를 직접 연다.
3. 설계가 왜 그렇게 됐는지 확인할 때 `11`과 해당 spec 116~130을 연다.
4. 수치·판정은 요약문이 아니라 JSON/JSONL/actual prompt/raw/parsed 원문에서 확인한다.
5. 역사 문구를 현재 사실로 승격하려면 현재 HEAD와 같은 입력으로 다시 검증한다.
