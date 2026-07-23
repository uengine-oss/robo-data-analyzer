# 096 — 후처리를 step7 과 같은 큐에 겹쳐 빈 구간을 채운다

작성: 2026-07-24. 3차 리서치 항목 7 의 설계를 실측 의존관계로 확정한 것.

## 1. 무엇이 문제인가 — 실측

기준선 콜드런(741.2초, `logs/analyzer.log` 재구성, 0.5초 격자) 시간축:

| 구간 | offset | 길이 | 상태 |
|---|---|---:|---|
| 결정론 적재 | +0 .. +5 | 5초 | LLM 0 |
| Scan Agent | +5 .. +29 | 24초 | in-flight ≈1 |
| 결정론 링킹 | +29 .. +32 | 3초 | LLM 0 |
| 잔여 패턴 루프 | +32 .. +85 | 53초 | in-flight ≈1 |
| link_external / context / module_pre | +85 .. +93 | 8초 | in-flight ≤23 |
| **step7 포화** | +93 .. +592 | 499초 | 정상 |
| **step7 스트래글러** | +592 .. +692 | 100초 | **in-flight 1** |
| **후처리 꼬리** | +692 .. +741 | 49초 | step7 완전 배수 후 별도 국면 |

- 서버-초(Σ 콜 latency) 31,387 · 평균 동시 42.4/64 · 유휴 34% · 포화 산술하한 490초.
- `in-flight<8` 지속 **207.5초(벽시계의 28.0%)**. 종전 기록 117초는 헤드 90초를 빼고 센 값이다.
- +592 이후 100초는 **단 한 콜**이 만든다: `code_rules_examples` 1건이 284.3초를 쓰고
  `LengthFinishReasonError` 로 실패했다(그 노드의 rules 는 빈 값이 됐다 — §6 참조).

**따라서 후처리 441콜(1,915 서버-초)이 채울 빈 슬롯은 꼬리 49초가 아니라 100초 구간이다.**

## 2. 왜 지금은 안 겹치는가 — 배리어의 정체

LangGraph 는 노드 간 배리어가 기본이다. 현재 선언(`pipeline/strategies.py`):

```
analyze_code_step  →  summarize_module_step  →  (table_desc, components, health, glossary)  →  embed
```

후처리는 step7 **노드 전체**가 끝나야 시작한다. 그런데 실제로 후처리가 읽는 값은
step7 의 **일부 분할**에서만 나온다. 원문 대조 결과:

| 후처리 | LLM 콜 | 실제로 읽는 것 | 나오는 분할 |
|---|---:|---|---|
| `summarize_module_step` | 23 | `fn.signature` · `fn.summary` · `fn.logical_name` | **#1 summary** |
| `build_components_step` | 19 | `fn.logical_name` · `fn.summary` · `calls_edges`(step5) | **#1 summary** |
| `build_glossary_step` | 392 | `fn.domain_terms` · `fn.summary` · `fn.search_text` + 모듈요약 | **#1 summary** → 모듈요약 |
| `enrich_table_description_step` | 7 | `fn.extra["table_insights"]` · `fn.summary` | #1 + #2 (§4 참조) |
| `health_metrics_step` | 0 | 그래프·결정론 | 전 분할 |
| `embed_step` | 0 | Neo4j 전체 | 전 분할 |

**세 개(434콜 중 434-7=434… 실제 434콜 = 23+19+392)가 #1 summary 하나에만 의존한다.**
그리고 #1 은 세 분할 중 가장 짧다(p50 17.8초 vs rules 30.0 · insights 49.2). 기준선에서
마지막 #1 은 **+592**, 마지막 노드 완료는 **+692** 였다.

즉 배리어는 **의존이 아니라 그래프 구조가 만든 것**이다. 이건 spec 082(CALLS 의존을
callee summary 확정 시점에 조기 해제)와 같은 종류의 문제를 한 계층 위에서 다시 만난 것이다.

## 3. 계약

### 3-1. `AnalysisHandoff` — run 스코프 인계 채널

`pipeline/execution/analysis_handoff.py`. run_executor 가 1개 만들어 config 에 주입한다.

| 멤버 | 계약 |
|---|---|
| `slots: PrioritySemaphore` | **run 전체가 공유하는 단 하나의 LLM 슬롯 세마포어.** 값은 종전과 같은 `max_concurrency`. |
| `expect(ids)` | step7 이 분석 대상 id 집합을 등록. |
| `publish_summary(fn)` | 한 노드의 #1 확정 직후 호출. 그 `CodeNode` 객체를 인계 목록에 싣는다. |
| `resolve(id)` | 노드 처리 완료(성공·실패·스킵 무관). 미공개 노드를 대기에서 뺀다. |
| `await summaries()` | 전 대상의 #1 이 확정(또는 확정 불가로 판명)될 때까지 대기 → 공개된 `CodeNode` 목록. |
| `await analysis()` | step7 노드 전체 완료까지 대기. |
| `publish_modules(list)` / `await modules()` | 모듈 요약 → 용어사전 인계. |
| `merge(code_nodes)` | state 의 코드노드 목록에서 분석된 노드를 **공개된 객체로 치환**한 목록. |
| `abort(exc)` / `close()` | 모든 게이트를 연다. 대기자는 `exc` 를 받거나 그 시점 값으로 진행. |

