# ROBO Data Analyzer

ANTLR Parser가 만든 구조 사실을 받아 코드 구조, 호출, 테이블 접근, 업무 의미를 분석하고 Neo4j 제품 그래프로 공개합니다.

현재 설계의 단일 기준은 [`specs/001-analyzer-redesign/README.md`](specs/001-analyzer-redesign/README.md)입니다.

## 핵심 흐름

1. DDL과 실행 설정을 읽습니다.
2. Parser JSON을 concrete code node와 구조 사실로 적재합니다.
3. 외부 설정·문서에서 검증 가능한 근거를 적재합니다.
4. Framework 프로젝트만 Scan Agent로 재사용 가능한 접근 규칙을 찾습니다.
5. 호출, 참조, 테이블 읽기·쓰기를 결정론적으로 연결합니다.
6. 함수별 의미 입력을 조립합니다.
7. Summary, Table Usage, Rule을 독립적으로 분석합니다.
8. routine 검색 embedding을 만들고 전체 제품 그래프를 transaction 하나로 공개합니다.

Analyzer는 Parser 사실을 보충하려고 source를 다시 토큰화하지 않습니다. source 기반 제품 노드는 자기 범위와 줄 번호가 일치하는 `code_text`를 가집니다.

## 주요 구조

| 경로 | 책임 |
|---|---|
| `agents/` | Framework·외부 형식 탐색 Agent |
| `agents/tools/` | Agent가 호출하는 내부 도구 |
| `api/` | 분석·제어·검색 HTTP와 MCP |
| `graph/` | 제품 그래프 계약과 Neo4j 공개 |
| `integrations/` | Catalog·Fabric 연결 |
| `llm/` | 모델 호출, cache, usage, embedding |
| `pipeline/parsing/` | Parser facts, 언어 adapter, binding |
| `pipeline/checks/` | 구조 coverage와 Rule 검증 |
| `pipeline/meaning/` | Summary·Table Usage·Rule 분석 |
| `pipeline/stages/` | `ddl`, `ast`, `external`, `scan`, `links`, `context`, `output` 실행 단계 |
| `search/` | routine 직접 검색과 관계 확장 |
| `shared/` | 설정, 파일, 이벤트, 공통 그래프 알고리즘 |
| `scripts/` | 운영자가 직접 실행하는 분석·감사 도구 |
| `tests/` | 단위·계약·통합 검증 |

## 실행

서버:

```powershell
cd D:\work\robo\project\robo-data-analyzer
.\.venv\Scripts\python.exe -m uvicorn main:app --host 0.0.0.0 --port 15502
```

기존 Neo4j database를 명시한 단발 분석:

```powershell
.\.venv\Scripts\python.exe scripts\run_analysis.py --data-dir D:\path\to\data --database analyzer_dev
```

`analyzer_dev`는 예시 이름입니다. 스크립트는 database를 만들지 않으므로 실제로 존재하며 사용이 허용된 이름을 지정해야 합니다.

## API

| 경로 | 역할 |
|---|---|
| `GET /` | health |
| `POST /robo/analyze` | 분석 시작과 NDJSON 진행 스트림 |
| `POST /robo/analyze/control` | pause, resume, stop |
| `POST /robo/search/semantic` | routine 직접 의미 검색 |
| `/robo/mcp` | 검색과 semantic detail MCP |

## 검증

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -t . -p 'test_*.py'
```

Neo4j 통합 검증은 허용된 기존 database에서만 수행합니다. 새 database를 자동 생성하지 않습니다.
