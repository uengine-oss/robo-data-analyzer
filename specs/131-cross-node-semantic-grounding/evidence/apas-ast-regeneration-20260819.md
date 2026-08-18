# APAS 최신 AST 재생성·전수 delta — 2026-08-19

## 판정

최신 parser `4b849f14575b6830b82ae5ee980658100c4d8692`로 APAS 원문 23개를 다시 파싱했다.
원문은 바뀌지 않았고, 구 AST의 기존 노드 20,878개는 삭제·속성 변경 없이 모두 보존됐다.
신규 노드는 parser가 현재 계약으로 보존하는 `LABEL` 67개와 문장 단위 postfix update
`ASSIGNMENT` 2개뿐이다. active GOTO 5개는 같은 routine의 active LABEL 정확히 1개에 모두
해소됐다.

## 입력과 실행 경계

- corpus: `D:\work\robo\분석대상모음\framework\c\apascrt00010t02_20260406`
- source: 23개
- 구 analysis: 23개, 2026-08-07 생성본
- parser: `main` / `4b849f1`, dirty 0
- endpoint: `POST http://127.0.0.1:8401/antlr/parsing`, body `{}`
- `ROBO_DATA_DIR`은 위 corpus를 가리켰고 `PARSER_REPAIR_APPLY_TO_SOURCE=false`였다.
- parser가 소유한 `analysis/`, `diagnostics/`, `repairs/`만 갱신했다. source write-back은 0건이다.

실제 stream 최종값:

```text
quality-summary: exact=23 recovered=0 partial=0 reviewRequired=0
                 unresolved=0 failed=0 astFiles=23 lines=44143
complete: status=COMPLETED percent=100
```

## 원문과 AST 전후 전수 대조

| 축 | 구 AST | 신 AST | 판정 |
|---|---:|---:|---|
| source file/hash/bytes mismatch | 0 | 0 | 원문 23개 불변 |
| AST files | 23 | 23 | file set 불변 |
| total nodes | 20,878 | 20,947 | +69 |
| stable node removed/changed | — | 0 | 기존 type/name/line/target/expression/operator 전부 보존 |
| `LABEL` | 0 | 67 | source named label 67/67, missing 0, extra 0 |
| `ASSIGNMENT` | 2,035 | 2,037 | `size++`, `tmp_data++` 두 문장을 `+= 1`로 보존 |
| 그 밖의 type 17종 | 동일 | 동일 | vocabulary/count delta 0 |
| GOTO | 5 | 5 | 기존 좌표 불변 |

변하지 않은 type/count:

```text
BREAK 348, CASE 756, CONSTANT_FIELD 1, DEFINE 182, ELSE 763, FILE 23,
FUNCTION 416, FUNCTION_CALL 12112, GLOBAL_VARIABLE 147, GOTO 5, IF 1920,
INCLUDE 450, LOOP 15, MEMBER 108, RETURN 1444, STRUCT 6, SWITCH 147
```

AST 파일 21개는 위 69개 노드 추가 때문에 바뀌었고, `zngmmapi10020.json`과
`zngmmpus00010.json` 두 파일은 byte hash까지 그대로다. LABEL 67개는 모두 해당 source 절대행의
`<name>:`과 일치하며 routine 밖 LABEL과 같은 routine/name 중복은 각각 0이다.

추가 ASSIGNMENT 두 건:

```text
zngmmapi10000.c:227  size++;      -> target=size,     operator=+=, expression=1
zngmmapi10000.c:228  tmp_data++;  -> target=tmp_data, operator=+=, expression=1
```

이는 같은 parser commit의 범용 C 계약이다. standalone postfix update만 상태 변경 문장으로
보존하고 for header·condition·argument·더 큰 expression 내부 update는 별도 ASSIGNMENT로 만들지 않는다.

## GOTO→LABEL exact 해소

| file/routine | GOTO line | target | LABEL line | count |
|---|---:|---|---:|---:|
| `zapamcom10100.c:b000_main_proc` | 305 | `PFM_CATCH` | 380 | 1 |
| 같은 routine | 321 | `PFM_CATCH` | 380 | 1 |
| 같은 routine | 335 | `PFM_CATCH` | 380 | 1 |
| `zapamcom10101.c:b000_main_proc` | 182 | `PFM_CATCH` | 232 | 1 |
| 같은 routine | 201 | `PFM_CATCH` | 232 | 1 |

Analyzer 공용 preflight 실제 결과:

```text
[static-jump] routines 416 · resolved GOTO 5 · target LABEL 2 · missing/duplicate 0
[의무] 4810 행 · 효과 증거 보유 4202 (87%)
complement 580/648 · continuation 727/727 · decision 1997/2537
entry 368/368 · exit 463/463 · handler 67/67
```

`target LABEL 2`는 GOTO가 실제 참조하는 서로 다른 두 routine의 target label 수다. corpus 전체
LABEL 67개와 집계 단위가 다르다.

## 회귀와 환경 복구

```text
.\mvnw.cmd test
Tests run: 154, Failures: 0, Errors: 0, Skipped: 8
BUILD SUCCESS

.venv\Scripts\python.exe scripts\evidence_preflight.py measure --data-dir <APAS>
exit 0 · static jump missing/duplicate 0
```

검증용 parser 8401만 종료했다. 기존 Analyzer 15502와 Neo4j 7687 listener는 유지했다.
구·신 대조 manifest는 최종 정리 전까지 `%TEMP%\robo-apas-ast-{old,new}-20260819.json`에만 둔다.
