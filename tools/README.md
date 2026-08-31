# Analyzer 검증·관리 도구

일반 실행자는 루트 Workspace 명령을 사용하고, 제품 분석은 `cli/analyze_data_dir.py`를 사용합니다.
이 디렉터리는 제품 runtime이 import하지 않는 명시적 도구만 둡니다.

## `verify/` — read-only 검증

| 명령 | 역할 |
|---|---|
| `check_byte_identical.py` | prompt prefix 캐시 계약 검증 |
| `judge_run.py` | 봉인 hash·Rule 의무·findings·제품 closure·활성 graph exact-once 대조 |
| `audit_coverage.py` | 함수별 구조 잔차와 unsupported 발생 집계 |
| `export_semantic_review_packets.py` | 실제 prompt·응답·Rule 원천·findings·활성 graph 직접 의미 검토 packet 봉인 |
| `inventory_semantic_outputs.py` | silent blank·status·finding·좌표 충돌 전수 감사 |

## `admin/` — 명시적 관리

| 명령 | 역할 |
|---|---|
| `reset_neo4j_schema.py` | 오염된 Neo4j 제약·인덱스 복구; 파괴 가능하므로 평상시 실행 금지 |
| `refresh_pricing.py` | LLM 가격표 수동 갱신 절차 안내 |

재사용 도구는 입력과 output을 명시적으로 받고 고객 원문을 내장하지 않습니다. Strategy A 실행기,
특정 corpus 전용 probe와 표본 수정기는 제품 저장소에 추가하지 않습니다.
