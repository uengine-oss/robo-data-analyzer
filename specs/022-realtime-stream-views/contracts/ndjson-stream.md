# Contract: NDJSON 분석 진행 스트림 (analyzer → frontend)

**계약 당사자**: analyzer `POST /robo/analyze`(생산) ↔ frontend `roboApi.analyze` switch(소비).

**본 기능의 계약 영향 = 형식 불변, 호출 시점만 확대.** 새 이벤트 타입 0. 다른 서비스(antlr·architect·catalog) 영향 0.

## 이벤트 타입 (현행 = 유지)

```
phase_event         {type, event: start|node_done|complete|paused|resumed|stopped, node?, run_id?}
node_event          {type, op:"merge", label, id, props}
relationship_event  {type, op:"merge", rel_type, from:{id, label?, rule_id?}, to:{id?|name?, label?, rule_id?}, props}
message             {type, content}
processing_event    {type, step, entity_kind, entity_id, sub_action, status}
tool_event / todo_update   (Agent 미들웨어 — 불변)
error / warning     {type, content}
stream_end          (종료 센티넬)
```

## 본 변경이 보장하는 불변식 (계약 안정성)

1. **이벤트 타입 집합 불변** — 위 목록에 추가/삭제 0. 골격 step은 기존 `node_event`/`relationship_event`/`message`를 **더 일찍** 보낼 뿐.
2. **wire 형식 불변** — `node_event`={op,label,id,props}, `relationship_event`={op,rel_type,from.id,to.id,props}. 프론트 `mergeStreamNode`/`mergeStreamRelationship` 시그니처 변경 0.
3. **id 일관** — 골격 emit 노드 id == step6 emit 노드 id(017 통일) → 프론트 병합(덮어쓰기), 중복/깜빡임 0.
4. **숨김 정책 일관** — 라이브로 오는 REFERENCES/NEXT/BRANCH/AFFECTS_TABLE/HAS_RULE/EXAMPLE/QUESTION는 프론트가 기존대로 미표시. 표시 골격관계(HAS_MEMBER/PARENT_OF/READS/WRITES/CALLS/HAS_COLUMN/BELONGS_TO/INIT_BY/FK)는 즉시 렌더.
5. **종료 정합** — `phase_event:complete` + `stream_end` 후 프론트가 `getGraphData` 1회 재조회(최종 정합). 라이브 누락분 보정(현행 유지).

## 소비측(프론트) 계약

- `phase_event` → 진행바·타임라인. `node_event`/`relationship_event` → nodeMap/linkMap reactive 병합 → 트리·그래프·카운트. `message` → 콘솔. `processing_event` → (옵션) 콘솔 보조.
- 카운트는 `graphData`(nodeMap/linkMap 숨김 제외) 기준.

## 검증

- 실데이터로 골격 emit 후 프론트 nodeMap/linkMap이 라이브 증가, 최종이 `getGraphData`와 정합. (quickstart.md SC 매핑)
