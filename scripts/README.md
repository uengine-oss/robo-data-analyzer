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
| 기준선 | `baseline_runner.py` | 개발자 | 여러 프로젝트 회귀 비교 |
| CI 검증 | `check_byte_identical.py` | 개발자/CI | 프롬프트 prefix 캐시 계약 검증 |
| 데이터 재생성 | `reembed_graph.py` | 운영자 | 전체 재분석 없이 검색 임베딩 재생성 |
| 데이터 재생성 | `resynthesize_glossary.py` | 운영자 | 전체 재분석 없이 용어사전 재종합 |
| 복구 | `reset_neo4j_schema.py` | 운영자 | 오염된 Neo4j 제약·인덱스 복구 |
| 유지보수 | `refresh_pricing.py` | 개발자 | LLM 가격표 수동 갱신 절차 안내 |

`reset_neo4j_schema.py`는 파괴적 복구 도구이므로 평상시 실행하지 않습니다.
특정 spec 한 번만 검증하는 스크립트는 공용 CLI 목록에 추가하지 않고 해당
`specs/<feature>/` 또는 `tests/`에서 관리합니다.
