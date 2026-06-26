# Phase 1 Data Model: 식별자 정규화 + 분석설명

## 엔티티

### 식별자(FQN/id) — 단일 헬퍼 규칙
```
fqn(*components) = 비어있지 않은 조각만 "." 으로 join
  table  = fqn(db, schema, name)          # db="" → "RWIS.X" / db있음 → "mydb.RWIS.X"
  column = fqn(db, schema, table, name)   # = fqn(table_id, column_name)
불변식: 빈 구성요소로 인한 선두/중복 점 없음. 같은 입력 → 항상 같은 키(생성·후처리 동일).
```

### TABLE 노드
```
id = fqn(db, schema, name)            # ★헬퍼 위임(현재 Table.id property가 직접 조립)
db, schema, name                       # 보조 속성 (빈값이면 미저장)
description / description_source        # DDL 1차설명
comment                                # 주석 원문 (빈값이면 미저장)
analyzed_description                    # ★LLM 합성설명 — step7이 id 매칭으로 SET (유실 대상)
table_type
불변식: 코드가 R/W하는 테이블은 analyzed_description 보유(생성됐는데 유실 0). 빈 db/comment 키 미저장.
```

### COLUMN 노드
```
id = fqn(db, schema, table, name)     # ★헬퍼 위임
name, dtype, nullable, is_primary_key, is_unique
default_value                          # None이면 키 미저장(이미 그러함)
description / comment / analyzed_description  # comment 빈값 미저장, analyzed_description=유실 대상
불변식: 동일 — analyzed_description 유실 0, 빈 comment 미저장.
```

### 분석설명 입력(table insights)
```
table_fqn = fqn(...)  # ★노드 id와 동일 헬퍼 → step7 write가 노드에 적중
column write id = fqn(table_fqn, column_name)
불변식: insights fqn 형식 == 노드 id 형식 (양끝 일치 = 유실 불가).
```

## 관계 / 흐름
```
DDL 파싱 → TABLE/COLUMN 노드 생성 (id=fqn 헬퍼, 빈값미저장 MERGE)
step4 링킹 → reads/writes fqn (fqn 헬퍼) → step6 table_insights
step6 → LLM table_description 생성 → step7
step7 → analyzed_description을 id(fqn 헬퍼)로 MATCH SET
        → 매칭 0건이면 경고(표면화)   ← 조용한 유실 차단
```

## 상태/검증 규칙
- update_analyzed: 입력 N건 중 매칭 M건. M < (대상 존재 수)면 경고 로그.
- 재분석 후: R/W 테이블·컬럼 analyzed_description 빈 것 0 / 선두점 id 0 / 기존 회귀 0.
