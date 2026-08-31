## [TASK — 한 TABLE과 허용 COLUMN의 최종 의미]

입력은 Analyzer가 한 물리 TABLE에 정확히 연결한 source 설명과 실제 코드 사용만 담는다.
각 대상의 근거가 직접 입증하는 표시명과 설명을 작성한다. 물리 이름 번역, 업계 관례,
다른 테이블·컬럼의 의미, 주석에만 남은 동작으로 빈칸을 꾸미지 않는다.

표시명은 source의 중심 명사와 수식어를 보존한다. source의 업무 명사구를 그대로 재사용할 수 있으면 새 동의어로 바꾸지 않는다. 특히 사람/계정/기관/지점처럼 서로 다른 대상을 하나로 치환하지 않고, 원장등록/신청가능처럼 서로 다른 상태·행위를 같은 뜻으로 합치지 않는다. 상반된 후보가 있으면 한 후보를 버리며 둘을 “또는”으로 합쳐 모호한 이름을 만들지 않는다.

## [INPUT]

- `table.source_description`: DDL·Catalog가 제공한 `source_logical_name`,
  `source_description`, `source_comment`를 출처 이름과 함께 보존한 값이다.
- `table.observed_operations`: Analyzer가 실제 코드에서 확정한 직접 READ/WRITE/DML 조합이다.
- `table.usage_meanings`: 함수별 TABLE 사용 의미 중 이 TABLE에 직접 귀속된 값이다.
- `columns`의 key: 출력에서도 정확히 한 번 보존해야 하는 허용 물리 COLUMN 이름이다.
- `columns.<name>.source_description`: 그 COLUMN 자체가 소유한 source 의미다.
- `columns.<name>.usage_evidence`: Analyzer가 그 COLUMN에 직접 귀속한 원문이다.
  `ownership`은 귀속 방식, `source`는 절대 줄과 해당 줄의 활성 원문이다.
- 없는 종류의 근거는 key 자체가 없다. 다른 대상의 값으로 보충하지 않는다.

## [OUTPUT CONTRACT]

최상위에는 `logical_name`, `description`, `columns`만 출력한다.

- TABLE `logical_name`: 무엇에 관한 데이터인지 구별되는 짧은 명사구다.
- TABLE `description`: 어떤 데이터를 담고 실제 업무에서 어떻게 읽거나 변경하는지 1~2문장으로
  종합한다.
- `columns`는 입력 COLUMN key를 누락·추가 없이 정확히 한 번씩 갖는다.
- 각 COLUMN 값에는 `logical_name`, `description`만 출력한다.
- COLUMN `logical_name`: 근거가 입증한 사용자 표시명이다.
- COLUMN `description`: 저장 값의 의미와 실제 사용 역할이다.
- 각 값은 문자열 하나다. 근거가 의미를 입증하지 못하면 빈 문자열로 둔다.
- db/schema/table/column 물리 좌표, 상태, 근거, 누락 사유, 내부 ID를 응답에 다시 출력하지 않는다.

## [DECISION]

1. TABLE과 각 COLUMN을 서로 다른 의미 소유자로 고정한다.
2. 각 대상에서 정식 source 설명, 직접 사용, 비권위 comment 순으로 읽는다.
3. 서로 양립하는 직접 사용은 종합하되 함수 전체 책임이나 함께 사용한 다른 TABLE의 효과를
   가져오지 않는다.
4. source와 사용이 충돌하면 정식 source 의미를 보존하고, 실제 사용이 입증한 맥락만 덧붙인다.
5. 물리 이름·literal 모양만으로 기관, 식별자 종류, 상태값 뜻, 단위, 보안 방식을 추측하지 않는다.

예: TABLE A를 읽고 TABLE B에 쓰는 함수에서 A를 설명할 때 B의 저장 결과를 A에 넣지 않는다.
예: source가 `암호화된 연락처 부분정보`이면 익숙하다는 이유로 `마스킹된 연락처`로 바꾸지 않는다.
예: COLUMN source와 직접 사용이 모두 없으면 물리명을 번역하지 않고 두 의미 값을 비운다.
잘못: source의 `고객 원장등록 상태`와 코드의 `신청가능 여부`를 `원장등록 또는 신청가능 상태`로 합친다.
좋음: 정식 source의 중심 명사구를 보존하고, 직접 사용이 입증한 별도 역할만 description에 덧붙인다.

## [SELF CHECK]

- 입력 COLUMN key와 출력 COLUMN key가 정확히 같은가
- 모든 TABLE/COLUMN에 두 문자열이 정확히 한 번 있는가
- 각 값이 자기 대상의 source·직접 사용에만 근거하는가
- 입력에 없는 의미나 내부 메타데이터를 만들거나 복창하지 않았는가
