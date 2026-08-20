# Analyzer 의미 무손실 작업 절대규칙

**Status**: Active
**Updated**: 2026-08-20
**Purpose**: 컨텍스트 요약·새 세션마다 가장 먼저 읽는 짧은 불변식 정본

이 문서는 과거 실험 이력이나 진행률을 소유하지 않는다. 현재 작업 위치는 `CURRENT_STATUS.md`, 상세 검증 절차는 `specs/131-cross-node-semantic-grounding/history/handovers/04-검증방법.md`, 전략 A/B 역사와 실패 교훈은 `11-핵심압축-인계정본.md`, 실제 수치는 각 `evidence/`가 소유한다.

## 1. 권위와 재독

1. 현재 사용자의 명시 지시가 최우선이다.
2. 그다음은 현재 source·AST·actual prompt/raw/parsed·DB/API 실행 결과다.
3. constitution·현행 contract/spec이 그 뒤이며, 과거 handoff·과거 완료 보고는 역사 증거다.
4. 자동 컨텍스트 요약, 새 세션, 작업 재개, 목표 변경 때 `principles.md`, `manual.md`, `speckit.md`, Analyzer constitution, 이 문서, `CURRENT_STATUS.md`, 검증 정본 04를 다시 읽는다.
5. 문서 간 충돌은 숨기지 않고 `CURRENT_STATUS.md`의 충돌 원장에 기록한다. 최신 사용자 지침과 현재 실행 증거로 해소하기 전에는 구현하지 않는다.

## 2. 현재 작업 범위

- 현재는 Analyzer 전체와 Analyzer에 직접 입력을 공급하는 Parser·Catalog·Fabric 계약까지만 수정·검증한다.
- Analyzer의 Neo4j 저장과 Analyzer MCP/API 출력 경계까지 무손실성을 확인한다.
- **Architect는 현재 보류다.** Architect 코드·설정·서비스·GWT 실행은 수정하거나 실행하지 않는다. 이미 발견한 Architect 후보는 보류 근거만 보존한다.
- 사용자 WIP와 관련 없는 저장소 변경은 읽기만 하고 stage·수정·되돌리기·삭제하지 않는다.

## 3. 임의 상한과 의미 손실 금지

1. 원문 source bytes와 hash가 권위 정본이다. Analyzer는 원문을 바꾸거나 일부만 canonical source로
   치환하지 않는다.
2. 큰 원문은 AST owner·좌표·의존 관계로 나눌 수 있지만, 분할 전후 source 범위의 합집합이 원문과
   정확히 같고 겹침·빈틈·순서 손실이 0이어야 한다. 분할은 손실 압축이 아니라 무손실 구조화다.
3. 의미 문자열, Rule/Example, 노드, 테이블, 컬럼, 조건, 효과, 질문, GWT 등 제품 결과를 임의 문자 수·개수·Top-N으로 자르지 않는다.
4. 정확한 source/AST 의무 수를 결정론적으로 알 수 있을 때만 그 exact 개수를 schema와 validator가 강제할 수 있다.
5. batch의 item/token/concurrency 값은 **실행 단위**일 뿐 결과 상한이 아니다. 전체 의무를 서로 다른 payload로 나눠 전부 처리하고 canonical ID로 정확히 한 번씩 합친다.
6. 합격식은 `입력 의무 = trusted + quality finding + unavailable`, 누락 0, 중복 0, 미분류 0이다.
7. provider/context/output 한계에 닿으면 내용을 줄여 성공시키지 않는다. 미처리 의무 ID·source 좌표·원인·raw/partial을 보존하고 fail-closed 또는 좌표별 quality finding으로 표면화한다.
8. 표시·로그 preview만 줄일 수 있다. 단 canonical 전체 값이 별도로 무손실 보존되고, preview가 LLM·validator·저장 truth·MCP 판단 입력으로 재사용되지 않으며, 잘림 표시와 전체 값 위치가 있어야 한다.
9. `600을 1200으로`, `4개를 8개로` 바꾸는 식의 숫자 이동은 근본 수정이 아니다.
10. 목표 정확도는 이상적으로 100%다. 90%나 그보다 낮은 실측은 숨기지 않고 그대로 보고하되,
    그 수치가 나머지 의무를 버려도 되는 허용 손실률이나 합격선이 되지는 않는다.

## 4. LLM 책임과 중간 열화 금지

