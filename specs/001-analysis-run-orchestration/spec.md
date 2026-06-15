# Feature Specification: Analysis Run Orchestration & NDJSON Streaming

**Feature Branch**: `001-analysis-run-orchestration`

**Created**: 2026-06-15

**Status**: Backfilled (reverse-engineered)

**Input**: 기존 구현 코드(main.py · api/analyze_router.py · shared/workflow/executor.py · run_registry.py · pause_controller.py · shared/log/ndjson_stream.py · cli_scripts/analyze_data_dir.py · data_models/analysis_run.py)에서 역공학으로 backfill 한 명세 — "이미 동작하는 것"을 문서화.

## User Scenarios & Testing *(mandatory)*

<!--
  IMPORTANT: User stories should be PRIORITIZED as user journeys ordered by importance.
  Each user story/journey must be INDEPENDENTLY TESTABLE - meaning if you implement just ONE of them,
  you should still have a viable MVP (Minimum Viable Product) that delivers value.
-->

### User Story 1 - 분석 1회 실행 + 진행 스트림 수신 (Priority: P1)

운영자/아키텍트가 레거시 소스 디렉토리를 지정해 분석을 시작하면, 즉시 long-lived NDJSON 스트림이 열리고 step 진행·그래프 노드/엣지 생성·도구 호출이 실시간으로 흘러나온다. downstream(robo-architect 그래프 뷰어)은 같은 스트림을 소비해 그래프가 채워지는 것을 본다.

**Why this priority**: 이 한 가지만으로 제품이 성립한다 — 분석을 시작하고 결과(그래프 + 진행)를 받는 것이 핵심 가치. 제어·중단이 없어도 운영자는 1회 분석을 완수하고 산출물을 얻는다.

**Independent Test**: `POST /robo/analyze`(또는 CLI `analyze_data_dir.py`)로 한 프로젝트를 분석 → 스트림이 `phase_event:start` 로 시작해 `stream_end` 로 닫히고, 중간에 `node_event`/`relationship_event` 가 흐르며, 종료 후 `artifacts/` 4파일 + `usage_summary.json` 이 생성되는지로 단독 검증.

**Acceptance Scenarios**:

1. **Given** 유효한 `project_root` 와 `strategy`, **When** `POST /robo/analyze` 호출, **Then** HTTP 200 + `Content-Type: application/x-ndjson` 스트림이 즉시 반환되고 워크플로우는 백그라운드 task 로 진행된다.
2. **Given** 분석이 진행 중, **When** 스트림을 읽으면, **Then** `phase_event`(start/node_done/complete) · `node_event` · `relationship_event` · `tool_event` 가 한 줄(NDJSON)씩 push 되고 마지막에 `stream_end` 한 줄로 닫힌다.
3. **Given** 분석이 정상 완료, **When** 종료 후 `run_dir`(`logs/analysis.run/latest/`)를 보면, **Then** `artifacts/` 산출물 + `usage_summary.json` 이 존재한다.
4. **Given** `ROBO_LLM_API_KEY` 가 비어 있음, **When** `POST /robo/analyze` 호출, **Then** HTTP 401("ROBO_LLM_API_KEY missing")로 거부된다.

---

### User Story 2 - 진행 중 분석 일시정지/재개/중단 (Priority: P2)

운영자가 진행 중인 분석을 화면을 보며 멈추거나(비용/오판 우려), 다시 이어가거나, 깔끔히 끝낼 수 있다. 제어 신호는 활성 run 에 전달되고, 상태 전이는 같은 NDJSON 스트림으로도 표면화된다.

**Why this priority**: 운영 안전장치 — 사람이 개입할 수 있어야 잘못된 분석을 막는다. 단 P1 없이는 무의미하므로 P2.

**Independent Test**: 분석이 진행 중일 때 `POST /robo/analyze/control {action:"pause"}` → 응답 `{success, status:{isPaused:true}}` + 스트림에 `phase_event:paused` 출현, `resume` → `phase_event:resumed`, `stop` → run 이 정상 종료(실패 아님)되는지로 검증.

**Acceptance Scenarios**:

1. **Given** 활성 분석 존재, **When** `control {action:"pause"}`, **Then** `{success:true, action:"pause", status:{isPaused:true, isStopped:false}}` 반환 + 다음 step 경계에서 hold + 스트림에 `phase_event:paused`.
2. **Given** paused 상태, **When** `control {action:"resume"}`, **Then** hold 가 풀려 진행 재개 + `phase_event:resumed`.
3. **Given** 활성 분석 존재, **When** `control {action:"stop"}`, **Then** run 이 사용자 의도 종료로 끝나(`phase_event:stopped`) 실패로 기록되지 않는다.
4. **Given** 활성 분석 없음, **When** 어떤 control 호출이든, **Then** HTTP 409("no active analysis")로 거부된다.

---

### User Story 3 - 매 run 깨끗한 출발 보장 (Priority: P3)

운영자/테스터는 매 분석이 옛 run 의 잔재 없이 깨끗한 상태에서 시작하길 기대한다 — 그래프와 로그가 직전 run 으로 오염되지 않아야 결과를 신뢰할 수 있다.

