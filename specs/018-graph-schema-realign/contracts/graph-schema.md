# Contract: 출력 그래프 스키마 (소비처 약속)

**생산자**: robo-data-analyzer · **소비처**: frontend(NVL/네비게이터), catalog(read), architect(ingestion).
constitution Cross-Service Contract = "↓ Output 그래프 스키마". 본 변경은 이 contract 를 바꾸므로 **세 소비처 동시 정합 필수**.

## 소비처가 의존해도 되는 것 (안정 보장)

1. **노드 식별** = `레이블 + node.id` (id 단일, 불변).
2. **종류 구분** = **노드 라벨**(UPPER_SNAKE). 관계명으로 종류 구분하지 말 것.
3. **"무엇을 담나"** = `HAS_MEMBER` 하나로 조회 → 멤버 종류는 라벨 필터(PROCEDURE/FUNCTION/VARIABLE/CONSTANT…).
4. **"어디 속하나"** = `BELONGS_TO`.
5. **"실행 순서"**(DBMS) = `NEXT`(이전→다음 구문). framework 엔 없음(정상).
6. **"중첩"** = `PARENT_OF`. (HAS_MEMBER 와 구분 — 멤버 소유 ≠ AST 중첩.)
7. **구조 노드**(DECLARE/TRY/TRIGGER_BLOCK)는 그래프에 **존재**하며 summary/rules 가 비어도 "분석 누락"이 아니라 "구조". 표시는 종류 한글 라벨 + "구조".

## 마이그레이션 (옛 → 새) — 소비처 정합 표

| 옛 관계명 | 새 관계명 | 소비처 조치 |
|---|---|---|
| `HAS_FUNCTION` | `HAS_MEMBER` | + 라벨이 루틴(PROCEDURE/FUNCTION/METHOD/TRIGGER)인 것만 |
| `HAS_VARIABLE` | `HAS_MEMBER` | + 라벨이 데이터(VARIABLE/CONSTANT/FIELD/PARAMETER)인 것만 |
| `BELONGS_TO_PACKAGE` | `BELONGS_TO` | 그대로(끝점은 FOLDER/PACKAGE) |
| (구문에 달리던 `HAS_FUNCTION`) | (제거) | 구문은 `PARENT_OF`/`NEXT` 로 순회 |

**옛 관계명 잔재 0** — 정합 후 전 소비처 전수 grep 으로 확인(`HAS_FUNCTION`/`HAS_VARIABLE`/`BELONGS_TO_PACKAGE` 0건).

## 영향 소비처 (정합 대상)
- **frontend**: `nodeRelationships.ts`(getRelationshipGroups/getAttachedByType), `navigator.ts`(moduleTree), `node-detail/{ModuleCard,PackageCard,FileCard,DatatypeCard}.vue`, 기타 색/표시.
- **catalog**: `schema_query_service.py`(Cypher).
- **architect**: `ingestion/.../{rule_context,agent_validator}.py`.
- **architect 내 analyzer 사본**: 서브모듈 bump 로 동기화.

## 비변경 (불변 — 회귀 0 기준)
기존 보기 동작·드래그·캔버스·CALLS/READS/WRITES/FK/EM 관계·id 식별·매 run wipe 정책은 변경 없음.
