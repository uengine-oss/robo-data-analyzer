# Stop-aware postprocess logging

## Goal

사용자가 분석을 중단한 정상 제어 흐름과 실제 분석 실패를 후처리 로그에서 구분한다.

## Requirements

- `PauseController.isStopped=true`로 인계가 닫히면 후처리 skip은 INFO `analysis_stopped`다.
- stop이 아닌 인계 abort만 WARNING `analysis_aborted`다.
- 컴포넌트 군집, 모듈 요약, 테이블 설명, 용어사전은 같은 판정 정책을 사용한다.
- run executor의 `phase_event: stopped`와 `run: stopped` 정상 종료 계약은 보존한다.
- 사용자 stop을 `run_failed` 또는 fatal stream error로 표시하지 않는다.
- 품질·안전 가드레일 개입은 WARNING이어도 `guardrail_applied`와 방어 조치를 명시한다.
- 측정만 하고 산출물을 바꾸지 않는 가드레일은 `guardrail_observed`로 구분한다.
- 일반 호출 실패 복구는 가드레일로 과장하지 않고 `recovery_started`로 기록한다.