**교착 금지**: 생산자(step7 · summarize_module)는 `finally` 에서 반드시 `close()`/`abort()`
한다. 게이트가 안 열리면 superstep 이 영원히 안 끝난다 — 조용한 교착은 즉시 표면화한다.

### 3-2. `PrioritySemaphore` — 총량 고정 우선순위 슬롯

`shared/concurrency/priority_semaphore.py`.

- `async with sem:` → priority 0 (기존 `asyncio.Semaphore` 드롭인).
- `async with sem.at(p):` → 명시 우선순위. 대기자는 `(priority, 도착순)` 오름차순으로 깨운다.
- **총 in-flight 는 값 하나로 통제된다.** 후처리에 별도 세마포어를 추가하지 않는다
  (§01-2 `+28%` · §01-4 의 재판 금지).

우선순위: **step7 노드 = 0**, **후처리 = 1**. step7 이 슬롯을 채우는 동안 후처리는 대기하고,
step7 이 못 채우는 순간에만 빈 슬롯을 기회적으로 점유한다.

> 슬롯 회계 주의: step7 은 **노드당** 1슬롯을 잡고 그 안에서 1~2콜을 발사한다(종전과 동일).
> 후처리는 **콜당** 1슬롯이다. 따라서 서버로 가는 동시 요청의 **최대치는 종전과 같고**,
> 후처리는 step7 이 노드로 못 채운 잔여 슬롯만 쓴다. 총량은 늘지 않는다.
>
> 후처리 콜이 슬롯을 쥔 채 step7 노드를 막을 수 있으나, 후처리 콜의 p50 은 4.5~6.2초로
> step7 콜(17.8~49.2초)보다 짧아 head-of-line 지연 상한이 작다.

### 3-3. 그래프 선언 — 배리어 제거

```
(analyze_code_step, summarize_module_step, build_components_step,
 enrich_table_description_step, health_metrics_step, build_glossary_step),
embed_step,
```

한 superstep 의 병렬 그룹이며, 각 후처리 step 이 **자기 게이트를 스스로 선언**한다
(오케스트레이터 없음 — 빌더는 여전히 strategy 를 모른다).

| step | 게이트 |
|---|---|
| `summarize_module_step` | `await handoff.summaries()` |
| `build_components_step` | `await handoff.summaries()` |
| `build_glossary_step` | `await handoff.modules()` |
| `enrich_table_description_step` | `await handoff.analysis()` |
| `health_metrics_step` | `await handoff.analysis()` |
| `enrich_lineage_from_catalog_step`(DBMS) | 게이트 없음 — step7 과 무관한 HTTP |

state 키 충돌 없음: 그룹 안에서 `analysis_contexts`/EM 9종(step7), `modules`(요약),
`tables`·`columns`(테이블설명)를 각각 한 step 만 반환한다.

## 4. 산출물 불변을 어떻게 보장하나

**이 변경은 "언제 시작하는가"만 바꾼다. 입력값 집합은 바뀌지 않는다.**

1. `analyze_code_three_split` 은 `summary_out` 확정 직후 `fn.summary` ·`stereotype` ·
   `logical_name` · `search_text` · `domain_terms` 를 노드에 반영한다. 종전엔 같은 값을
   fan-in 지점에서 반영했다 — **값이 같고 시점만 앞당겨진다.** 이 다섯 필드는 #2·#3 이
   건드리지 않는다.
2. 후처리는 **전 대상 노드**의 #1 이 확정된 뒤에만 시작한다. 부분 집합으로 시작하지 않는다.
   따라서 프롬프트 입력(용어 우주·별칭 후보·모듈 멤버 목록)이 종전과 동일하다.
3. `table_insights` 는 `constrain_tables_to_rw` 가 rules 와 함께 정리하므로 노드 완료
   시점에만 확정된다. 그래서 테이블설명은 `analysis()` 게이트다 — 7콜뿐이라 손해가 없다.
4. 실패·스킵 노드는 `resolve()` 로 대기에서 빠진다. 종전에도 그 노드는 summary 가 없었다.

## 5. 판정 기준

1차 지표(벽시계 아님 — 같은 코드가 741~767초로 흔들린다):

