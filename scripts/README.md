# Analyzer CLI 가이드

일반 실행자는 루트 Workspace 명령을 사용합니다.

```cmd
cd D:\work\robo\robo-workspace
robo.cmd up analyzer
```

이 폴더는 Analyzer 개발·운영자가 명시적으로 실행하는 CLI만 둡니다. 애플리케이션
런타임은 이 폴더를 import하지 않으므로 Python 패키지로 취급하지 않습니다.

| 구분 | 명령 | 사용자 | 유지 이유 |
|---|---|---|---|
| 표준 분석 | `analyze_data_dir.py` | 운영자 | API와 같은 분석 파이프라인의 공식 CLI |
| CI 검증 | `check_byte_identical.py` | 개발자/CI | 프롬프트 prefix 캐시 계약 검증 |
| 데이터 재생성 | `reembed_graph.py` | 운영자 | 전체 재분석 없이 검색 임베딩 재생성 |
| 데이터 재생성 | `resynthesize_glossary.py` | 운영자 | 전체 재분석 없이 용어사전 재종합 |
| 복구 | `reset_neo4j_schema.py` | 운영자 | 오염된 Neo4j 제약·인덱스 복구 |
| 유지보수 | `refresh_pricing.py` | 개발자 | LLM 가격표 수동 갱신 절차 안내 |

`reset_neo4j_schema.py`는 파괴적 복구 도구이므로 평상시 실행하지 않습니다.
일회성 검증 스크립트는 저장소에 남기지 않습니다 — `specs/`와 `tests/`에도 두지
않고, 세션 임시 영역에서 실행한 뒤 입력 데이터·절차·결과를 보고 문서에 전문
기록하는 것으로 재현성을 보존합니다. `tests/`에는 재사용 가능한 자동 테스트만
둡니다.
