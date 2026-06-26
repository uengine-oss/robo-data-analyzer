# Phase 0 Research: 컬럼 논리명

근거 = 현행 코드(테이블 logical_name=spec 025 경로) 전수확인. NEEDS CLARIFICATION 0.

## D1. 테이블 logical_name 경로 미러 (새 메커니즘 발명 X)
- **Decision**: 컬럼 논리명을 **테이블 logical_name(025)과 동일 경로**로 산출·운반·저장.
- **근거**: `data_models/code_analysis_output.py` — 테이블은 `TableInsightItem.logical_name`(L44 인근) 있음, 컬럼 `ColumnInsight`(L61)는 `name`+`description`만. enrich(`enrich_table_description_step.py:120`)가 테이블 `logical_name`+`analyzed_description` 운반. → 컬럼도 같은 모양으로 `ColumnInsight.logical_name` 추가 + enrich 운반 + `column.py` SET.

## D2. 응답 스키마 = `ColumnInsight.logical_name` 1필드만
- **Decision**: `ColumnInsight`에 `logical_name: str = Field("", description="컬럼 도메인 표시명 한 줄 (코드 식별자·DDL 타입 X)")` 추가. 그 외 필드 0.
- **근거(Constitution VI 스키마절제)**: 옛 `usage`는 소비0이라 제거된 전례 — `logical_name`은 표시로 소비되므로 추가 정당. 최소.

## D3. 프롬프트 — 두 전략 동일·byte-identity 유지
- **Decision**: `prompts/dbms/table_description.yaml`·`prompts/framework/table_description.yaml` 둘 다 컬럼 출력에 **논리명(짧은 업무명)** 항목을 **동일 문구·동일 위치**로 추가. 공유 system prefix가 있으면 그 byte-identity 깨지지 않게(둘 다 동일 편집).
- **근거(Constitution V KV캐시)**: prefix 공유 프롬프트는 byte-identical 필수. 양쪽 동일 편집으로 보장. **전략 불변(FR-002)** 도 이로써 충족.

## D4. 빈 논리명 미저장 (025 규칙)
- **Decision**: LLM이 논리명을 못 만들거나 빈값이면 저장 안 함. `column.py` SET 시 비어있지 않은 값만(테이블 logical_name `SET_ANALYZED`가 빈값 미저장인 것과 동일).
- **근거**: spec 025 FR-003 동일 규칙. COLUMN.logical_name 부재 = 설명 부재와 동일 정상상태(Constitution IV — silent fail 아님, 정상 부재).

## D5. 수렴 (한 컬럼 여러 관점)
- **Decision**: 한 컬럼이 여러 코드노드 관점에서 분석돼 논리명 후보가 여럿이면, 테이블 logical_name 수렴 방식과 동일하게 단일값으로 수렴(enrich 합성 시 일관 선택).
- **근거**: enrich_table_description_step 이 이미 컬럼 description 을 코드노드들에서 합성 — 같은 합성 지점에서 logical_name 도 일관 선택.

## 미해결: 없음
