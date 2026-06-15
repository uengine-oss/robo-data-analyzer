# Contract: Analyzer Data Input

analyzer가 의존/노출하는 경계.

## C1. 소비 입력 (↑ antlr 006이 생성)

analyzer는 **`data/{source, ddl, analysis}`만** 소비한다.

| 폴더 | 소비 단계 | 기대 |
|---|---|---|
| `data/source` | step3 스캔, step6 결정론 링킹(grep) | 소스 원본 |
| `data/ddl` | step1 parse_ddl(sqlglot) | **표 정의만**(프로시저 0 — antlr 006 불변식) |
| `data/analysis` | step2 load_ast | AST JSON(노드 스키마 불변) |

**의존**: `data/ddl` 청결은 antlr 006(US2)이 보장. analyzer는 신뢰하고 그곳만 읽는다.

## C2. `POST /robo/analyze` (요청 — 역호환 단순화)

```json
{
  "strategy": "framework|dbms",   // 유지
  "project_root": "...",          // (변경) 수용하되 inert — 소스를 바꾸지 않음
  "ddl_dir": "...",               // (변경) 기본 data/ddl; 명시 시도 data/ 하위 권장
  "has_xml_mapper": false, "locale": "ko", "datasource": null  // 유지
}
```

- 출력(NDJSON 스트림)·제어(`/analyze/control`)·Neo4j override 헤더 처리: **불변**.

## C3. 다운스트림 출력 (↓ robo-architect)

- Neo4j 그래프 스키마(노드 라벨/엣지 타입): **불변**(헌법 VI). 이 기능은 출력 계약을 건드리지 않는다.

## 불변·호환

- 분석 알고리즘·LLM 프롬프트·그래프 스키마 **불변**.
- `project_root` 필드는 한동안 수용(옛 클라이언트 무중단) 후 별도 제거.
- AST 입력 계약(`data/analysis` 노드 스키마) **불변**.
