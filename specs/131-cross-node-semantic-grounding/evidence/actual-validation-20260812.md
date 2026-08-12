# Actual validation: dynamic semantic frames

검증일: 2026-08-12

## 판정

Framework와 DBMS의 실제 Analyzer 실행, 격리 Neo4j 저장, 기본 MCP projection, Architect
Simplified/Detailed 소비를 끝까지 검증했다. 검증 표본에서 fabrication과 contradiction은 각각 0건이며,
모든 필수 slot은 근거와 상태를 가졌다. 이 판정은 아래의 작은 실제 표본에 한정하며 다른 corpus 전체로
일반화하지 않는다.

## 격리 조건

- 작업 root: `D:\work\robo\_runs\semantic-frame-20260812-actual`
- Framework DB/run: `semframefwfix20260812` / `20260812_120338_1bc324a22d80`
- DBMS DB/run: `semframedbfinal20260812` / `20260812_120541_684ac64e5e92`
- Architect DB: `semframearch20260812`
- Analyzer MCP: 격리 포트 `16502`; Architect: 격리 포트 `18501`
- cache와 response cache는 사용하지 않았다. 두 Analyzer run 모두 response cache hit 0이다.
- 기존 기본 DB와 기본 서비스는 변경하지 않았다. `accept`와 `apply`는 모든 실행에서 0회다.

## Analyzer 실제 실행

Framework 표본 `order_guard.c`는 MODULE과 FUNCTION frame 2개를 저장했다. FUNCTION에는 12개 slot이
있었고 `sufficient`인데 의미가 빈 slot은 0개였다. membership 검사, 수량 검사, `reserve_stock` 호출과
분기 결과가 원문 구조와 일치했다. finding은 0건이고 run quality는 `completed`였다.

DBMS 표본 `order_status.sql`과 DDL은 MODULE, FUNCTION, 자식 control/data node, TABLE,
COLUMN 2개를 포함한 frame 11개를 저장했다. `sufficient`인데 의미가 빈 slot은 0개였다. 다음 의미를
원문과 직접 대조했다.

- `ORDER_STATUS`를 `ORDER_ID`로 조회하고 `STATUS_CD`를 결과 변수에 둔다.
- `STATUS_CD = 'CANCELLED'`이면 `STOPPED`를 반환하고 종료한다.
- 그 외에는 조회한 상태를 그대로 반환한다.
- `NO_DATA_FOUND`는 `UNKNOWN`을 반환하며 상위 호출자에게 전파하지 않는다.
- EXCEPTION frame은 failure, handling, propagation slot을 모두 가진다.

finding은 0건이고 run quality는 `completed`였다. 이후 해당 DB의 7개 대상을 모두 1,024차원으로
재임베딩했고 vector index가 `ONLINE`임을 확인했다.

기본 MCP `node_detail`은 `semantic-frame-packet/v1`을 반환했다. target, profile, slots와 연결된
data object를 ID 기반으로 전달하며 complete source body/hash나 새 source excerpt 필드는 포함하지
않았다.

## Architect 실제 실행

| 경로 | Proposal | 최종 영향 단계 | 결과 | frame packet | Command / GWT |
|---|---|---:|---|---:|---:|
| Simplified Intent→Plan | `PRO-002` | Plan 283.560초 | 성공, 미확정 draft | 4개, ID 중복 0 | 1 / 3 |
| Detailed DISCOVER→DECOMPOSE→STRATEGIZE→CONNECT→DEFINE→TACTICAL | `PRO-003` | Tactical 237.088초 | 성공, consolidate 완료 | 4개, ID 중복 0 | 1 / 3 |

두 경로는 FUNCTION, SELECT, IF, EXCEPTION frame을 재사용했다. `gwt_evidence_ref_errors`,
`ungrounded_gwt_values`, `tactical_evidence_ref_errors`를 최종 저장 결과에 다시 적용한 결과 모두
오류 0건이었다. 세 GWT는 정상 상태 반환, `CANCELLED → STOPPED`, `NO_DATA_FOUND → UNKNOWN`을
각각 정확한 RULE/STATEMENT/SYMBOL/DATA_OBJECT evidence ID에 연결했다. 패킷을 재귀 검사했을 때
`raw_code`, `source_excerpt`, `complete_source`, `source_hash`, `source_code`, `body` 필드는 0개였다.

## 실행에서 발견해 수정한 결함

1. 부모 계획이 자식 소유 RULE slot까지 필수로 요구해 실제 frame population이 실패했다. Framework는
   함수가 소유한 RULE만 명시해 fan-out하고 DBMS root는 할당된 행만 계획하도록 정정했다.
2. EXCEPTION propagation이 `direct_terminates`를 반영하지 않아 비전파 의미를 빠뜨렸다. 소유 statement
   범위와 실제 종료 정보를 사용하도록 정정했다.
3. Architect content-ref 해석이 RULE의 실제 frame owner를 `parentId`에 보존한 뒤 `nodeId`만 검사해
   정확한 근거를 거부했다. evidence ID의 권위 owner를 기준으로 좌표를 정규화하고 두 좌표를 검증한다.
4. Detailed 모델이 정확한 evidence ID와 root node ID를 함께 제시한 경우 source owner가 어긋났다.
   evidence index가 소유자를 결정하도록 정규화하되 업무 의미나 값을 추론하지 않게 했다.

## 3층 검증

1. 자동 회귀: Analyzer `1064 passed, 5 skipped, 378 subtests`; Architect
   `1375 passed, 2 skipped, 3 warnings`.
2. 결정론 대조: frame population/owner/completeness, source-free packet, dedupe, exact evidence refs,
   scalar grounding 검사를 모두 통과했다.
3. 사람 의미 판정: 실제 C/SQL, 저장 frame, 최종 Simplified/Detailed GWT를 나란히 읽어 누락·창작·모순을
   판정했다. 표본의 목표 의미는 6/6 일치, fabrication 0, contradiction 0이다.

## 검증 당시 위치와 정리 상태

아래 경로와 `semframe*20260812` 격리 DB 6개는 검증 증거를 이 문서에 확정하고 커밋한 뒤
2026-08-12에 정리했다. 실행 폴더는 Windows 휴지통으로 이동했으며, 기본 DB와 기본 서비스는
건드리지 않았다.

- Framework run: `D:\work\robo\_runs\semantic-frame-20260812-actual\framework-runs-fixed\20260812_120338_1bc324a22d80`
- DBMS run: `D:\work\robo\_runs\semantic-frame-20260812-actual\dbms-runs-final\20260812_120541_684ac64e5e92`
- Simplified final: `D:\work\robo\_runs\semantic-frame-20260812-actual\architect-runs\semantic-frame-simplified-final-plan-fixed`
- Detailed final: `D:\work\robo\_runs\semantic-frame-20260812-actual\architect-runs\semantic-frame-detailed-final-tactical-fixed`
