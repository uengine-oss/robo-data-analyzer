# rules

LLM 프롬프트 YAML. `RuleLoader(target_lang="framework"|"exp"|"dbms"|"common")` 로드. **미사용 규칙은 제거함.**

## exp/ (exp 파이프라인 전용)

| 파일 | 사용처 |
|------|--------|
| call_extract | exp Call batch (obj_name, method_name, 비즈니스 호출만, 단일/배치 items) |
| sql_table_extract | exp step5 SQL→테이블 (단일/배치 sql_items) |

## framework/ (exp step6 일부 + analyzer 공용)

| 파일 | 사용처 |
|------|--------|
| analysis | exp step6 메서드 요약, analyzer framework 분석 |
| association | analyzer framework ASSOCIATION |
| class_summary_batch | exp step6 클래스 요약 |
| class_user_story | analyzer framework |
| field_summary | exp step6 필드 요약 |
| method_summary_enhancement | exp step6 호출그래프 보강, analyzer framework |
| stereotype | analyzer framework |

## dbms/ (analyzer dbms 전략)

| 파일 | 사용처 |
|------|--------|
| analysis, dml | analyzer dbms |
| node_context | analyzer dbms 노드 컨텍스트 |
| procedure_summary_only, procedure_user_story | analyzer dbms 프로시저 |
| table_summary | analyzer dbms 테이블 요약 |
| variables | analyzer dbms 변수 |

## common/

| 파일 | 사용처 |
|------|--------|
| variable_summary | analyzer dbms |
