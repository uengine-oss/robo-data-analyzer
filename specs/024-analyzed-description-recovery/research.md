# Phase 0 Research: 식별자 정규화 + 분석설명 유실 복구

NEEDS CLARIFICATION 없음(근본·증거 실측 확정). 설계 결정:

## D1. 유실 근본 = 식별자 형식 불일치 + DRY 위반
- **결정/근거(로그+그래프 실측)**: TABLE.analyzed_description 6/16, COLUMN 61/153 유실. LLM은 성공(로그 done, 입력8605/출력86)했으나 step7 SET_ANALYZED_QUERY가 `MATCH (t {id})` 로 매칭하는데 노드 id(`.RWIS.X`, 3조각 빈 db 선두점)와 step7 write id/insights fqn(`RWIS.X`, 2조각)이 불일치 → 매칭 0건 → 조용한 no-op 유실. 우연히 두 형식 다 처리된 6개만 생존.
- **DRY 위반**: id 형식이 Table.id property(graph_nodes.py:78)·step7(117행)·insights fqn 등 ≥2곳에서 따로 조립 → 어긋날 수밖에.

## D2. 수정 방식 = A(표면정합) 기각, B(근본 정규화) 채택
- **A(step7만 노드 형식에 맞춤)**: 선두점 유지 → 클린성 결함 잔존 + 미래 또 어긋날 위험. 기각.
- **B(단일 fqn 헬퍼, 빈 구성요소 생략)**: `fqn(db, schema, name)` = 비어있지 않은 조각만 점으로 join → db 빈값이면 `RWIS.X`, 있으면 `mydb.RWIS.X`. Table.id·Column id·step7 write·insights fqn 원천 전부 이 헬퍼 위임. **DRY 복원 = 버그 제거 + 클린(선두점 0).** 채택(원칙 §3 탈바꿈).
- **기각 대안**: 빈 db에 기본값(예: "DEFAULT") 부여 = 없는 데이터 날조(왜곡, 헌법 위반).

## D3. 블래스트 안전성 (전수조사)
- `embed_step.py:145` `c.id.split(".")[parts[-2]]` = 음수 인덱스 → 선두 변경 안전.
- 외부 소비처(catalog/frontend/architect)는 id 불투명 read → 재분석이 새 id 생성, 신선 read(점위치 의존·재구성 없음). text2sql=별도 스키마 무관. architect ingestion=이미 깨짐(별개).
- → B 블래스트 관리가능. id 형식 변경은 재분석 산출물에 한정.

## D4. 동반 결함 동시 수정 (헌법 일관)
- **빈값미저장(spec 020)**: table/column MERGE_QUERY가 `nonempty()` 미적용 → 빈 db·comment 키 저장. MERGE 입력에 nonempty 적용. 단 coalesce(기존 보존) 의미 유지.
- **조용한 no-op 표면화**: `update_analyzed_descriptions`가 매칭 0건이어도 통과 → 갱신 결과 카운트 확인해 0이면 경고(이번 유실의 은폐자).

## D5. 검증 = 실제 재분석 ([[principles]]§6, 헌법 VI)
- 코드 수정 후 analyzer 재분석 1회 실행 → ① 코드가 R/W하는 테이블·컬럼 analyzed_description 빈 것 0(특히 RDD01DD_TB) ② 기존 채워진 6개·노드 식별 회귀0 ③ 선두점 id 0 ④ no-op 경고 동작. 그래프 직접쿼리 + 로그 대조(개수만 X).

## 미확정(구현 시 코드로 확정)
- insights table_fqn 정확한 원천(step4 링킹 reads/writes fqn 생성지점) — 거기도 fqn 헬퍼 적용해야 양끝 일치. 구현 첫 태스크에서 grep로 핀포인트.
