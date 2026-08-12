## [TASK — 한 TABLE과 모든 허용 COLUMN의 최종 의미]

이 출력은 사람이 데이터 카탈로그에서 테이블·컬럼의 뜻을 읽는 최종 설명이 된다.
입력 evidence는 테이블 하나에 대한 원래 스키마 설명과 코드노드별 직접 사용 후보다.
아래 절은 각각 자기 절이 명시한 필드에만 적용된다.

목표는 각 TABLE/COLUMN마다 현재 근거가 입증하는 역할만 종합하고, 근거 부족은 빈 의미와
`insufficient`/`missing_context`로 명시하는 것이다. 물리 이름 번역이나 다른 컬럼 후보 복사는
결과가 아니다.

## [OUTPUT CONTRACT — wire 구조]

- TABLE 물리 좌표 `db`, `schema`, `name`과 `columns`는 최상위에 둔다.
- TABLE의 `logical_name`, `description`, `evidence_status`, `missing_context`는 최상위
  `meaning` 안에 둔다.
- 각 COLUMN은 `name`과 `meaning`만 가지며, 같은 네 의미 필드는 그 `meaning` 안에 둔다.
- `meaning` 밖에 의미 필드를 중복하지 않는다. 이 중첩은 상태 조합을 schema가 직접 강제하기
  위한 wire 구조이며 저장 시 같은 canonical TABLE/COLUMN 필드로 정규화된다.

## [INPUT GUIDE — 각 값의 뜻·권한·허용 판정]

- `table`은 exact `db/schema/name`과 DDL이 소유한 source logical name/description/comment/type이다.
  TABLE 좌표와 정식 source 의미의 최우선 근거다.
- `direct_operations`는 `source_id`→TABLE의 직접 READ/WRITE/DML과 절대 source 좌표를 잇는 구조
  근거다. TABLE이 실제 접근 대상인지와 접근 방향을 판정한다.
- `table_usages`는 code node가 바로 이 TABLE에 수행한 역할의 자연어 후보다. 후보의 table key와
  direct operation이 일치할 때만 종합하며 `CODE_NODE_USES_TABLE`의 source_id 소유 범위를 함수
  전체 책임으로 확대하지 않는다.
- 각 `columns[*]`는 DDL allowlist의 정확한 COLUMN이다. source 의미와 `candidates`는 그 COLUMN에만
  적용된다.
- `columns[*].candidates[*]`의 `source_id`, `ownership`, `evidence_lines`, `source_lines`는
  `CODE_NODE_USES_COLUMN`인 code node→TABLE→COLUMN→원문 좌표 관계다. COLUMN 자연어는 중간
  모델을 거치지 않고 이 관계의 원문에서 이번 최종 요청이 한 번만 생성한다.
- `source_lines[*].focus_text`의 `[TARGET_COLUMN]`은 현재 `columns[*].name`,
  `[OTHER_COLUMN]`은 같은 줄의 sibling DDL column을 뜻한다. 원본 파일의 절대 `line`은 감사
  좌표로 유지되며, sibling 이름은 현재 컬럼 의미로 재귀속하지 못하게 구조적으로 가린 것이다.
- sample이 있으면 실제 값의 형식·범위를 보조할 수 있지만 값 몇 개로 업무 정책·단위·코드 뜻을
  일반화하지 않는다.
- `[]`/`{}`/빈 source 필드는 해당 종류의 근거가 없다는 뜻이다. required TABLE/COLUMN 자체를
  누락하지 말고, 다른 근거도 충분하지 않을 때 `insufficient`로 판정한다.

## [DECISION PROCEDURE]

1. exact TABLE 좌표와 DDL COLUMN allowlist를 고정한다.
2. TABLE과 각 COLUMN을 따로 source metadata→direct usage→comment→sample 순으로 읽는다.
3. candidate의 source_id/table/column/좌표 관계가 현재 대상과 일치하는지 확인한다.
4. 입증된 최소 역할만 logical_name/description에 쓰고 각 대상의 sufficiency를 별도로 판정한다.
5. source와 후보가 충돌하면 우선순위를 적용하고, 근거 부족은 창작 대신 missing_context로 낸다.

### 소유권 — 모든 필드에 적용

- 테이블·컬럼마다 자기 근거만 종합하고 다른 테이블·컬럼의 책임을 가져오지 않는다.
- `table_usages[*].usage_summary`는 바로 이 테이블의 직접 사용 설명이며 함수 전체 summary가
  아니다. `direct_operations`는 결정론적으로 연결된 직접 READ/WRITE와 절대 소스 좌표다.
- 대상 테이블은 결정론 READS/WRITES로 실제 접근이 확인된 parsed DDL 테이블이다. 모든 소유
  컬럼은 출력하되, 컬럼 근거가 없으면 의미 필드를 빈 문자열로 둔다.

예시 — 사용 후보가 없는 테이블 (입력: `table_usages: []`, `source_description: "우편번호 기준정보"`):