1. Framework 의미 owner는 routine(`FUNCTION|PROCEDURE|METHOD|TRIGGER`)이다. 내부 CALL/CONTROL/STATEMENT/RETURN은 routine 의미를 위한 구조 근거다.
2. DBMS 의미 owner는 skip-transparent effective AST의 각 CodeNode다. Framework와 같은 호출·소유권으로 합치지 않는다.
3. Parser/결정론 코드가 node/owner/좌표/순서/조건/CALL/RW/RETURN/transaction/Rule/Example/applicable obligation을 소유한다.
4. Rule은 LLM 0의 exact condition/effects이고 Example은 accepted Rule에서 LLM 0으로 무손실 합성한다.
5. LLM은 충분한 근거가 제공된 natural-language 의미와 sufficiency만 판정한다. 구조를 만들거나 누락된 의미를 이름·상식·기본값으로 채우지 않는다.
6. LLM 결과를 다른 LLM이 다시 요약·번역해 canonical truth를 교체하지 않는다. aggregate 의미가 필요하면 child frame과 exact evidence ID를 보존한 추가 claim이어야 하며 child truth를 덮지 않는다.
7. 큰 부모의 원문을 child summary로 바꿀 때 cross-child 조건·순서·효과가 사라질 수 있으면 손실 압축이다. 원문/구조 evidence를 분할 전달하거나 명시적 한계로 종료해야 하며, 비권위 요약만으로 상위 의미를 확정하지 않는다.
8. sanitizer·dedupe·normalizer·projection은 형식 정규화만 한다. 의미 일부 삭제·합성·축약·조용한 빈값 치환을 하지 않는다.
9. 같은 내용의 LLM 재시도와 별도 LLM judge/repair를 추가하지 않는다. transport retry와 content retry를 구분한다.
10. **큰 원문을 LLM 요약문으로 바꿔 다음 의미 판정의 권위 입력으로 사용하지 않는다.** 표시용
    summary, child summary, module summary는 원문·AST·exact evidence를 대체할 수 없다.
11. context 축소가 필요하면 먼저 target의 의미 의무 ID를 전수 확정하고, 의무별로 exact source span,
    조상 path condition, 참조 definition/symbol, direct callee, TABLE/COLUMN 근거를 닫힌 집합으로
    수집한다. 이것은 산문 요약이 아니라 좌표가 있는 구조 projection이다.
12. 그 폐쇄 집합도 provider context를 넘으면 source를 자르거나 요약하지 않고 의미 의무를 더 작은
    독립 단위로 분해한다. 각 단위는 canonical obligation ID와 source coverage를 보존하며, 결정론
    merge가 누락·중복·순서·cross-boundary dependency 잔차 0을 검사한다.
13. 분할 경계를 넘어야만 판단 가능한 의미는 임의로 `sufficient`로 닫지 않는다. 필요한 이웃
    obligation/source 좌표를 연결해 다시 계획하거나 `partial|needs_context`로 명시한다.
14. context compaction 후보는 가장 큰 사전 봉인 표본에서 `exact-source baseline`과 같은 의무별로
    직접 비교한다. omission/contradiction/fabrication이 늘면 속도가 빨라도 폐기한다.
15. 사람이 읽는 최종 `SUMMARY`와 다음 의미 판정을 위한 입력 압축을 구분한다. `SUMMARY`는 canonical
    frame 전체에서 파생한 비권위 projection이고, 결과를 바꾸는 모든 required obligation이 어느
    summary clause 또는 별도 상세 claim에 보존됐는지 역추적 가능해야 한다. 짧게 보이게 할 필요가
    있으면 별도 preview를 만들 뿐 canonical frame이나 전체 summary를 덮어쓰지 않는다.
16. 대화·에이전트의 자동 context summary도 길찾기용 index일 뿐 사실 정본이 아니다. 재개할 때
    summary를 다시 요약하지 않고 이 문서의 재독 순서와 source/hash/evidence 원장으로 복구한다.

## 5. 원인 추적

틀린 좌표 하나에서 시작하되 같은 생산자·계약·소비자 전체로 확장한다.

```text
원문 source
→ Parser AST
→ Analyzer 결정론 조립·owner·obligation
→ 실제 직렬화 prompt/system/user/schema
→ raw/parsed provider 응답과 finish reason
→ normalizer/validator/finding
→ Neo4j canonical frame와 projection
→ Analyzer API/MCP packet
```

