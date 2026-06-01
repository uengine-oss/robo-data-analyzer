# module_test — CodeGraph → robo 매핑/적재 PoC

CodeGraph를 **도구로** 분석대상에 돌려 만든 SQLite 그래프를,
robo Neo4j 스펙(노드 라벨·관계 타입·id)으로 **매핑하고 실제 Neo4j에 적재**하는 테스트.
(LLM 0, 의존 0 — 매핑은 표준 sqlite3, 적재는 robo venv의 neo4j 드라이버)

> **테스트 데이터는 robo의 `data/`와 분리** — 여기 `testdata/` 아래에 둔다.
> (CodeGraph가 `.codegraph/` 인덱스를 만들어 robo data 폴더를 더럽히지 않게.
>  `.codegraph/`는 재생성 가능하므로 `.gitignore`로 추적 제외.)

## 구성
```
module_test/
├── testdata/source/          ← 분석 대상 (zapamcom10060.c, 10140.c) · robo data와 분리
├── codegraph_to_robo_mapping.py   ← SQLite→robo 스펙 매핑 + 커버리지/공백 리포트 (적재 X)
├── load_to_neo4j.py               ← 매핑 결과를 Neo4j 'test' DB에 적재 (robo Cypher 그대로)
└── .gitignore                     ← **/.codegraph/ 제외
```

## 쓰는 법
```bash
# 1) 대상 인덱싱 (한 번) — testdata 안에 .codegraph 생성
cd module_test/testdata/source
codegraph init -i

# 2) 매핑 리포트 (적재 없이 무엇이 매핑/공백인지)
cd ../../..                       # robo-data-analyzer
python module_test/codegraph_to_robo_mapping.py

# 3) Neo4j 'test' DB에 실제 적재 (robo venv)
.venv/Scripts/python module_test/load_to_neo4j.py
#  → Neo4j Browser:  :use test   MATCH (n) RETURN n
```
> 적재 접속정보·DB명(`test`)은 `load_to_neo4j.py` 안에 직접 박혀 있음 — **`.env` 안 건드림**.

## 매핑 로직 (codegraph_to_robo_mapping.py = 단일 진실)

| CodeGraph | → robo | 비고 |
|---|---|---|
| node `file`/`class` | `MODULE` | id = 경로(확장자 제거, /→.) |
| node `function`/`method` | `FUNCTION` | id = `module_id.name` |
| node `import`/`route` | **DROP** | robo 스펙에 없음 |
| edge `calls` | `CALLS` | |
| edge `contains`(file→fn) | `HAS_FUNCTION` | |
| edge `imports`/`extends`/`type_of` | **DROP** | robo엔 상속/import 엣지 없음 |

## 실측 결과 (ProFrame C 2파일, 88노드/187엣지)

```
✅ 매핑됨:  FUNCTION 38, MODULE 2 / CALLS 53(→Neo4j MERGE 후 42 고유), HAS_FUNCTION 38
   유효 매핑률: 노드 40/40
❌ DROP(노이즈, 정상):  import 노드 48, imports/contains(→import) 엣지
⚠️ robo가 채워야(CodeGraph 0):  READS/WRITES, SCHEMA/TABLE/COLUMN, RULE/EXAMPLE/QUESTION, 의미
🔎 call 커버리지:
   내부 함수 호출 53 ✅ (검증: 전부 로컬 함수, 표준라이브러리 0)
   mpfmdbio(DB R/W) 29 ❌ · mpfm_dlcall(프로그램간) 11 ❌  ← robo가 잡아야
   b300_call_zapamcom10040 ✅ / mpfm_dlcall("zapamcom10040") 대상 0 ❌
✅ Neo4j 'test' 적재: MODULE 2 · FUNCTION 38 · CALLS 42 · HAS_FUNCTION 38
```

## 결론
CodeGraph는 **구조 백본(MODULE/FUNCTION/CALLS/HAS_FUNCTION)만** 채운다.
robo는 그 위에 **R/W·DB·의미·커스텀(mpfm_dlcall)** 을 LLM/스캔으로 추가해야 한다.
(상세: ../../docs/2-robo-vs-CodeGraph.md)
