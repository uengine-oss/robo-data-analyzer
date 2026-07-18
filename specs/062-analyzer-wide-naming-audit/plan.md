# Implementation Plan

1. tracked+신규 runtime 파일, 공개 import, 외부 runner, 환경변수 key와 동적 진입점을 동결한다.
2. `system/env_catalog.py`를 루트 `analyzer_settings.py`로 git mv하고 class/singleton/모든 소비자를 한 슬라이스로 바꾼다.
3. AST와 텍스트 후보 수확으로 모호한 파일·클래스·함수·변수를 찾고 파일별로 수정/삭제/통과 판정한다.
4. 자연스러운 책임 경계만 이동·분할·합침하고 호환 shim은 만들지 않는다.
5. 061/062 감사대장과 README를 갱신하고 정적→국소→전체→공식 실행→UI 순으로 검증한다.
6. LLM 실행 경계는 원시 응답과 usage metadata가 없는 성공을 거부하고 YAML 호출과 Agent 호출 양쪽의
   반대 사례 테스트로 비용 계측·예산 차단 누수를 막는다.
