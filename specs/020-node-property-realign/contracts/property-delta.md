# Contract Delta: 노드 속성 (소비처 약속)

생산자(analyzer)가 Neo4j에 박는 **노드 속성**의 변경 — 소비처(frontend·catalog)가 맞춰야 할 계약.

## 소비처가 읽는 규칙 (after)
1. 노드는 **자기 종류에 의미있는 속성만** 가짐(빈값 키는 아예 없음). 소비처는 `key in props` 로 존재 확인 후 사용.
2. 부가 텍스트 = **`notes`** 단일(옛 `comment`·`annotations` 폐기).
3. 루틴 인터페이스 = **`params`·`return_type`**(옛 `signature` 폐기).
4. 위험 = **`risk_level`**(옛 `risk`), 순환 = **`in_call_cycle`**(옛 `in_cycle`) — 루틴에만. `loc`·`god_flag`·`table_coupling`·`fan_in`·`fan_out` **없음**(필요시 CALLS 관계로 파생).
5. 분석 인사이트 = Table/Column **`analyzed_description`**(옛 코드노드 `table_insights_json` 폐기).

## before → after (소비처 조치)

| 옛 | after | 소비처 조치 |
|---|---|---|
| `comment`·`annotations` | `notes` | NodeDetailBase: 표시 키 `notes`로 |
| `signature` | `params`·`return_type` | NodeDetailBase: signature 표시 제거, params/return 표시 |
| `risk` | `risk_level` | NvlGraph(건강색)·NodeDetailBase rename |
| `in_cycle` | `in_call_cycle` | NodeDetailBase rename |
| `loc`·`god_flag`·`table_coupling`·`fan_in`·`fan_out` | (제거) | NodeDetailBase 표시 제거 |
| `table_insights_json`(코드노드) | (제거) | 컬럼 `analyzed_description` 사용 |

## 영향 서비스
- **frontend**(전수 정합): `NodeDetailBase.vue`, `NvlGraph.vue`.
- **catalog**: `labels(n)`·`n{.*}` 무관통과 — 속성명 명시 참조만 grep 점검.
- **불변**: 017 id·018 관계·019 라벨. 프롬프트 byte-identical.