**Why this priority**: 정확성/재현성 기반이지만, 분석 자체(P1)가 동작한 뒤에야 가치가 드러나므로 P3.

**Independent Test**: 직전 run 이 노드/파일을 남긴 상태에서 새 분석을 시작 → 그래프가 비워진 채(직전 노드 0) 시작하고 `logs/` 가 통째 재생성되는지로 검증.

**Acceptance Scenarios**:

1. **Given** 직전 run 의 노드가 Neo4j 에 남아있음, **When** 새 run 시작, **Then** 그래프가 `DETACH DELETE` 로 wipe 된 뒤 스키마(제약/인덱스)가 idempotent 하게 재확인된다.
2. **Given** 직전 run 의 로그/산출물이 `logs/` 에 있음, **When** 새 run 시작, **Then** `logs/` 가 통째 rmtree 후 재생성되어 새 run 산출물만 남는다.

---

### Edge Cases

- `project_root` 가 존재하지 않으면? → HTTP 400("project_root not found").
- 알 수 없는 `strategy` 면? → HTTP 400(`get_pipeline` ValueError 전파).
- `project_root` 미전달(null)이면? → 업로드 모드로 간주해 `ROBO_DATA_DIR/source` 를 기본 대상으로 사용.
- 직전 run 이 정리되기 전 새 run 이 시작되면? → 활성 슬롯을 덮어쓰고 `warning` 으로 표면화(동시 분석 1개 모델).
- 클라이언트가 스트림을 중간에 끊으면? → 그 run 은 종료된다(별도 폴링/재개 통로 없음).
- 누적 LLM 비용이 `ROBO_BUDGET_RUN_USD` 임계를 넘으면? → 회로 차단기가 발동해 파이프라인이 자동 중단되고 `error` 로 emit 된다.
- 분석 도중 step 이 실패하면? → 조용히 끝나지 않고 `error` 이벤트가 명시 emit 된 뒤 스트림이 닫힌다(silent fail 금지).
- 서버를 재시작하면 진행 중이던 run 은? → 끝난다. 디스크 기반 재개는 없다(소프트 hold 범위).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: 시스템은 `POST /robo/analyze` 로 분석 1회 실행을 시작하고 즉시 `application/x-ndjson` long-lived 스트림을 반환해야 한다(워크플로우는 백그라운드 진행).
- **FR-002**: 시스템은 CLI 진입점(`analyze_data_dir.py`)으로도 동일한 1회 실행을 수행하고 같은 NDJSON 이벤트를 콘솔로 출력해야 한다.
- **FR-003**: 매 `/robo/analyze` 호출은 독립 run 이어야 하며, run 은 `run_id`(`YYYYMMDD_HHMMSS`)로 유일하게 식별되어야 한다.
- **FR-004**: 진행 상태는 NDJSON 스트림 단일 채널로만 push 되어야 한다 — 진행 조회용 폴링 엔드포인트는 제공하지 않는다.
- **FR-005**: 스트림은 `phase_event` · `tool_event` · `todo_update` · `processing_event` · `node_event` · `relationship_event` · `error` · `warning` 이벤트를 흘리고, 마지막에 반드시 `stream_end` 한 줄로 닫혀야 한다(성공·실패·중단 무관).
- **FR-006**: 시스템은 `POST /robo/analyze/control` 로 진행 중 분석에 `pause` / `resume` / `stop` 신호를 전달할 수 있어야 하며, 응답은 `{success, action, status:{isPaused, isStopped}}` 형태여야 한다.
- **FR-007**: 제어 payload 에는 run_id 가 없고, 신호는 단일 활성 run 에 전달된다("동시 분석 1개" 모델). 활성 run 이 없으면 control 은 HTTP 409 로 거부되어야 한다.
- **FR-008**: 일시정지는 step 경계(및 step6 라운드 루프)의 안전 지점에서만 적용되는 soft hold 여야 하고, stop 은 run 을 정상 종료(실패 아님)로 마무리해야 한다.
- **FR-009**: pause/resume/stop 상태 전이는 NDJSON 스트림에 `phase_event`(paused/resumed/stopped)로도 표면화되어야 한다.
- **FR-010**: 일시정지/재개/중단은 프로세스가 살아있는 동안만 유효한 hold 여야 하며, 디스크 기반 재개(서버 재시작 후 이어받기)는 제공하지 않는다.
- **FR-011**: 매 run 시작 시 시스템은 Neo4j 그래프를 `DETACH DELETE` 로 wipe 한 뒤 스키마(제약/인덱스)를 idempotent 하게 재확인해야 한다.
- **FR-012**: 매 run 시작 시 시스템은 로그 루트(`ROBO_LOG_DIR`)를 통째 rmtree 후 재생성해야 하며, 산출물 디렉토리는 항상 `logs/analysis.run/latest/`(timestamp 폴더 없음)여야 한다.
- **FR-013**: run 정상 완료 시 시스템은 누적 상태 산출물을 `artifacts/` 에 dump 하고 `usage.jsonl` 을 집계한 `usage_summary.json` 을 생성해야 한다.
- **FR-014**: 누적 LLM 비용이 `ROBO_BUDGET_RUN_USD` 임계(기본 20.0 USD)를 초과하면 per-run 회로 차단기가 발동해 파이프라인을 자동 중단해야 한다.
- **FR-015**: 모든 step/run 실패는 조용히 끝나지 않고 `error` 이벤트로 명시 emit 된 뒤 전파되어야 한다(silent fail 금지).
- **FR-016**: `ROBO_LLM_API_KEY` 가 없으면 분석 시작은 HTTP 401 로 거부되어야 한다.
- **FR-017**: 잘못된 입력(존재하지 않는 `project_root`, 알 수 없는 `strategy`)은 HTTP 400 으로 거부되어야 한다.
- **FR-018**: 요청 헤더 `X-Neo4j-*`(Electron 임베드)가 있으면 그 run 에 한해 Neo4j 연결을 override 하고, 없으면 env 설정으로 폴백해야 한다.
- **FR-019**: 모든 NDJSON 이벤트는 emit 시점에 `analyzer.log` 에도 한 줄 mirror 되어야 한다(클라이언트 연결이 없는 CLID 단독 실행에서도 흔적 보존).

