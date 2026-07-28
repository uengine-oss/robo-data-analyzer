# Plan: Stop-aware postprocess logging

1. 인계 abort 소비자 네 곳의 중복 문구와 pause 상태를 조사한다.
2. stop/failure 판정을 공용 helper 한 곳에 둔다.
3. 사용자 stop과 실제 failure의 log level·operation·message를 단위 검증한다.
4. 가드레일 적용·관측과 일반 복구를 명시적인 operation으로 분리한다.
5. 관련 pause/handoff/postprocess 회귀를 실행한다.
6. 실제 E2E 중단은 취소 증거로만 기록하고 새 쇼핑몰 run은 complete를 요구한다.