- 모델은 마지막 용의자다.
- checker·gold·좌표계·occurrence/unique 집계를 제품보다 먼저 반증한다.
- 원문에는 있으나 prompt에 없으면 upstream/context 결함, prompt에는 있으나 raw에서 빠지면 provider/prompt/schema 결함, raw에는 있으나 후단에서 사라지면 normalizer/storage/projection 결함이다.
- 원인 고리마다 `파일:행`, 실제 입력값, 변환값, 출력값을 남긴다. 추측 고리가 하나라도 있으면 수정 단계로 넘어가지 않는다.
- 막히면 코드·로그·Git·최소 재현·역실험을 먼저 하고, 저장소 밖 사실은 공식 문서·논문·공식 source를 조사한다. 그래도 설계가 갈리면 필요한 질문과 이유를 적어 사용자에게 딥리서치 도움을 요청한다.

## 6. 검증

세 층이 모두 끝나야 `검증됨`이다.

1. 자동 테스트와 prompt/schema byte 계약
2. 결정론 전수 회계: AST occurrence, CALL/RW/RETURN, Rule rows, Example, slot/evidence population과 잔차 0
3. 결과 열람 전에 봉인한 Framework/DBMS 혼합 30~40개를 주 에이전트가 source→actual input→output→stored 값으로 직접 의미 판정

exact/partial/contradiction/fabrication/omission/needs_context를 분리한다. 한 표본 결과를 전체 언어·전체 노드로 일반화하지 않는다. 성능은 calls/input/output/TTFT/E2E/wall/queue/cache와 정확도를 함께 보며 정확도와 교환하지 않는다.

## 7. Neo4j·실행 자원

1. **Neo4j가 꺼져 있거나 인증·`SHOW DATABASES` 확인이 실패하면 Analyzer 작업을 시작하거나 계속하지 않는다.** 코드·문서·분석 실행을 멈추고 상태만 보고한다.
2. 허용 database는 `neo4j`, `test`, `framework`, `dbms`와 Neo4j 관리용 `system`뿐이다. 임시·격리 database를 새로 만들지 않는다.
3. 실제 Framework 재분석은 `framework`, DBMS 재분석은 `dbms`를 비우고 재사용한다. `neo4j`와 `test`는 명시된 목적 없이 비우지 않는다.
4. 실행 산출물은 **이 spec의** `_runs/framework`, `_runs/dbms` 아래에서만 교체한다. 저장소나
   워크스페이스 루트에 날짜별·실험별 폴더와 임시 proxy/upstream을 누적하지 않는다.
5. 실행 전후 서비스·포트·DB·폴더 소유권을 확인한다. 종료 시 이번 작업이 만든 임시 프로세스·파일·허용 목록 밖 DB를 정리하되, 사용자 WIP와 정본 evidence는 삭제하지 않는다.

## 8. SDD·Git·완료

- 구현 전에 spec/contract/plan/tasks와 실패·경계·과대 입력 반증 테스트를 먼저 고정한다.
- 발견한 원칙 위반을 범위 밖으로 미루지 않는다. 다만 현재 보류된 Architect는 후보와 영향만 기록하고 수정하지 않는다.
- 특수 corpus 이름·함수명·상태값을 제품 코드나 prompt에 하드코딩하지 않는다.
- 변경은 책임별 작은 슬라이스로 검증 후 커밋·푸시한다. 관련 없는 WIP를 stage하지 않는다.
- 완료 보고는 `검증됨 / 실패 / 미검증 / 보류`를 나눠 쓰고, 테스트 통과만으로 의미 정확도를 주장하지 않는다.

## 9. 전략 A/B 상세 보존 위치

- 절대규칙·실패 교훈: `specs/131-cross-node-semantic-grounding/history/handovers/11-핵심압축-인계정본.md`
- Strategy A/B spec: `specs/127-cross-strategy-evidence-validation/spec.md`, `plan.md`, `tasks.md`
- A v3 실측: `specs/127-cross-strategy-evidence-validation/evidence/strategy-a-v3-results.jsonl`, `strategy-a-v3-summary.json`
- B 직접 판정: `specs/127-cross-strategy-evidence-validation/evidence/strategy-b-semantic-review-20.json`
- 봉인 정답지: `specs/127-cross-strategy-evidence-validation/gold/semantic-sample-20.json`, `semantic-gold-20.json`

현재 역사 판정은 **B 좌표·근거 전략이 기준, A Python 관계식은 KEEP/실험**이다. 이 기록을 삭제하거나 A를 자동 폐기·승격하지 않는다. 현재 무손실 감사에서 두 전략의 전제도 현재 코드와 실측으로 다시 반증한다.
