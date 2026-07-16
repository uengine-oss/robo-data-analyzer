# Contract: 폴백·파이프라인 processing 스트림 이벤트

**소유**: `robo-data-analyzer` (spec 054)
**소비자**: `robo-data-frontend` (spec 055, `analyzer.ts`의 `applyProcessingEvent`)
**기반**: `shared/log/processing_progress.py`의 기존 스키마 재사용 — 새 이벤트 타입을 만들지 않는다.

## 기존 이벤트 스키마 (재사용)

```json
{"type":"processing_event","step":"step6.analyze_code",
 "entity_kind":"CodeNode","entity_id":"<id>","sub_action":"<detail>","status":"start|done|failed"}
```

`track_processing(entity_kind, entity_id, sub_action)` async 컨텍스트가 start→done(정상) 또는
start→failed+re-raise(예외)를 emit한다. 조용한 반환 경로 없음(원칙 IV).

## 폴백 이벤트 (신규 `entity_kind="Fragment"` + CodeNode sub_action)

| 단계 | entity_kind | entity_id | sub_action | status |
|---|---|---|---|---|
| 품질검사 결과 | CodeNode | 함수 id | `rules_quality` | done(정상) / `anomaly`는 done+detail |
| 폴백 시작 | Fragment | 함수 id | `fallback` | start |
| 분할 완료 | Fragment | 함수 id | `split` | done (detail: 조각 수 n, 라인 범위) |
| 조각 분석 | Fragment | `<함수id>#frag<k>` | `analyze` | start / done / failed |
| 병합 | Fragment | 함수 id | `merge` | start / done / failed |
| 재검사 | CodeNode | 함수 id | `rules_recheck` | done / failed |
| 최종 경로 요약 | CodeNode | 함수 id | `rules_path` | done (detail: `normal` / `fallback_ok` / `fallback_anomaly`) |

- 정상 경로(폴백 0회): `rules_quality done` + `rules_path done(normal)`만 나온다(Fragment 이벤트 없음).
- 실패(조각/병합/재검사): 해당 단계 `failed` + `log_error` + 이상 플래그. run은 죽이지 않되 조용한 성공
  금지(FR-023, FR-026).

## 프론트 소비 주의 (spec 055)

- `analyzer.ts::applyProcessingEvent`는 `entity_kind`별 dedup(`processedIds`)과
  `PROGRESS_THROTTLE[rawKind] ?? 5` throttle을 이미 적용한다. `Fragment`는 함수당 조각 수가 유한하므로
  throttle 기본값(5)로 충분. 필요 시 `PROGRESS_THROTTLE["Fragment"]=1`로 조각 진행을 촘촘히 보이게 조정
  가능(프론트 단독 변경).
- 서버측 다건 loop(색인·샘플 fetch 등)는 Analyzer emit 지점에서도 N개마다 1회로 throttle(FR-028) — 프론트
  throttle과 이중 방어.

## 파이프라인 보강 이벤트 (coverage matrix 대상, FR-027)

`research.md`의 coverage matrix에서 "보강필요"로 표기된 구간에 대해 위 스키마로 start/done/failed를
추가한다. 구간별 `step`/`entity_kind`/`sub_action` 매핑은 matrix 표에 확정한다(예: 색인 생성 →
`step7.embed` / `Index` / `create`·`await_online`·`fingerprint`).
