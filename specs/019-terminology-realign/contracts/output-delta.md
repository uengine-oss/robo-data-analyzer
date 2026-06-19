# Contract: 출력 라벨·스트림 종류 변경 (소비처 약속)

**생산자**: robo-data-analyzer · **소비처**: frontend(NVL/네비게이터), catalog(read), architect(ingestion).
constitution Cross-Service Contract = "↓ Output 그래프 스키마". 본 변경(그릇 라벨·스트림 종류)은 contract 변경 → **세 소비처 동시 정합 필수**.

## 소비처가 의존해도 되는 것 (변경 후 안정)

1. **노드 식별** = `node.id`(단일, 불변 — 017).
2. **관계** = HAS_MEMBER/BELONGS_TO/PARENT_OF/NEXT/CALLS/READS/WRITES… (018, 불변).
3. **그릇 종류** = 노드의 **실제 종류 라벨**: `FILE`(C/Python/DBMS 파일) · `CLASS`(Java) · `PACKAGE`(폴더). 표시·라우팅은 이 종류로.
4. **"그릇 전체"·"호출가능 전체" 조회** = **타입 집합**으로(그릇={FILE,CLASS,INTERFACE,RECORD}, 호출가능={METHOD,FUNCTION,PROCEDURE,TRIGGER}). 역할 라벨(FUNCTION/MODULE) 없음 — 단독 역할 라벨로 종류 판단 금지.
5. **스트림/로그 노드 종류** = 그 노드의 **실제 종류**(영속 라벨과 일치). 고정 "FUNCTION" 아님.

## 마이그레이션 (지금 → 새) — 소비처 정합 표

| 항목 | 지금 | 새 | 소비처 조치 |
|---|---|---|---|
| 그릇 라벨 | `:MODULE`(+sub) | 실제 종류 `:FILE`/`:CLASS` (역할 폐기) | 종류는 실제 라벨로; "그릇 전체"는 타입 집합으로 |
| Java 메서드 | `:FUNCTION:METHOD` | `:METHOD` (역할 폐기) | 호출가능 판정 = 타입 집합으로 |
| DBMS .sql | `:MODULE`(종류 없음) | `:FILE` | FILE 카드/표시로 |
| frontend ROLE 처리 | `FILE` 을 role 로 스킵 | FILE = 실제 종류(표시) | `nodeClassify` ROLE_LABELS 에서 FILE 제외 |
| 스트림 종류 | `label:"FUNCTION"` 고정 | 노드 실제 종류 | 종류별 처리 그대로(이미 라벨로 분기) |
| 분석단위 용어(내부) | function/Function | CodeNode | (코드 내부 — 소비처 API 영향 없으면 무관) |

**옛 가정 잔재 0** — "그릇=항상 MODULE", "분석단위=function" 가정 제거. 정합 후 소비처 전수 grep.

## 영향 소비처

- **frontend**: `nodeClassify`(isContainerNode 타입집합·호출가능 타입집합·badge)·`templateRouter`(FILE/CLASS·METHOD 라우팅)·`navigator`(타입집합으로 그릇 식별·트리)·`graphStyles`(색/크기/PRIMARY_LABELS)·`analyzer.ts`(PRIMARY_LABELS). 백엔드 embed Cypher `MATCH(:FUNCTION)`→타입/id.
- **catalog**: `schema_query_service.py` 라벨 참조(`:MODULE` 등) 정합.
- **architect**: ingestion 라벨 참조 정합. architect 내 analyzer 사본 = 서브모듈 bump.

## 비변경 (불변 — 회귀 0 기준)

id 식별·018 관계(HAS_MEMBER/BELONGS_TO/NEXT…)·구조노드·매 run wipe·드래그/캔버스·실제 루틴 라벨(:FUNCTION/:PROCEDURE) 불변.
