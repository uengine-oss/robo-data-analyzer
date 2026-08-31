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
| 봉인 실행 전수 회계 | `judge_run.py` | 검증자 | 지정 run의 봉인 hash·Rule 의무/findings·제품 closure·활성 graph를 재분석 없이 exact-once 대조 |
| 구조 누락 감사 | `audit_coverage.py` | 검증자 | 현재 run의 함수별 구조 잔차와 unsupported 발생을 corpus 단위 집계 |
| 복구 | `reset_neo4j_schema.py` | 운영자 | 오염된 Neo4j 제약·인덱스 복구 |
| 유지보수 | `refresh_pricing.py` | 개발자 | LLM 가격표 수동 갱신 절차 안내 |
| 직접 의미 검토 export | `export_semantic_review_packets.py` | 검증자 | 지정 run의 실제 prompt·응답·Rule 원천·findings·활성 graph를 선택 target 또는 전수 packet으로 봉인 |
| 의미 빈값 전수 감사 | `inventory_semantic_outputs.py` | 검증자 | actual prompt와 지정 graph를 read-only 조인해 silent blank/status/finding/좌표 충돌을 집계 |
| 수동 GPU 대화 | `send_text_to_gpu.py` | 개발자 | workspace `gpu-chat.cmd`가 사용하는 최소 텍스트 클라이언트 |

`reset_neo4j_schema.py`는 파괴적 복구 도구이므로 평상시 실행하지 않습니다.
Strategy A 실행기, 특정 APAS/쇼핑몰 run을 전제로 한 표기법 probe와 표본 수정기는 저장소에 남기지 않습니다.
재사용 CLI는 입력을 명시적으로
받고, 고객 원문을 내장하지 않으며, 결과를 지정 output/evidence 경로에만 씁니다.
`tests/`에는 재사용 가능한 자동 회귀만 둡니다.