| 지표 | 기준선 | 목표 |
|---|---:|---|
| `in-flight<8` 지속 | 207.5초 | 감소 |
| 서버-초(Σ) | 31,387 | 불변(±3%) |
| 유휴 % | 34% | 감소 |
| step7 콜당 지연 p50 (rules/summary/insights) | 30.0 / 17.8 / 49.2초 | **+20% 이상 오르면 기각**(§01-2·4 재판) |
| 결정론 6종 | 4,484 · 439 · 70 · 15 · 651 · 7,209 | **정확 일치** |
| 완주 | 416/416 | **정확 일치** |

## 5-1. 실측 결과 (콜드 1회 + 웜 1회)

`.llm-cache` 비우고 `scripts/analyze_data_dir.py --strategy framework`.

| 지표 | 기준선 741.2초 | 096 650.9초 |
|---|---:|---:|
| 벽시계 | 741 | **651** |
| 서버-초(Σ) | 31,387 | 31,438 (+0.2% — 불변) |
| 평균 동시 | 42.4/64 | **48.3/64** |
| 유휴 | 34% | **25%** |
| `in-flight<8` 지속 | 207.5초(28.0%) | **123.5초(19.0%)** |
| step7 소요 | 599.2초 | 511.4초 |
| rules p50 / summary p50 / insights p50 | 30.0 / 17.8 / 49.2 | 30.2 / 18.3 / 47.0 |

**겹치기가 실제로 일어났다는 직접 증거** (run 시작 기준 offset):

| 후처리 | 기준선 시작 | 096 시작 |
|---|---:|---:|
| module_summary | +692 | **+587** |
| component_naming | +704 | **+586** |
| table_description | +703.5 | **+607** |
| glossary | +704 | **+613** |

step7 의 마지막 rules 콜은 +607 에 끝났다 — 모듈요약·컴포넌트명이 step7 이 **아직 돌고 있는
동안** 시작했다. 배리어는 사라졌다.

**−90초를 전부 이 변경의 공으로 돌리면 안 된다.** step7 자체가 599.2→511.4초로 줄었는데
이 변경은 step7 을 빠르게 만들지 않는다(후처리는 낮은 우선순위라 step7 을 느리게만 할 수 있다).
차이의 정체는 기준선에 있던 **284.3초 스트래글러가 이 run 엔 없었던 것**이다(§6). 따라서:

- **이 변경에 귀속되는 것**: 후처리 꼬리 49초 → 44초, 그리고 무엇보다 **스트래글러가 생겼을 때
  후처리가 그 뒤에 줄서지 않는다**는 성질. 기준선 조건(마지막 #1 이후 step7 이 100초 더 감)에서는
  후처리 441콜이 통째로 그 100초 안으로 들어간다.
- **이 변경에 귀속되지 않는 것**: step7 의 −87.8초.

**왜 꼬리가 44초나 남았나 — 구조적 상한을 계측했다.** 후처리 441콜 중 392콜(용어사전)은
`전 노드 #1 → 모듈요약 → 용어사전` 사슬 끝에 있다. 용어 우주와 별칭 후보가 전 노드의
`domain_terms` 를 요구해서 부분 시작이 불가능하다(하면 프롬프트가 달라진다). 이 run 에서
마지막 #1(+586)과 step7 종료(+607) 간격이 21초뿐이라 사슬이 밖으로 튀어나왔다.
**즉 이 처방의 이득은 "마지막 #1 이후 step7 이 얼마나 더 도는가"에 비례한다.**

**결정론 검증(웜런 119.2초 — LLM 변동 제거)**: 6종 **전부 정확 일치**
(4,484 · 439 · 70 · 15 · 651 · 7,209), 완주 416/416, RULE 892 · EXAMPLE 1,765.

콜드런의 `요약 보유` 는 438 이었다. 빠진 하나는 코드노드가 아니라 모듈
`code:zapamcom10070.c` 이고, 원인은 **spec 080 반복 감지기가 그 module_summary 호출을
차단한 것**이다(`period=70 chars, chars_seen=3328`). 감지기가 설계대로 작동했고 fail 정책이
원본 모듈을 보존했다. 웜런에서 같은 호출이 성공해 439 로 복귀했으므로 이 변경과 무관한
LLM 변동이다 — 숨기지 않고 여기 적는다.

## 6. 이 슬라이스에서 다루지 않는 것 (발견은 기록한다)

- **스트래글러**: `code_rules_examples` 1콜이 284.3초 뒤 `LengthFinishReasonError` 로
  실패했고 그 노드의 rules 는 빈 값으로 남았다. spec 054 의 조각 폴백은 "파싱은 됐으나
  품질 이상" 경로에만 걸려 있어 **길이 초과 실패에는 복구가 없다.** 성능(빈 구간 100초)과
  품질(규칙 유실) 양쪽 결함이지만 이 슬라이스 범위 밖이다. 별도 spec 으로 다룬다.
- **헤드 88초**(scan 24 + 잔여 53): 잔여 루프는 미해결 케이스의 R/W 를 만들 수 있어
  step7 전체가 그 결과를 기다린다. 데이터로 도출한 부분 겹치기가 가능한지는 별도 조사.
