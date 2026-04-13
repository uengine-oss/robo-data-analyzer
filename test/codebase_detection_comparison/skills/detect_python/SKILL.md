# detect_python — Python 코드 탐색 매뉴얼

**답안지 없음**. 이 문서는 "Python 프로젝트를 만났을 때 어디를 먼저 볼지" 에 대한 탐색 팁입니다.

## 1순위 단서: 매니페스트

- `requirements.txt`, `pyproject.toml`, `Pipfile`, `setup.py`, `setup.cfg`
- 이 파일 하나만 읽으면 의존성 80% 확정. 프레임워크 이름이 그대로 있음.

## 2순위: 설정 파일

- `.env`, `settings.py`, `config.py`, `docker-compose.yml`
- DB 연결 문자열(`neo4j://`, `postgresql://`, `redis://`)이 여기 있음.

## 3순위: import 빈도 + 호기심 탐색

- 의존성 파일이 없거나 부족하면 `grep_imports('.py')` 로 빈도 분석
- 미지의 import (`xxx.*`) 가 자주 보이면:
  - `find_definition('xxx')` 로 선언부 추적
  - `read_file_range` 로 구현 확인
  - `collect_unknown_evidence('xxx')` → `characterize_unknown_library('xxx')`

## 자주 놓치는 것

- 가상환경 디렉토리 (`venv/`, `.venv/`) 는 스캔에서 제외 (자동).
- 테스트 파일(`tests/`)의 import 는 실제 프로덕션 의존성이 아닐 수 있음 — 가중치 낮추기.
- `__init__.py` 의 re-export 로 의존성이 감춰진 경우 있음.
