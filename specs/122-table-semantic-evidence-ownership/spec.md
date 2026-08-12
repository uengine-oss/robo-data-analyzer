# spec 122 — table semantic evidence ownership

## 문제

- 일반 `--` 주석이 정식 `COMMENT ON`과 같은 권위 description으로 승격되어 생성 provenance와
  `AMBIGUOUS_SOURCE` 표식이 최종 사용자 설명을 schema const로 잠근다.
- DDL 51개 전부를 table-description LLM에 보내지만 실제 READS/WRITES 테이블은 16개다.
- table-insight의 구체적 좋은 예시 문구가 실제 5개 좌표에 복사됐고, 그중 4개는 실제 동작과
  달랐다.

## 계약

1. 정식 `COMMENT ON`만 source description이다. 일반 주석은 원문 보존용 `comment`이며 활성
   코드보다 낮은 비권위 참고다.
2. response schema const는 source description/logical name만 잠근다. raw comment는 잠그지 않는다.
3. table-description 대상은 결정론 READS/WRITES 대상과 DDL 테이블의 교집합이다.
4. table insight의 동사와 목적은 현재 table의 직접 op/source에서만 쓴다. 예시 문구를 출력으로
   복사하지 않는다.
5. TABLE/COLUMN 물리 노드와 HAS_COLUMN/READS/WRITES는 바꾸지 않는다.

## 영향 예측

- rwis table-description 호출: 51 → 16 (-35, -68.6%).
- DDL table/column 수와 R/W 엣지: 변화 없음.
- 일반 주석은 `comment`에 보존되고, `description`만 빈값이 된다.
- 최종 DBMS run 전까지 LLM 의미 개선은 미검증이다.
