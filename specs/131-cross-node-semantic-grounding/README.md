# spec 131 — Analyzer 의미 무손실 작업 진입점

**Status**: Active
**Updated**: 2026-09-01
**Role**: 현재 Analyzer SDD와 작업 상태의 단일 진입점

이 디렉터리에는 현재 실행 가능한 Analyzer spec만 둔다. 과거 보고서·실험·실행 결과를 다시
복사하지 않는다.

## 읽는 순서

1. `D:\work\robo\AGENTS.md`와 공통 memory 세 파일
2. Analyzer `.specify/memory/constitution.md`
3. 이 폴더의 `spec.md`
4. 이 폴더의 `CURRENT_STATUS.md`
5. 현재 작업에 필요한 `plan.md`, `tasks.md`, `contracts/`의 해당 절

## 현재 정본

| 파일 | 소유하는 내용 |
|---|---|
| `spec.md` | 불변식·책임 경계·제품 요구·합격 기준 |
| `plan.md` | 현재 spec을 구현하는 순서와 검증 확대 방식 |
| `tasks.md` | 남은 작업과 완료 증거 |
| `contracts/parser-structural-facts-v2.md` | Parser 구조 사실 wire |
| `contracts/analyzer-meaning-and-publication-v2.md` | Analyzer 의미·공개 wire |
| `contracts/import-binding-environment-v1.md` | 선택 source 환경의 import 결속·종료 상태 wire |
| `CURRENT_STATUS.md` | 현재 HEAD·완료·미검증·다음 행동 |

## spec 밖 자료

| 위치 | 역할 |
|---|---|
| `D:\work\robo\_playground\analyzer\{profiles,runs,experiments,archive}` | product-external run and diagnostic resources |
| `D:\work\robo\docs\작업보고내용\2026-08-26-Analyzer-최종-구조-고해성사.html` | 현재 Analyzer를 사람이 읽는 최종 보고서 |

playground runs의 과거 수치는 현재 제품 합격 근거가 아니다. 재분석 없이 원인을 추적하거나 마지막 봉인
검증을 수행할 때만 필요한 파일을 선택해서 읽는다. 소비처가 없던 과거 실험 `_artifacts`는 제거했다.
Architect와 goal-verify는 이 spec의 범위 밖이다.
