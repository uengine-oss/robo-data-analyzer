# 작업 목록

## 완료

- [x] 소비되지 않던 Function Table Usage LLM 경로와 옛 이름 제거
- [x] 직접 Table Usage를 확정된 `READS`·`WRITES`·operation에서 결정론으로 투영
- [x] graph commit 뒤 receipt·성능 증거·terminal manifest 봉인 실패를 `published_unsealed`로 분리
- [x] 정상 `RunStopped`와 실제 workflow 오류 경합 분리
- [x] Rule 분할 용량을 model/runtime 설정으로 이동하고 atomic row oversize 검증
- [x] runtime/test 의존성 분리, exact lock 생성, 실행환경 identity 봉인
- [x] 소비자 없는 모듈·설정·ontology 값·모순 주석 제거
- [x] 최대 Summary·Rule 입력을 실제 요청 section별로 재검토하고 정확도를 해치지 않는 선에서 종료
- [x] publication·semantic plan·meaning fanout 책임 분리
- [x] 현재 SDD·공용 Analyzer skill·작업보고 동기화
- [x] 전체 비DB 회귀와 exact-once 회계 통과
- [x] 봉인된 최종 GPU 결과에서 Function·Rule·Table·Column 41개 표본 직접 판정
- [x] Analyzer와 관련 서비스에서 제거된 제품 이름·숨은 소비자 0건 확인
- [x] diff·WIP·옛 이름·비밀·공용 skill 연결 마감 감사

## 검증 경계

- 최종 GPU 전체 실행은 약속한 1회만 수행했다. 이후 변경은 결정론 로직과 검증·구조 정리이며 GPU를 재호출하지 않았다.
- Neo4j `test`, Architect, goal-verify와 사용자 WIP에는 접근·변경·stage하지 않았다.
- `scripts/recompute_strategy_a_notation.py`는 보호된 사용자 파일로 읽기·실행·stage하지 않았다.
