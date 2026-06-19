# Phase 1 Data Model: 노드 속성 정체화 (before → after)

식별자=`id`(017)·라벨=antlr type(019)·관계(018) 유지. 본 스펙 = **노드 속성**만 정체화.

## 1. 코드 노드 속성 (종류별 — "빈값 미저장")

저장 규칙: 각 노드는 **비어있지 않은 키만** 가짐(`SET n += map`). 아래는 "그 종류가 값이 있을 때 갖는" 속성.

| 속성 | 공통(전 코드노드) | 루틴(METHOD/FUNCTION/PROCEDURE/TRIGGER) | 구문(SELECT/IF/…) | 그릇(FILE/CLASS) |
|---|:---:|:---:|:---:|:---:|
| id · name · file_path · start_line · end_line | ✅ | ✅ | ✅(name 없으면 미저장, 표시는 라벨[라인]) | ✅ |
| code_text | ✅ | ✅ | ✅ | —(파일=원문없음) |
| summary · stereotype · logical_name | ✅(분석된 노드) | ✅ | ✅ | ✅ |
| **notes**(=옛 comment+annotations) | 값 있으면 | 값 있으면 | 보통 없음 | 값 있으면 |
| **params · return_type** | — | ✅(있으면) | ✗ | ✗ |
| **risk_level · in_call_cycle** | — | ✅(루틴만) | ✗ | ✗ |

## 2. 속성 before → after

| 옛 속성 | after | 비고 |
|---|---|---|
| `signature` | **저장 미포함**(필드는 유지) | 루틴은 params·return_type 가 대체. ★필드는 call_linker·LLM이 써서 유지 |
| `annotations` | 저장 시 **`notes`로 통합**(필드 유지) | framework 전용→DBMS 빈값 문제 해소 |
| `comment` | 저장 시 **`notes`로 통합**(필드 유지) | 범용 부가텍스트. ★Module.comment/annotations 필드는 module_pre_analysis가 써서 유지 |
| `params` · `return_type` | 유지(루틴, 빈값 미저장) | granular |
| `loc` · `god_flag` · `table_coupling` | **폐기(저장)** | 위험계산엔 메모리로 씀 |
| `fan_in` · `fan_out` | **폐기(저장)** | CALLS 관계로 즉석 파생 |
| `risk` | **`risk_level`** (루틴만) | 이름 명확화·최종판정만 저장 |
| `in_cycle` | **`in_call_cycle`** (루틴만) | 이름 명확화 |
| `table_insights_json` | **폐기** | 인사이트는 Table/Column `analyzed_description`(enrich step)에 이미 분배 |
| `component_id` · `component_name` | 유지 | 그룹(영역보기)용, 변경 없음 |

## 3. 위험지표 (계산 vs 저장 분리)

```
health_metrics_step(계산, 메모리)
  loc · fan_in · fan_out · god · table_coupling · in_cycle   ← 전 신호로 계산(품질 불변)
        │ 종합
        ▼
저장(루틴 노드)  =  risk_level (+ in_call_cycle)   ← 최종 판정만. 중간지표 미저장
```

## 핵심 불변식 (검증 대상)

1. 구문 노드 속성에 framework/health 잡속성(annotations·params·return_type·signature·loc·god_flag·fan_in·fan_out·table_coupling) **0**.
2. 루틴 노드는 params·return_type(있으면)·risk_level·in_call_cycle 보유.
3. 부가텍스트는 `notes` 하나(annotations·comment 키 잔재 0).
4. `risk_level` 결과 = 변경 전 `risk`와 동일(위험도 품질 불변).
5. Table/Column `analyzed_description` 존재(table_insights_json 폐기 후 인사이트 유실 0).
6. 017 id·018 관계·019 라벨 불변. 프롬프트 byte-identical.
