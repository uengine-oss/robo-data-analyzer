# Phase 0 Research: Data-Folder-Only Consumer

분기 제거를 어떻게 단일 진실로 수렴시킬지 결정. 각 항목: Decision / Rationale / Alternatives.

## R1. 입력원 단일화 — Codebase가 단일 진실

**Decision**: `Codebase`의 기본값을 고정한다 — `root = ENV.data.dir/source`, `ddl_dir = ENV.data.dir/ddl`. `/robo/analyze`의 `root_str = project_root or data/source` 삼항식을 제거하고 항상 `data/source`. 모든 step은 `project.root`/`project.ddl_dir`만 참조(이미 그러함) → 기본값 고정만으로 전 파이프라인이 `data/`로 수렴.

**Rationale**: 헌법 III(단일 진실)·II(분기 0). 한 곳(`Codebase` 기본값)만 정하면 step들의 분기가 자동 소멸 — 코드 이동 없이 탈바꿈. 레거시(경로 모드 이전)가 정확히 이 형태였다(회귀).

**Alternatives**: *각 step에서 개별 수정* — 분기를 여러 곳에서 손봐 누락 위험. 기각(단일 진실 위배).

## R2. DDL 단계가 data/ddl만 읽게 (크래시 근본 해결)

**Decision**: `parse_ddl_step`의 `ddl_dir = project.ddl_dir or project.root`에서 `or project.root` 폴백을 제거 → `project.ddl_dir`(=`data/ddl`)만 rglob. antlr 006이 `data/ddl`에 표 정의만 담으므로 프로시저가 sqlglot에 닿지 않음.

**Rationale**: 현재 크래시(함수 .sql이 sqlglot에 투입)의 직접 원인이 "root 전체 rglob"였다. `data/ddl`만 읽으면 근본 해소. antlr 006(US2)에 의존.

**Alternatives**: *analyzer에 resilience(skip 불가파일)* — 헌법 IV(no silent failure)와 충돌, 그리고 입구가 더러운 채 남음. 근본 아님 — 기각(격리는 상류 antlr 006이 담당).

## R3. scan·linking의 project_root 전파 제거

**Decision**: `scan_codebase_step`/`scanner_input_collector`/`scan_agent`/`link_deterministic_step`이 `project.root`(이제 `data/source` 고정)를 그대로 쓰게 두고, "경로 모드 vs data" 특수 분기·로그만 제거. 동작 경로는 단일.

**Rationale**: 이 step들은 이미 `project.root` 기준으로 동작 — R1로 root가 `data/source`로 고정되면 추가 변경 최소. 모드 분기·메시지만 정리(헌법 II).

**Alternatives**: 없음(자연 귀결).

## R4. 역호환 — 옛 클라이언트의 project_root

**Decision**: `AnalyzeRequest.project_root` 필드는 **수용하되 무력화**(소스를 바꾸지 않음). 데스크톱/프론트가 전환되기 전까지 옛 요청이 오류 없이 동작. 추후 필드 제거는 별도.

**Rationale**: 프론트(스펙 014)·데스크톱 전환과 디커플링. 깨지지 않는 점진 이행.

**Alternatives**: *즉시 필드 제거* — 옛 클라이언트 400. 기각(이행 마찰).

## 결론 — NEEDS CLARIFICATION 0
의존: **antlr 006**(`data/ddl` 청결 보장)이 먼저/함께. Phase 1 진행 가능.