- 잘못: 테이블과 컬럼을 출력에서 누락하거나, 첫 컬럼 설명을 테이블 설명으로 복사
- 좋음: `description: "우편번호 기준정보를 담는 테이블"` — 있는 근거가 말하는 만큼만

### 근거 우선순위 — 모든 필드에 적용

1. 정식 `source_description`
2. 현재 테이블·컬럼의 직접 사용으로 입증된 후보
3. 비권위 `source_comment`
4. 위 근거가 말하는 최소 역할

`source_comment`는 생성 provenance·운영 메모·낡은 설명일 수 있다. 활성 직접 사용과 충돌하면
버리고, `Source references`·`Operations`·`AMBIGUOUS_SOURCE` 같은 provenance/미확정 표식은
업무 의미로 복사하지 않는다. 물리 이름·업계 관례로 기관, 식별자 종류, 단위, 보호 방식,
상태 의미를 만들지 않는다.

예시 — 입력: `source_description: "횟수 초기화 제외 여부"`, 사용 후보 하나가 "실패 신청 제외 여부"라고 주장:

- 잘못: `description: "실패 신청 제외 여부"` — 하위 근거(후보)가 상위 근거(source)를 덮었다
- 좋음: `description: "횟수 초기화 제외 여부. 재신청 가능 판정에 사용"` — source 보존 + 입증된 맥락만 추가

예시 — 입력: `source_description: "암호화된 연락처 부분정보"`:

- 잘못: `description: "마스킹된 연락처"` — 익숙한 표현으로 바꾸며 보호 방식(암호화)과 범위(부분정보)가 소실됐다
- 좋음: `description: "암호화된 연락처 부분정보"` — 근거가 말하는 보호 방식과 범위를 그대로 유지

### 물리 좌표 — `db`, `schema`, `name`

입력 table의 exact 물리 좌표를 그대로 쓴다. table/column의 `source_logical_name`이 비어
있지 않으면 출력 `logical_name`에 그대로 쓰고, 정식 `source_description`이 비어 있지 않으면
출력 `description`에 그대로 쓴다. 이 두 값만 schema const다. 비권위 `source_comment`는
잠그지 않으며 직접 사용과 함께 의미를 판단한다.

### TABLE `description` / `logical_name`

이 절은 테이블의 `description`·`logical_name`에만 적용된다.

`description`은 이 테이블이 어떤 데이터를 담고 어떤 업무에서 직접 조회·저장되는지
사용자 친화적인 1~2문장으로 설명한다. 여러 사용 후보를 종합하되 한 함수가 함께 사용한
다른 테이블의 효과를 섞지 않는다.

`logical_name`은 무엇에 관한 데이터인지 구별되는 짧은 명사구다. 마스터·이력·임시·집계
성격은 근거에 있을 때만 붙인다. 근거가 부족하면 빈 문자열이며 물리명을 번역해 꾸미지 않는다.

예시 — 한 함수가 테이블 A를 읽고 테이블 B에 쓸 때, 지금 A를 설명 중:

- 잘못: A의 description에 B의 저장 대상·쓰기 결과를 넣음
- 좋음: A의 직접 SELECT 대상과 그 조회 결과의 현재 함수 내 용도만 서술

예시 — usage 후보가 "상태 조회에 사용"·"승인 여부 판정에 사용" 두 개:

- 잘못: 첫 문장 하나를 그대로 테이블 전체 설명으로 채택
- 좋음: `description: "요청의 처리 상태를 담는다. 상태 조회와 승인 여부 판정에 사용된다"` — 양립하는 사용을 종합

### COLUMN `logical_name` / `description`

이 절은 컬럼 필드에만 적용된다.

입력 허용 컬럼 이름을 정확히 한 번씩 반환한다. 새 컬럼 생성, 누락, 다른 컬럼 후보 복사를
하지 않는다. `logical_name`은 표시명, `description`은 저장 값의 의미와 현재 사용 역할이다.
DDL 타입이나 물리 컬럼명 자체를 설명으로 반복하지 않는다.
직접 사용·source 의미가 모두 없어 근거 있는 의미를 만들 수 없으면 해당 의미 필드는 빈
문자열로 둔다. required key를 빼지는 않으며, 물리명 번역이나 관례로 빈칸을 꾸미지 않는다.

각 컬럼의 `candidates`는 code node별 직접 사용 원문 근거다. `source_id`, `ownership`,
`evidence_lines`, `source_lines`는 결정론 좌표다. 현재 컬럼의 candidate만 사용하고 다른 컬럼·
다른 테이블의 동명 candidate를 복사하지 않는다. source line이 계산식·filter·join·group·쓰기에서
입증한 역할은 보존하되, 그 역할을 넘어 업무 용어를 이름에서 만들지 않는다.
한 candidate의 `focus_text`에 `[OTHER_COLUMN]`이 같이 나타나도 그 연산은 sibling의 근거가
아니다. `[TARGET_COLUMN]`이 그 식에서 직접 맡는 역할만 판정한다.

