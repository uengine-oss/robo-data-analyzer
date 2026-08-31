## [TASK — 한 TABLE과 허용 COLUMN의 최종 의미]

입력은 한 물리 TABLE에 정확히 연결된 source 설명과 직접 코드 사용만 담는다. 각 대상의 근거가
입증하는 표시명과 설명을 쓰며 물리 이름 번역·업계 관례·다른 대상 의미로 빈칸을 꾸미지 않는다.
source의 업무 명사구를 그대로 재사용하고 중심 명사와 수식어를 보존한다. 사람/계정/기관/지점이나
원장등록/신청가능처럼 다른 대상·상태를 동의어로 합치지 않는다. 상반된 후보를 버리며 둘을 “또는”으로 합쳐
쓰지 않는다.

## [INPUT]

- `table.source_description`: TABLE의 source logical name·description·comment
- `table.observed_operations`: 확정된 직접 READ/WRITE/DML
- `columns` key: 출력이 그대로 보존할 물리 COLUMN 이름
- `columns.<name>.source_description`: 해당 COLUMN 자체의 source 의미
- `columns.<name>.usage_evidence`: 해당 COLUMN에 직접 귀속한 활성 원문

근거가 없는 종류는 key 자체가 없다. 다른 대상 값으로 보충하지 않는다.

## [OUTPUT CONTRACT]

최상위는 `logical_name`·`description`·`columns`, 각 COLUMN 값은 `logical_name`·`description`만 가진다.
`columns`는 입력 COLUMN key와 정확히 같아야 한다. logical_name은 근거의 중심 명사구인 짧은
표시명이고, description은 저장 값과 입증된 직접 사용 역할이다. TABLE description은 실제 읽기·변경을
1~2문장으로 종합한다. 근거가 의미를 입증하지 못하면 해당 문자열을 비운다. 물리 좌표·상태·근거·
누락 사유·내부 ID는 출력하지 않는다.

## [DECISION]

정식 source 설명 → 직접 사용 → 비권위 comment 순으로 판단한다. 양립하는 직접 사용만 종합하고
함수 전체 책임이나 함께 사용한 다른 TABLE 효과를 가져오지 않는다. 충돌하면 정식 source 의미를
보존하고 사용이 입증한 별도 맥락만 덧붙인다. literal·이름 모양으로 기관·식별자 종류·상태값·단위·
보안 방식을 추측하지 않는다.

예: 잘못 “신청 상태 또는 원장 등록 여부”, 좋음 “신청 가능 여부”처럼 source 의미를 보존한다.

## [SELF CHECK]

입력과 출력 COLUMN key가 같고, 각 문구가 해당 대상의 근거만 반영하는지 확인한다.
