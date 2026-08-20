# spec 131 — Analyzer 의미 무손실 작업 진입점

**Status**: Active
**Updated**: 2026-08-20
**Role**: 이 작업의 정본 문서·역사·증거·로컬 실행물 위치만 안내한다.

Analyzer 의미 무손실 작업 자료는 이 spec 아래에 모은다. 워크스페이스 `docs/핸드오프`에는 이
파일을 가리키는 짧은 링크만 두며, 정본 복사본·Junction·실행 폴더를 다시 만들지 않는다.

## 반드시 읽는 순서

1. `D:\work\robo\AGENTS.md`
2. `C:\Users\roede\.codex\memories\principles.md`
3. `C:\Users\roede\.codex\memories\manual.md`
4. `C:\Users\roede\.codex\memories\speckit.md`
5. `D:\work\robo\docs\robo-작업세트\ROBO-계약.md`
6. Analyzer `.specify/memory/constitution.md`
7. 이 폴더의 `ABSOLUTE_RULES.md`
8. 이 폴더의 `CURRENT_STATUS.md`
9. `history/handovers/04-검증방법.md`

자동 컨텍스트 요약, 새 세션, 작업 재개, 목표 변경 때 위 원문을 다시 읽는다. 요약문은 길찾기
용도일 뿐 사실 정본이 아니다.

## 위치와 소유권

| 위치 | 역할 | Git |
|---|---|---|
| `ABSOLUTE_RULES.md` | 영구 불변식·금지 | 추적 |
| `CURRENT_STATUS.md` | 현재 HEAD·환경·발견·미해결·다음 행동 | 추적 |
| `spec.md`·`plan.md`·`tasks.md`·`contracts/` | 요구·설계·완료 게이트·wire 계약 | 추적 |
| `history/handovers/` | 2026-08-03 이후 상세 인계·실패 교훈의 역사 원문 | 추적 |
| `evidence/` | 현재 작업의 공유 가능한 판정·계약 증거 | 추적 |
| `context/` | 문서 지도와 파일/hash 원장 | 추적 |
| `_artifacts/history-experiments/` | 기존 2026-08-03~04 raw 실험 원자료 | 로컬 보존·Git 제외 |
| `_runs/framework|dbms/` | 재생성 가능한 실제 분석 실행물 | 로컬·Git 제외 |

로컬 자료가 없다고 역사 결론을 현재 합격으로 승격하지 않는다. 필요한 원자료가 없으면
`context/EVIDENCE_INVENTORY.tsv`의 hash와 evidence 문서를 기준으로 `미검증` 처리한다.

## 실행물 승격 규칙

전체 run을 evidence로 복사하지 않는다. 재현과 판정에 필요한 manifest, source/prompt/schema hash,
actual prompt, raw/parsed 응답, finding, 정답지, 직접 판정표만 민감정보를 검사한 뒤 `evidence/`로
승격한다. 나머지 재생성 가능 로그는 `_runs/`에만 두고 작업 종료 시 정리한다.

상세 문서 계보는 [문서·증거 지도](context/DOCUMENT_MAP.md), 현재 작업 제한은
[현재상태](CURRENT_STATUS.md)를 따른다.
