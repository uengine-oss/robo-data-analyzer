# Analyzer 분석 정확도·성능·검증 문서 지도

이 폴더는 역사와 검증 절차를 보존한다. **현재 작업 상태를 중복 소유하지 않는다.**

## 새 세션 진입

1. 현재 단일 진입점 `specs/131-cross-node-semantic-grounding/README.md`를 읽는다.
2. 영구 규칙은 `specs/131-cross-node-semantic-grounding/ABSOLUTE_RULES.md`, 현재 상태·미해결·
   다음 행동은 같은 spec의 `CURRENT_STATUS.md`만 따른다.
3. [04-검증방법.md](04-검증방법.md)는 현재도 구속력 있는 검증 절차다.
4. [11-핵심압축-인계정본.md](11-핵심압축-인계정본.md)와 나머지 파일은 역사·실패 교훈을
   확인할 때 원문으로 읽는다.
5. 문서보다 현재 코드·actual logs·실행 결과가 우선이다.

## 문서 계보와 역할

| 문서 | 역할 |
|---|---|
| `11` | 최신 역사 계보, 절대 규칙의 형성 과정, 실패 교훈, 2026-08-11까지의 검증 이력 |
| `10` | 상세 수치와 작업 이력 |
| `09` | NL 품질·지침 실험 수치 원장 |
| `08` | 상위 제약·실패 계약·종료선의 역사 정본 |
| `04` | 헌법 IX가 구속하는 3층 검증 절차 |
| `01`·`02` | 절대계약과 결정론/LLM 책임 경계 |
| `03` | 성능 실측과 원인 이력 |
| `05` | spec116 정정표 |
| `06` | 과거 재개 상태 스냅숏 |
| `07` | AST 구조 사실 생산자 계약 |

역사 안의 권위 계보는 `11 > 10 > 09 > 08`이다. 아래 번호의 “현재” 문구는 그 날짜의 역사
스냅숏이며 현재 재개 지시로 사용하지 않는다. 현재 규칙과 상태는 ABSOLUTE/CURRENT가 우선한다.

## 현재 작업 증거

- 현재 상태 정본: `specs/131-cross-node-semantic-grounding/CURRENT_STATUS.md`
- 영구 규칙 정본: `specs/131-cross-node-semantic-grounding/ABSOLUTE_RULES.md`
- 전체 문서 지도: `specs/131-cross-node-semantic-grounding/context/DOCUMENT_MAP.md`
- 요구 계약: `specs/131-cross-node-semantic-grounding/spec.md`
- 구현 순서: `specs/131-cross-node-semantic-grounding/plan.md`
- 현재 체크리스트: `specs/131-cross-node-semantic-grounding/tasks.md`
- 봉인40: `specs/131-cross-node-semantic-grounding/evidence/sealed-sample-40.json`
- 직접 판정표: `specs/131-cross-node-semantic-grounding/evidence/semantic-review-40.md`
- Framework actual prompts:
  `logs-sequence-20260811/20260811_052508_ebcac2e2c44c/llm-prompts/`
- DBMS actual prompts:
  `logs-dbms-sequence-20260811/20260811_054711_c70a30c7114f/llm-prompts/`

## 갱신 규칙

- 현재 상태는 spec131 CURRENT_STATUS 한 곳에서만 교체 갱신한다.
- 00과 이 README에는 동적 수치·다음 작업을 복사하지 않는다.
- spec은 요구, plan은 구현 순서, tasks는 현재 완료 게이트, evidence는 판정 근거만 소유한다.
- 과거 정본은 삭제하거나 현재화 명목으로 개작하지 않고 역사로 보존한다. 잘못된 사실은 해당
  문장에 명시적 정정 또는 최신 포인터를 둔다.
- 실행 명령·run id·검증 결과는 evidence/canonical 이력에 기록한다. 추측은 현재 사실에 섞지 않는다.
- 고객 원문·키·비밀번호 등 비밀 `.env` 값은 문서에 복사하지 않는다. DB 이름·포트처럼 재개에
  필요한 비밀이 아닌 환경 계약은 허용하되 실제 환경에서 다시 확인한다.
