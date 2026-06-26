# Phase 0 Research: 테이블 논리명 + 비즈니스 품질

NEEDS CLARIFICATION 없음. 결정:

## D1. 테이블 논리명 = 코드노드 패턴 확장
- **결정**: TableDescription(LLM 출력)에 logical_name 추가 → 테이블 설명 생성 시 논리명+문단 함께 산출. 신규 LLM 호출 없음(기존 table_description 단계에 필드 추가).
- **근거**: 코드노드는 이미 logical_name을 code_summary가 산출. 같은 방식 = DRY·결정론 유지.

## D2. 빈 논리명 미저장
- **결정**: 근거 부족 시 빈 logical_name → spec 024 `nonempty()`가 자동 미저장. 날조 금지(FR-003).

## D3. 요약 비즈니스화 = 규칙 불변, 예시·지침만
- **결정**: code_summary_batch.yaml의 **user_template_specific**(분할고유)만 손질 — 예시를 업무이벤트형으로 + "행/컬럼 기계어보다 업무이벤트 우선" 1줄. 규칙(값보존·날조금지)은 이미 있음 → 불변.
- **근거(KV캐시)**: 공통 `_common.yaml` system은 byte-identical 유지(3분할 prefix 캐시) — 분할고유 user_template만 바꾸면 캐시 안 깨짐(헌법 VIII).
- ★주의: 요약은 임베딩검색(별도 프로젝트) 입력 → 변경 시 검색 재측정(SC-005). 회귀 시 재고.

## D4. 프론트 표시
- **결정**: TableCard.vue가 logical_name 우선 표시 + 물리명 병기(Explore 조사: TableCard는 db/schema/name 속성 직접 사용 → logical_name 속성 추가 표시).

## D5. 검증 = 024와 묶어 1회 재분석
- **결정**: 024+025 코드 후 1회 재분석 → snapshot diff. 의도변화(논리명 추가·요약 문체) + 회귀0. 요약 품질은 샘플 원문대조(값/코드 보존·날조0).