### Key Entities *(include if feature involves data)*

- **Run (AnalysisRun)**: 분석 1회 실행 메타. 속성 = `run_id`(`YYYYMMDD_HHMMSS`) · `started_at`(UTC) · 대상 `Codebase` 참조 · `run_dir`(항상 `logs/analysis.run/latest/`).
- **Active Run Slot (run_registry)**: 단일 활성 run 슬롯 — `(run_id, 컨트롤러)` 하나만 보유. 새 run 이 직전 슬롯을 덮어쓰며(동시 1개), 자기 run 일 때만 비운다.
- **PauseController**: pause/resume/stop 신호의 set(control 엔드포인트)와 소비(run 태스크 `gate()`)를 중개. `status()` = `{isPaused, isStopped}`. `RunStopped` = 정상 종료 sentinel.
- **NDJSONStream**: 진행 이벤트 단일 채널(unbounded queue). `emit()` push, `iter()` yield, `close()` 시 `stream_end` 투입.
- **NDJSON 이벤트 타입**: `phase_event`(step start/node_done/paused/resumed/stopped/complete) · `tool_event` · `todo_update` · `processing_event` · `node_event`(그래프 노드 merge) · `relationship_event`(그래프 엣지 merge) · `error` · `warning` · `stream_end`(close 신호).
- **CostBudget**: per-run 누적 비용 추적기 + 회로 차단기. `limit_usd`(=`ROBO_BUDGET_RUN_USD`) 초과 시 `BudgetExceededError`.
- **Artifacts**: run 산출물 — `artifacts/*.json`(ast/linking/analysis_contexts/analysis/graph_extras) + `usage.jsonl` + `usage_summary.json`(+ `health_report.json`).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 유효한 분석 요청은 워크플로우 완료를 기다리지 않고 진행 스트림을 즉시(첫 바이트 1초 이내) 받기 시작한다.
- **SC-002**: 모든 분석 run 은 시작/실패/중단과 무관하게 단 1개의 `stream_end` 로 종료되어 consumer 가 무한 대기(hang)하지 않는다.
- **SC-003**: 활성 분석이 있을 때 control 요청 100%가 다음 안전 지점 안에서 반영되고, 활성 분석이 없으면 100% 409 로 거부된다.
- **SC-004**: 매 run 종료 시점에 직전 run 의 그래프 노드 잔재가 0개이고, `logs/analysis.run/latest/` 에 이번 run 의 산출물만 존재한다.
- **SC-005**: 단일 run 의 누적 LLM 비용이 설정 임계를 초과하는 일은 발생하지 않는다(초과 즉시 자동 중단).
- **SC-006**: 어떤 step 도 실패를 조용히 삼키지 않는다 — 모든 실패는 스트림의 `error` 이벤트로 관측 가능하다.

## Assumptions

- 사용자 = 분석을 실행하는 운영자/아키텍트 + 같은 스트림을 소비하는 downstream robo-architect 그래프 뷰어.
- 동시 분석은 1개만 운영한다고 가정한다(control payload 에 run_id 가 없는 단일 슬롯 모델). 다중 동시 run 은 범위 밖.
- 진행 추적은 NDJSON push 단일 채널만 쓴다고 가정한다 — 폴링/조회 API 는 의도적으로 없다.
- run 의 영속성은 메모리(프로세스 수명) 범위로 가정한다 — 서버 재시작 후 디스크에서 이어받는 재개는 범위 밖.
- Neo4j 인스턴스가 가용하고 매 run wipe 가 허용되는 테스트/분석 정책이라고 가정한다(파괴적 `DETACH DELETE`).
- API key 의 단일 진실은 `.env` 의 `ROBO_LLM_API_KEY` 이며 요청별 override 는 지원하지 않는다(import 시점 frozen).
- 외부 antlr AST · catalog · LLM 엔드포인트 등 파이프라인 내부 단계는 기존 시스템을 재사용한다고 가정한다(본 명세의 범위는 오케스트레이션/스트리밍/제어 계층).