# codebase_detection_comparison

임의의 코드베이스 경로 1 개를 받아 **프레임워크 패턴** 과 **DB 접근 패턴** 을 자연어로 출력하는 Deep Agent.

## 사용법

```bash
python -m test.codebase_detection_comparison.analyze <프로젝트_경로> [--timeout 600] [-v]
```

6 개 프로젝트 일괄 실행:

```bash
bash test/codebase_detection_comparison/test_results/_run_all.sh
```

## 설계

**결정론 수집 (Python) + 판단·탐색 (Deep Agent LLM)** 두 단계.

1. `collector.collect(root)` 가 확장자 분포, 매니페스트, 설정, import 빈도, 대표 파일 뼈대, 함수 인덱스, 리소스 파일 목록을 수집해 한 장짜리 컨텍스트로 렌더.
2. 이 컨텍스트를 첫 user 메시지로 Deep Agent 에 주입. LLM 이 아래 3 도구만으로 패턴을 확정:
   - `grep(pattern, path_glob)` — 전역 정규식 검색
   - `read_file(path, max_lines)` — XML/SQL/설정 등 직접 읽기
   - `read_function(file, function)` — tree-sitter 로 함수 본문만 추출
3. LLM 이 tool_call 없는 마지막 텍스트 메시지로 2 블록 요약 출력 → `extract_final_text` 가 수거.

별도 `submit_*` 도구·캐시·리소스 분류 없음. 끝은 자연스러운 LLM 종료.

## 원칙

- **관찰한 것만 쓴다** — 관찰하지 않은 테이블/함수/호출 지어내기 금지.
- **패턴이 목적, 테이블은 덤** — 호출 방식 + 식별 방식 관찰되면 즉시 종료. 테이블 전수 수집 안 함.
- **도구 호출 5 회 이내 권장** — 이미 본 증거로 충분하면 바로 출력.

## 출력 형식

```
프레임워크 패턴
- 식별된 이름 + 관찰 근거 원문 인용.

DB 접근 패턴
- 호출 방식: 실제 코드 라인 원문 1~2 개.
- 식별 방식: 문자열 리터럴 / XML 매퍼 id / JPA 어노테이션 / 헤더 파일명 등.
- 명명 규칙: 관찰된 패턴 (예: z*_*_s0000). 없으면 생략.
- 관찰된 테이블 예시: 이미 본 범위에서 3~5 개.
```

## 파일 구조

```
codebase_detection_comparison/
├── analyze.py                 # CLI 진입점
├── agent.py                   # Deep Agent + 3 도구
├── collector.py               # 결정론 수집
├── prompts/
│   └── analysis_context.txt   # 첫 user 메시지 템플릿
├── tools/
│   └── tree_sitter_tools.py   # 언어 무관 AST 추출
├── test_results/
│   ├── _run_all.sh            # 6 개 프로젝트 일괄 실행
│   └── *.log
├── TROUBLESHOOTING.md         # hang/tool_call 이슈 핵심 기록
└── README.md
```

## 성능 (6 개 테스트 프로젝트)

| 프로젝트 | 시간 | tool_calls | 특징 |
|---------|------|-----------|------|
| apascrt00010t02 | 17s | 5 | C/ProFrame — `mpfmdbio("z*_*_s0000")` + `pdb_*.h` 헤더 |
| library_system | 8s | 3 | Python sqlite3 래퍼 — SQL 리터럴 |
| loan-ejb-app | 15s | 6 | EJB 2.x + JDBC 혼용 |
| ofbiz-order-standalone | 9s | 2 | OFBiz — `EntityQuery.from("X")` + entitymodel.xml |
| swing_system | 9s | 2 | C/Pro*C 시뮬 — `db_<entity>_<action>` |
| ukesa | 9s | 2 | iBATIS — namespace + statement id |

총 92s. 프로젝트당 평균 ~15s / 3 tool calls.

## 참고 문서

- [TROUBLESHOOTING.md](TROUBLESHOOTING.md) — Deep Agent hang 원인 (tool_call vs text response) 및 도구 수 폭증 이슈.
