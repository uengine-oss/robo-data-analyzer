# Phase 1 Data Model: 용어 정체화 (before → after)

식별자 = `id` 단일(017 유지). 관계 = 018 유지(HAS_MEMBER/BELONGS_TO/NEXT…). 본 스펙 = **노드 종류 라벨 + 분석단위 용어 + 로그 표시**만 정체화.

## 1. 노드 라벨 = 자기 antlr 타입 하나 (역할 라벨 전면 폐기, 예외 0)

★확정 원칙(2026-06-19): **모든 노드 라벨 = 그 노드의 antlr `type` 하나.** 역할로 덧붙이던 라벨(MODULE/FUNCTION/VARIABLE/CONSTANT/DATA_TYPE) 전부 폐기. framework·DBMS 동일.

| 노드 | 지금 | → after | 비고 |
|---|---|---|---|
| Java 클래스 | `:MODULE:CLASS` | **`:CLASS`** | (US1 완료) |
| C/Python 파일 | `:MODULE:FILE` | **`:FILE`** | (US1 완료) |
| DBMS `.sql` 파일 | `:MODULE`(종류 소실) | **`:FILE`** | (US1 완료) |
| Java 메서드 | `:FUNCTION:METHOD` | **`:METHOD`** | 역할 FUNCTION 폐기 |
| C/Python 함수 | `:FUNCTION` | `:FUNCTION` | antlr 타입이 FUNCTION이라 그대로 |
| DBMS 함수/프로시저/구문 | `:FUNCTION`/`:PROCEDURE`/`:SELECT` | 동일 | 이미 antlr 타입(016 완료) |
| Java 필드 | `:VARIABLE:FIELD` | **`:FIELD`** | 역할 VARIABLE 폐기 |
| C 멤버/전역변수 | `:VARIABLE:MEMBER`/`:VARIABLE:GLOBAL_VARIABLE` | **`:MEMBER`/`:GLOBAL_VARIABLE`** | |
| C/Java 상수 | `:CONSTANT:DEFINE`/`:CONSTANT:CONSTANT_FIELD` | **`:DEFINE`/`:CONSTANT_FIELD`** | 역할 CONSTANT 폐기 |
| C struct/union/enum | `:DATA_TYPE:STRUCT` 등 | **`:STRUCT`/`:UNION`/`:ENUM`** | 역할 DATA_TYPE 폐기 |
| 폴더/패키지 | `:PACKAGE` | `:PACKAGE` | antlr 타입 그대로 |
| 외부 호출 stub | `:FUNCTION:EXTERNAL`/`:MODULE:EXTERNAL` | 유지 | 타입 미상 fallback(유일 예외) |

- **삭제 대상**: dataclass 칸 `primary_label`·`sub_label`(라벨 글자 조립용, 노드 속성 아님) + 온톨로지 `sub_label_of()`·`primary_label_of()`·`_ROLE_TO_LABEL`(라벨 변환기). 라벨 = `node["type"]` 직결.
- **유지**: 온톨로지 `role_of()` + yaml `roles:` — "어떤 게 모듈/함수/변수/무시인지" **분류(디스패치)** 용. ★언어별 기준이 달라(자바 FILE=무시·CLASS=그릇 / C FILE=그릇) 이게 유일 출처. 라벨엔 안 씀.
- 역할(호출가능/그릇/변수)은 소비처가 **타입집합으로 파생**: 호출가능={METHOD,FUNCTION,PROCEDURE,TRIGGER}, 그릇={FILE,CLASS,INTERFACE,RECORD}, 변수류={FIELD,MEMBER,GLOBAL_VARIABLE,VARIABLE,PARAMETER}, 상수={DEFINE,CONSTANT_FIELD}, 데이터타입={STRUCT,UNION,ENUM}.

## 2. 분석단위 코드 용어 (`Function` → `CodeNode`)

| 지금 | → after | 위치 |
|---|---|---|
| `Function` dataclass | `CodeNode` | data_models/graph_nodes.py + 전 import |
| state `functions` | `code_nodes` | state.py·artifact_writer·logger |
| step `analyze_function` | `analyze_node`(동급) | step6 |
| `upsert_functions`/`update_summaries` | `upsert_code_nodes`/… | nodes/function.py(→ 파일명도?) |
| `function_*` 지역식별자 | 종류중립 | 전반 |

★**유지(실제 의미라 옳음)**: DBMS 실제 타입 `:FUNCTION`/`:PROCEDURE` 노드 라벨, `_ROUTINE_LABELS`(호출가능 판정용 내부 집합), 진짜 "함수/프로시저"를 가리키는 표현. ★단 ontology 가 framework 메서드에 **역할 `FUNCTION` 을 라벨로 덧붙이던 것은 폐기**(메서드=`:METHOD` 단일).

## 3. 로그/스트림 표시 종류

| 지금 | → after |
|---|---|
| node_event `label:"FUNCTION"` 고정 (analyze_function_step:377) | 노드의 **실제 종류**(primary_label: SELECT/IF/PROCEDURE/FILE…) |
| rule-flow from/to `label:"FUNCTION"` (407~433) | 실제 종류(RULE 흐름은 RULE) |
| embed_step `("FUNCTION", …)` | 실제 종류 정합 |

→ **표시 종류 = 영속 라벨**(불일치 0).

## 핵심 불변식 (검증 대상)

1. 그릇 노드 표시 종류 = 실제(폴더=PACKAGE, 파일=FILE, 클래스=CLASS, DBMS=FILE). `:MODULE` 단독 표시 0(외부 stub 제외).
2. 소비처(navigator 그릇 목록·호출가능 판정 등)는 **타입에서 역할 파생**(역할 라벨 없이) → 회귀 0.
3. 로그/스트림 노드 종류 = 영속 라벨 (표시-영속 일치 100%).
4. 분석단위 명명 = 종류중립(CodeNode). 실제 루틴 라벨·판별은 유지.
5. 017 id·018 관계/구조/NEXT 불변. 프롬프트 system 본문 byte-identical(KV 캐시).
