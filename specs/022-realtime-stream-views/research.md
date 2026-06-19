# Research: 실시간 스트림 + 일관 보기모드

전수조사로 근본원인은 이미 확정. 남은 설계 미해결점만 결정.

## D1. 골격 step이 자기 델타를 어떻게 emit하나
- **Decision**: 각 골격 step이 노드/관계를 state·Neo4j에 넣은 직후, 공통 헬퍼 `emit_graph_delta(stream, nodes, edges)`를 호출. stream은 `step_config(config).get("stream")`로 접근(executor가 `config.configurable.stream` 주입 — step6에서 검증됨).
- **Rationale**: 비즈니스 함수 시그니처에 stream을 chain하지 않고, step 경계 1곳에서만 emit(응집). step6의 기존 패턴과 동일.
- **Alternatives**: executor가 state 델타를 보고 emit → 노드/관계 wire 변환을 executor가 알아야 해 책임 역전. 기각.

## D2. 노드/관계 dataclass → wire 형식 일반 매핑
- **Decision**: 헬퍼가 노드는 `{op:"merge", label: node.label, id: node.id, props: <빈값 제외 표시속성>}`, 관계는 `{op:"merge", rel_type, from:{id}, to:{id}, props}`로 변환. step6 `_emit_code_graph`의 형식을 그대로 따른다. step6도 이 헬퍼를 호출하도록 리팩터(DRY).
- **Rationale**: 프론트 `mergeStreamNode`/`mergeStreamRelationship`이 label/id/props·rel_type/from.id/to.id를 그대로 소비(계약 불변).
- **주의**: 골격 노드 id는 step6가 나중에 emit할 노드 id와 **동일**(017 id 통일) → 프론트 nodeMap이 덮어써 병합(깜빡임·중복 0). 골격이 먼저(구조), step6가 summary/logical_name 보강.
- **Table 끝점**: 골격 READS/WRITES from/to는 **id**(step1이 만든 TABLE id, catalog payload가 Start/End Node ID로 줌)로 emit. step6의 AFFECTS_TABLE만 name 사용(EM 흐름, 그대로 둠).

## D3. 콘솔 message
- **Decision**: 각 step 완료 시 `stream.emit({type:"message", content:"✅ <단계> 완료 — <요약 카운트>"})`. 프론트 `logToConsole`이 이미 `message` 렌더 → 추가 프론트 작업 없이 콘솔 실시간.
- **옵션**: `processing_event`(step 내 처리단위)는 이미 백엔드에 인프라 존재(`processing_progress.py`)하나 프론트 미렌더. 더 세밀한 라이브감이 필요하면 프론트 switch에 `processing_event` 핸들러 1줄 추가(콘솔 보조). 1차엔 message로 충분.
- **Rationale**: 최소 변경으로 SC-002 충족. message는 검증된 경로(antlr 파싱 메시지가 이미 이 경로로 보였음).

## D4. NVL 레이아웃 (보기모드 빈화면 D)
- **Decision**: `NvlGraph.vue` `NVL_OPTIONS`를 프로토타입(`doc/영역보기-NVL위에-D3오버레이-reference.html`)의 동작과 정합 — 대량 그래프 주입 시 forceDirected가 실제로 노드를 펼치도록 `physics.enabled=false`·`updateLayoutOnChange:false` 등 배치 차단 옵션 제거/완화.
- **Rationale**: 프로토타입은 기본 `{layout:'forceDirected'}`로 25노드급을 정상 배치. 앱은 "1-hop 드래그-드롭 소규모 전용"으로 튜닝돼 배치를 꺼서 대량 주입이 깨짐.
- **회귀 방지**: 드래그-드롭 1-hop 확장 시 노드가 과도하게 드리프트하지 않는지 라이브 확인. 필요 시 "bulk 주입(preset/setCanvasNodes)일 때만 풀 레이아웃 1회" 분기. 단 분기는 표시 동작이지 전략 분기 아님(원칙 II 무관).
- **헐 동기**: `useGroupHulls.ts`는 이미 프로토타입과 동일 world→screen 변환. 레이아웃이 애니메이션되면 `onLayoutStep`이 헐을 따라 그림 → 오히려 동기 개선. 정착 중 끊김 있으면 연속 rAF 추종 보강.
- **확정 방법**: 코드 수정 후 **라이브 자가검증**(Playwright)으로 모듈간 25노드 펼침 + 영역 헐 + 드래그-드롭 무회귀 확인. (정적으로 100% 단정 불가 → 라이브 1회로 확정.)

## D5. 카운트 (B)
- **Decision**: `GraphTab.vue:graphStats`를 `projectStore.graphData`(=nodeMap/linkMap 숨김 제외) 기준 `nodes.length`/`links.length`로 교체. 진행 중에도 graphData는 reactive라 라이브 증가.
- **Rationale**: 숨김(RULE/EXAMPLE/QUESTION·HIDDEN 관계) 제외·중복 제거가 graphData에 이미 적용 → 화면과 100% 일치(SC-004).

## D6. 전략 일관성 (framework vs dbms)
- **Confirmed(코드)**: 두 파이프라인 모두 `build_components_step`(component_id/component_name)·`health_metrics_step`(risk_level) 거침 → 영역/건강 보기 같은 키. 모듈간은 타입집합(CONTAINER/CALLABLE)+PARENT_OF로 전략 무관. → 보기모드 로직 1벌, 내용만 다름(추가 분기 불필요).
- **링킹 차이**: framework=scan+name+residual / dbms=dbms_tables+ast. 골격 emit은 각 링킹 step이 자기 산출 관계를 헬퍼로 emit하므로 전략 무관 일관(헬퍼 1벌).
- **검증**: framework 샘플 1회 재분석으로 영역/건강/모듈간이 dbms와 동일 동작하는지 라이브 확인.

## 미해결/리스크
- NVL 레이아웃 옵션 정확 조합은 라이브 튜닝 필요(정적 단정 불가) — implement에서 1회 확정.
- gateway↔catalog 인프라 500은 **범위 밖**이나 라이브검증 선결 조건 → 검증 전 복구.
