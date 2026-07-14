# Analyzer CLI 가이드

일반 실행자는 루트 Workspace 명령을 사용합니다.

```cmd
cd D:\work\robo\robo-workspace
robo.cmd up analyzer
```

이 폴더는 Analyzer 내부 작업용 CLI만 둡니다.

| 구분 | 명령 | 용도 |
|---|---|---|
| 표준 분석 | `analyze_data_dir.py` | 한 프로젝트 또는 전체 데이터 디렉터리 분석 |
| 기준선 | `baseline_runner.py` | 여러 프로젝트 실행 결과 비교 |
| CI 검증 | `check_byte_identical.py` | 프롬프트 바이트 동일성 검사 |
| 데이터 재생성 | `reembed_graph.py` | 전체 재분석 없이 검색 임베딩 재생성 |
| 데이터 재생성 | `resynthesize_glossary.py` | 전체 재분석 없이 용어사전 재종합 |
| 복구 | `reset_neo4j_schema.py` | 오염된 Neo4j 제약·인덱스 재생성 |
| 유지보수 | `refresh_pricing.py` | LLM 가격표 갱신 안내 |
| 스펙 검증 | `verify_045_046.py` | spec 045·046 전용 실측 검증 |

`reset_neo4j_schema.py`는 파괴적 복구 도구이므로 평상시 실행하지 않습니다. 스펙 전용 검증 파일은 해당 스펙 종료 후 `tests/` 또는 스펙 산출물로 이동할지 별도 판단합니다. 현재 사용자 WIP인 파일은 이동·삭제하지 않습니다.