예시 — X candidate의 focus_text가
`[TARGET_COLUMN] * DECODE([OTHER_COLUMN], '+', 1, '-', -1)`이고 Y candidate에는 단순
projection 줄만 있을 때:

- 잘못: Y가 부호를 결정한다고 설명 — 그 계산 줄은 Y candidate가 소유하지 않는다.
- 좋음: X에는 입증된 곱셈 역할을 쓰고, Y는 projection 이상의 의미 근거가 없다고 판정한다.
`source_lines[*].source_id`가 candidate의 code node와 다르고 ownership이
`derived_output_unique`이면, 바깥 SELECT가 한 하위 SELECT의 해당 물리 컬럼 출력을 exact
identifier로 소비했다는 구조 def-use 근거다. 그 줄의 계산·filter 역할은 사용하되 하위 쿼리의
다른 컬럼으로 확장하지 않는다.

예시 — candidate가 한 컬럼을 조건 비교와 계산 부호 선택에 직접 사용한다고 입증한 경우:

- 잘못: 컬럼 이름을 번역한 표시명만 쓰거나, table 설명에만 역할을 넣고 컬럼 설명은 비움
- 좋음: source line이 입증한 조건 기준·계산 역할을 해당 컬럼 description에 보존

`source_description`이나 `source_comment`에 저장 대상의 중심 명사와 수식어가 있으면
`logical_name`과 `description` 모두 그 의미 경계를 보존한다. 물리명의 약어를 풀어
다른 대상·소유자·보호 방식으로 바꾸지 않는다. 자연스럽게 고쳐 쓰는 과정에서 뜻이
달라질 위험이 있으면 source의 업무 명사구를 그대로 재사용하는 것이 정답이다.

예시 — 입력: 컬럼 `ownr_ref`, `source_description: "소유자 계정번호"`:

- 잘못: `logical_name: "소유자 이메일"` — 이름 느낌으로 대상 종류를 좁혔다
- 좋음: `logical_name: "소유자 계정번호"` — source가 말한 범위 유지

예시 — 입력: 컬럼 `reg_dt`, `source_description: "등록 날짜"`:

- 잘못: `description: "등록 일시"` — 날짜를 일시로 확대했다
- 좋음: `description: "등록 날짜"` — source가 날짜면 날짜로 유지

예시 — 입력: 복합 물리명에 기관·채널 토큰이 보이나 `source_description: "고객 일련번호"`:

- 잘못: `logical_name: "기관 지점 일련번호"` — 물리명의 다른 토큰(사람/계정/기관/지점 축)을 따라 중심 명사와 수식어를 바꿨다
- 좋음: `logical_name: "고객 일련번호"` — source의 업무 명사구를 그대로 재사용하고, 직접 사용 근거가 입증한 맥락만 덧붙임

같은 원리로 번호/부분정보/일련번호, 원장등록/신청가능, 암호화/마스킹처럼 source가 구별한
축을 출력에서 바꾸지 않는다.

### 입력 충분성 — TABLE/COLUMN `evidence_status` / `missing_context`

각 테이블과 각 컬럼을 따로 판정한다.

- 제공 근거로 역할과 의미 경계를 확정했으면 `sufficient`, `missing_context: []`.
- 근거가 말하는 일부 역할만 쓸 수 있으면 `partial`로 두고, 확정하지 못한 원문·metadata·
  직접 사용 근거를 `missing_context`에 구체적으로 쓴다.
- 물리 이름·타입밖에 없어 의미를 만들 수 없으면 `insufficient`로 두고 logical_name과
  description을 빈 문자열로 유지하며 필요한 근거를 `missing_context`에 쓴다.

정식 `source_description`이 없다는 사실만으로 사용 근거를 폐기하지 않는다. exact source가
계산 부호 선택, 조건 비교, join/group/filter 기준처럼 다른 값과 구별되는 역할을 직접 입증하면
그 **입증된 역할 설명**은 sufficient일 수 있고 logical_name은 빈 문자열이어도 된다. 반대로 값을
그대로 projection/복사한 사실만 있고 저장 값의 역할을 구별할 연산이 없으면 업무 뜻을 만들지
말고 insufficient/needs_context로 둔다. 실제 입력 문자열에 손상 문자가 없는데 인코딩 문제를
missing_context로 만들지 않는다.

입력이 비어 있는데 required field를 채우기 위해 물리명을 번역하는 행동은 금지한다. 올바른
대체 행동은 빈 의미 + `insufficient` + 필요한 근거의 명시다.

## [SELF CHECK]

제출 직전 모든 컬럼을 하나씩 source와 역대조한다. source가 구별한 중심 개념(중심 명사와
수식어)이 출력에서 대체·확대·축소된 항목이 하나라도 있으면 고쳐서 제출한다. 후보가
source와 충돌하면 후보를 버리며 둘을 “또는”으로 합쳐 보존하지 않는다.

출력은 `db`, `schema`, `name`, TABLE `meaning`,
`columns:[{name, meaning}]`만 포함하며 각 `meaning`은
`logical_name`, `description`, `evidence_status`, `missing_context`만 포함한다.
