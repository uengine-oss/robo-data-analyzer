# Contract: stereotype 라벨 — 위치기반 소비 계약

> 생산자(analyzer) ↔ 소비자(frontend·architect·catalog) 간 그래프 라벨 약속. 이 계약 변경은 소비처 알람 대상.

## 노드 라벨 순서 계약

- **`labels[0]` = 노드의 antlr 타입(종류)** — FILE / CLASS / INTERFACE / FUNCTION / METHOD / PROCEDURE / SELECT / IF / TABLE / COLUMN …
- **`labels[1:]` = stereotype(역할)** — Service / Command / Query / Adapter / Validator / DataMutator … (있을 때만).
- 외부 stub: `labels[0]` ∈ {FUNCTION, MODULE}, `labels[1:]` ⊇ {EXTERNAL}. (EXTERNAL은 stereotype 아님 — 종류미상 fallback.)

## 소비처 규칙 (필수)

- 종류 = `labels[0]`. 역할 = `labels[1:]`. **위치로 분리** — 하드코딩 stereotype 집합으로 분류하지 말 것(드리프트 0).
- 색상/필터/노드패널은 이 위치 규칙을 따른다.

## 보장 근거 & caveat

- 보장: analyzer가 타입 라벨을 step2(load_ast)에서, stereotype 라벨을 step6/step7에서 등록 → 토큰 생성순으로 타입 먼저.
- caveat: Neo4j는 라벨 순서를 계약으로 보장하지 않음(토큰 id 순). 이 파이프라인은 안 깨지나, 만약 순서가 어긋나면 최악 노드패널 1회 swap(데이터/로직 무손상). 소비처는 이 경미 리스크를 수용.

## 무중단 (이번 단계)

- stereotype **속성**(`node.properties.stereotype`)도 유지 — architect `module_retriever`·기존 프론트 패널이 아직 읽음. 소비처 위치기반 이관 후 속성 제거(후속 spec).
