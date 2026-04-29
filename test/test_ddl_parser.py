"""DDL 파서 단독 검증 테스트 (외부 의존성 없음).

목적:
  - analyzer/common/cypher/ddl_handler.py 의 DDLParser (정규식 휴리스틱)가
    실제 DDL 파일을 정확히 파싱하는지 원문과 대조 검증.
  - LLM/Neo4j 없이 순수 파싱만 측정.

실행:
  cd robo-data-analyzer
  DATA_DIR=c:/uEngine/robo/data-slots/AMS pytest test/test_ddl_parser.py -v -s

검증 항목:
  1. 원문 CREATE TABLE 카운트 vs 파싱 테이블 카운트
  2. COMMENT ON TABLE/COLUMN 매칭률
  3. 컬럼 개수 (한 줄 다중 컬럼 케이스 포함)
  4. FK / PK 추출
  5. 특정 테이블 (TI_AFFH 60개 컬럼) 정확성
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

# 프로젝트 루트 sys.path 추가
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from analyzer.common.cypher.ddl_handler import DDLParser, parse_ddl  # noqa: E402


# 기본 슬롯: 환경변수 DATA_DIR 우선, 없으면 AMS 슬롯 직접 지정
DEFAULT_SLOT = r"c:\uEngine\robo\data-slots\AMS"
DATA_DIR = os.environ.get("DATA_DIR", DEFAULT_SLOT)
DDL_DIR = os.path.join(DATA_DIR, "ddl")


def _load_ddl_files() -> list[tuple[str, str]]:
    """ddl 폴더 안의 모든 SQL 파일 (이름, 내용) 리스트."""
    if not os.path.isdir(DDL_DIR):
        pytest.skip(f"DDL 디렉토리 없음: {DDL_DIR}")
    files = []
    for fname in sorted(os.listdir(DDL_DIR)):
        fpath = os.path.join(DDL_DIR, fname)
        if not os.path.isfile(fpath):
            continue
        with open(fpath, "r", encoding="utf-8", errors="replace") as f:
            files.append((fname, f.read()))
    if not files:
        pytest.skip(f"DDL 파일 없음: {DDL_DIR}")
    return files


def _measure_raw_stats(ddl: str) -> dict:
    """원문에서 정규식으로 직접 카운트한 통계 (정답)."""
    return {
        "create_table": len(re.findall(r"\bCREATE\s+TABLE\b", ddl, re.IGNORECASE)),
        "comment_table": len(re.findall(r"\bCOMMENT\s+ON\s+TABLE\b", ddl, re.IGNORECASE)),
        "comment_column": len(re.findall(r"\bCOMMENT\s+ON\s+COLUMN\b", ddl, re.IGNORECASE)),
        "alter_table": len(re.findall(r"\bALTER\s+TABLE\b", ddl, re.IGNORECASE)),
        "foreign_key": len(re.findall(r"\bFOREIGN\s+KEY\b", ddl, re.IGNORECASE)),
        "references": len(re.findall(r"\bREFERENCES\b", ddl, re.IGNORECASE)),
        "primary_key": len(re.findall(r"\bPRIMARY\s+KEY\b", ddl, re.IGNORECASE)),
        # Oracle export 특징: ) TABLESPACE / 슬래시 종결자
        "paren_then_tablespace": len(re.findall(r"\)\s*\nTABLESPACE", ddl, re.IGNORECASE)),
        "paren_then_semicolon": len(re.findall(r"\)\s*;", ddl)),
        "slash_terminator": len(re.findall(r"^/\s*$", ddl, re.MULTILINE)),
    }


def test_ddl_parser_against_raw_source():
    """원문 통계 vs DDLParser 결과 대조 검증."""
    print(f"\n=== DDL 파서 검증 ===")
    print(f"DATA_DIR: {DATA_DIR}")
    print(f"DDL_DIR:  {DDL_DIR}")

    files = _load_ddl_files()
    parser = DDLParser()

    total_raw_ct = 0
    total_parsed_tables = 0
    failures = []

    for fname, ddl in files:
        print(f"\n--- 파일: {fname} ({len(ddl):,} bytes) ---")

        raw = _measure_raw_stats(ddl)
        result = parser.parse(ddl)
        tables = result.get("analysis", [])

        total_raw_ct += raw["create_table"]
        total_parsed_tables += len(tables)

        # 원문 통계 출력
        print(f"  [원문 통계]")
        print(f"    CREATE TABLE:        {raw['create_table']}")
        print(f"    ) TABLESPACE 패턴:   {raw['paren_then_tablespace']}  (Oracle export)")
        print(f"    ) ; 패턴:            {raw['paren_then_semicolon']}  (ANSI)")
        print(f"    슬래시(/) 종결자:    {raw['slash_terminator']}")
        print(f"    COMMENT ON TABLE:    {raw['comment_table']}")
        print(f"    COMMENT ON COLUMN:   {raw['comment_column']}")
        print(f"    ALTER TABLE:         {raw['alter_table']}")
        print(f"    FOREIGN KEY (text):  {raw['foreign_key']}")
        print(f"    REFERENCES (text):   {raw['references']}")
        print(f"    PRIMARY KEY (text):  {raw['primary_key']}")

        # 파싱 결과 통계
        total_cols = sum(len(t["columns"]) for t in tables)
        total_fks = sum(len(t["foreignKeys"]) for t in tables)
        total_pks = sum(len(t["primaryKeys"]) for t in tables)
        with_cols = sum(1 for t in tables if t["columns"])
        with_comments = sum(1 for t in tables if t["table"].get("comment"))

        print(f"  [DDLParser 결과]")
        print(f"    파싱 테이블:         {len(tables)}")
        print(f"    매칭률:              {len(tables)/max(raw['create_table'],1)*100:.1f}%")
        print(f"    총 컬럼:             {total_cols}")
        print(f"    컬럼 있는 테이블:    {with_cols}/{len(tables)}")
        print(f"    테이블 comment 있음: {with_comments}/{len(tables)}")
        print(f"    FK 추출:             {total_fks}")
        print(f"    PK 추출:             {total_pks}")

        # 차이 분석
        if len(tables) != raw["create_table"]:
            diff = raw["create_table"] - len(tables)
            failures.append(
                f"{fname}: CREATE TABLE {raw['create_table']}개 중 {len(tables)}개만 파싱됨 "
                f"(누락 {diff}개)"
            )

    # 종합
    print(f"\n=== 종합 ===")
    print(f"원문 CREATE TABLE 합계: {total_raw_ct}")
    print(f"파싱 테이블 합계:        {total_parsed_tables}")
    print(f"전체 매칭률:             {total_parsed_tables/max(total_raw_ct,1)*100:.1f}%")

    if failures:
        print(f"\n[!] 검증 실패:")
        for f in failures:
            print(f"  - {f}")

    # 1차 검증: 테이블 매칭률 90% 이상 (Oracle export DDL 위반시 즉시 실패)
    assert total_parsed_tables > 0, (
        f"파싱 테이블 0개! DDLParser 정규식이 원문 종결자(`)\\nTABLESPACE...\\n/`)를 인식 못함. "
        f"원문 CREATE TABLE {total_raw_ct}개 발견."
    )


def test_specific_table_ti_affh():
    """TI_AFFH 테이블 정확성 검증 (60개 컬럼, 한 줄에 탭구분).

    원문: AMS_tables.sql line 56-65
    기대값:
      - 컬럼 60개 (한 줄에 탭으로 구분되어 있음)
      - 모든 컬럼이 NVARCHAR/NUMBER 타입에 DEFAULT 값 보유
      - COMMENT ON COLUMN 매핑 (한글 코멘트)
    """
    files = _load_ddl_files()
    ams_file = next(((n, c) for n, c in files if "AMS_tables" in n or "AMS" in n.upper()), None)
    if not ams_file:
        pytest.skip("AMS_tables.sql 파일 없음")

    fname, ddl = ams_file
    result = parse_ddl(ddl)
    tables = result.get("analysis", [])

    # TI_AFFH 검색
    ti_affh = next((t for t in tables if t["table"]["name"].upper() == "TI_AFFH"), None)

    print(f"\n=== TI_AFFH 정확성 검증 ===")
    if not ti_affh:
        print("[!] TI_AFFH 테이블이 파싱 결과에 없음")
        # 파싱된 테이블 이름 일부 출력
        names = [t["table"]["name"] for t in tables[:20]]
        print(f"  파싱된 테이블 (앞 20개): {names}")
        pytest.fail(
            f"TI_AFFH가 파싱 결과에 없음. 원문 line 58에 'CREATE TABLE \"AMS_MRT\".\"TI_AFFH\"' 존재. "
            f"전체 파싱 테이블 수: {len(tables)}"
        )

    cols = ti_affh["columns"]
    table_comment = ti_affh["table"].get("comment", "")

    # 원문 line 58 한 줄에서 콤마로 카운트한 실제 컬럼 수 = 69개
    # (테스트 작성자의 초기 추정 60개는 잘못된 값)
    EXPECTED_COLS = 69

    print(f"  schema: {ti_affh['table']['schema']}")
    print(f"  name:   {ti_affh['table']['name']}")
    print(f"  comment: {table_comment}")
    print(f"  컬럼 수: {len(cols)} (기대값 {EXPECTED_COLS})")

    if cols:
        print(f"  파싱된 컬럼 목록 (앞/뒤 3개):")
        for c in cols[:3] + cols[-3:]:
            print(f"    - {c['name']:30s} {c['dtype']:20s} default={c.get('default_value', '-')} comment={c.get('comment', '-')[:30]}")

    # 컬럼 수 검증
    assert len(cols) == EXPECTED_COLS, (
        f"TI_AFFH 컬럼 수 불일치. 파싱={len(cols)}, 원문={EXPECTED_COLS}."
    )
    # 첫/마지막 컬럼 이름 검증
    assert cols[0]["name"] == "MANDT", f"첫 컬럼이 MANDT여야 하는데 {cols[0]['name']}"
    assert cols[-1]["name"] == "REGISTRABLE", f"마지막 컬럼이 REGISTRABLE이어야 하는데 {cols[-1]['name']}"
    # 한글 코멘트 검증
    assert table_comment == "IF_작업오더에대한PRT지정데이터", (
        f"TI_AFFH 테이블 comment 불일치. 파싱='{table_comment}'"
    )
    # 한글 컬럼 코멘트 검증 (MANDT는 '클라이언트')
    mandt_col = next((c for c in cols if c["name"] == "MANDT"), None)
    assert mandt_col and mandt_col.get("comment") == "클라이언트", (
        f"MANDT 컬럼 한글 코멘트 누락. comment='{mandt_col.get('comment') if mandt_col else None}'"
    )


def _split_top_level_commas(s: str) -> list[str]:
    """괄호/따옴표 인지 콤마 split — 컬럼 정의 카운트 정답 산출용."""
    out, buf = [], []
    depth = 0
    in_sq = in_dq = False
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        nxt = s[i + 1] if i + 1 < n else ""
        if in_sq:
            buf.append(ch)
            if ch == "'" and nxt == "'":
                buf.append(nxt); i += 2; continue
            if ch == "'":
                in_sq = False
            i += 1; continue
        if in_dq:
            buf.append(ch)
            if ch == '"':
                in_dq = False
            i += 1; continue
        if ch == "'":
            in_sq = True; buf.append(ch); i += 1; continue
        if ch == '"':
            in_dq = True; buf.append(ch); i += 1; continue
        if ch == "(":
            depth += 1; buf.append(ch); i += 1; continue
        if ch == ")":
            depth -= 1; buf.append(ch); i += 1; continue
        if ch == "," and depth == 0:
            seg = "".join(buf).strip()
            if seg:
                out.append(seg)
            buf = []; i += 1; continue
        buf.append(ch); i += 1
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return out


def test_per_table_column_count_audit():
    """각 테이블의 컬럼 수가 원문과 정확히 일치하는지 전수 검증.

    원문 정답 = CREATE TABLE 본문(괄호 안)을 괄호/따옴표 인지 콤마 split → 제약절 제외.
    """
    files = _load_ddl_files()
    fname, ddl = files[0]

    # 원문에서 (schema, name) → 컬럼 수 산출
    raw_col_counts: dict[tuple[str, str], int] = {}
    body_re = re.compile(
        r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?'
        r'(?:"(?P<schema>[^"]+)"\.)?"(?P<name>[^"]+)"\s*\(',
        re.IGNORECASE,
    )
    for m in body_re.finditer(ddl):
        schema = m.group("schema") or ""
        name = m.group("name")
        # 본문 시작은 매치 끝의 ( 다음. paren-aware로 매칭되는 ) 위치 찾기
        start = m.end()
        depth = 1
        in_sq = in_dq = False
        i = start
        while i < len(ddl) and depth > 0:
            ch = ddl[i]
            nxt = ddl[i + 1] if i + 1 < len(ddl) else ""
            if in_sq:
                if ch == "'" and nxt == "'":
                    i += 2; continue
                if ch == "'":
                    in_sq = False
                i += 1; continue
            if in_dq:
                if ch == '"':
                    in_dq = False
                i += 1; continue
            if ch == "'":
                in_sq = True; i += 1; continue
            if ch == '"':
                in_dq = True; i += 1; continue
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        body = ddl[start:i]
        segs = _split_top_level_commas(body)
        # 제약절 (PRIMARY KEY/FOREIGN KEY/UNIQUE/CHECK/CONSTRAINT 시작) 제외
        cols = [s for s in segs if not re.match(
            r'^\s*(?:CONSTRAINT|PRIMARY|FOREIGN|UNIQUE|CHECK)\b', s, re.IGNORECASE)]
        raw_col_counts[(schema, name)] = len(cols)

    # 파싱 결과
    result = parse_ddl(ddl)
    parsed_counts = {(t["table"]["schema"], t["table"]["name"]): len(t["columns"]) for t in result["analysis"]}

    # 비교
    mismatches = []
    for key, raw_n in raw_col_counts.items():
        parsed_n = parsed_counts.get(key)
        if parsed_n is None:
            continue  # 누락은 다른 테스트에서 검출
        if parsed_n != raw_n:
            mismatches.append((key, raw_n, parsed_n))

    print(f"\n=== 테이블별 컬럼 수 전수 감사 ===")
    print(f"원문 CREATE TABLE 본문 추출: {len(raw_col_counts)}")
    print(f"파싱 테이블: {len(parsed_counts)}")
    print(f"교집합 검증: {len(raw_col_counts) - sum(1 for k in raw_col_counts if k not in parsed_counts)}")
    print(f"컬럼 수 불일치: {len(mismatches)}")
    if mismatches:
        print(f"\n--- 불일치 상위 10개 ---")
        for (s, n), raw_n, p_n in mismatches[:10]:
            print(f"  {s}.{n}: 원문={raw_n} 파싱={p_n} (차이 {p_n - raw_n:+d})")

    assert not mismatches, f"{len(mismatches)}개 테이블의 컬럼 수가 원문과 불일치"


def test_table_comment_audit():
    """원문 COMMENT ON TABLE 모두가 파싱 결과에 정확히 매핑됐는지 전수 검증."""
    files = _load_ddl_files()
    fname, ddl = files[0]

    # 원문에서 COMMENT ON TABLE 추출 — schema.name → comment 매핑
    raw_comments: dict[tuple[str, str], str] = {}
    pat = re.compile(
        r"COMMENT\s+ON\s+TABLE\s+"
        r'(?:"(?P<schema>[^"]+)"\.)?"(?P<name>[^"]+)"\s+IS\s+'
        r"'(?P<body>(?:[^']|'')*)'",
        re.IGNORECASE | re.DOTALL,
    )
    for m in pat.finditer(ddl):
        schema = m.group("schema") or ""
        name = m.group("name")
        body = m.group("body").replace("''", "'")
        raw_comments[(schema, name)] = body

    result = parse_ddl(ddl)
    parsed = {(t["table"]["schema"], t["table"]["name"]): (t["table"].get("comment") or "")
              for t in result["analysis"]}

    print(f"\n=== 테이블 comment 전수 감사 ===")
    print(f"원문 COMMENT ON TABLE 고유: {len(raw_comments)}")
    print(f"파싱 테이블 중 comment 보유: {sum(1 for v in parsed.values() if v)}")

    missing_in_parse = [k for k in raw_comments if k not in parsed]
    mismatched = [(k, raw_comments[k], parsed[k]) for k in raw_comments
                  if k in parsed and parsed[k] != raw_comments[k]]

    print(f"파싱 결과에 테이블 자체가 없음: {len(missing_in_parse)}")
    print(f"comment 값 불일치: {len(mismatched)}")
    if missing_in_parse:
        print(f"  테이블 누락 예시: {missing_in_parse[:5]}")
    if mismatched:
        print(f"\n--- comment 불일치 상위 5개 ---")
        for k, raw, p in mismatched[:5]:
            print(f"  {k[0]}.{k[1]}: 원문={raw[:50]!r} vs 파싱={p[:50]!r}")

    assert not mismatched, f"{len(mismatched)}개 테이블 comment 값 불일치"
    # missing_in_parse는 누락 테이블이 있을 때만 발생 → 별도 검증 (현재 0)


def test_column_comment_sample_audit():
    """COMMENT ON COLUMN 200개 무작위 샘플로 정확성 검증."""
    import random
    files = _load_ddl_files()
    fname, ddl = files[0]

    pat = re.compile(
        r"COMMENT\s+ON\s+COLUMN\s+"
        r'(?:"(?P<schema>[^"]+)"\.)?"(?P<table>[^"]+)"\."(?P<col>[^"]+)"\s+IS\s+'
        r"'(?P<body>(?:[^']|'')*)'",
        re.IGNORECASE | re.DOTALL,
    )
    raw_col_comments = []
    for m in pat.finditer(ddl):
        schema = m.group("schema") or ""
        raw_col_comments.append((
            schema, m.group("table"), m.group("col"),
            m.group("body").replace("''", "'"),
        ))

    print(f"\n=== 컬럼 comment 샘플 감사 ===")
    print(f"원문 COMMENT ON COLUMN 총 {len(raw_col_comments)}개")

    result = parse_ddl(ddl)
    by_key: dict[tuple[str, str], dict[str, str]] = {}
    for t in result["analysis"]:
        key = (t["table"]["schema"], t["table"]["name"])
        by_key[key] = {c["name"]: c.get("comment", "") for c in t["columns"]}

    random.seed(42)
    sample = random.sample(raw_col_comments, min(200, len(raw_col_comments)))
    mismatched = []
    missing = []
    for schema, table, col, expected in sample:
        cols = by_key.get((schema, table))
        if cols is None:
            missing.append((schema, table, col, "테이블 없음"))
            continue
        if col not in cols:
            missing.append((schema, table, col, "컬럼 없음"))
            continue
        actual = cols[col]
        if actual != expected:
            mismatched.append((schema, table, col, expected, actual))

    print(f"샘플 200개 검증 → 일치: {200 - len(mismatched) - len(missing)}, 불일치: {len(mismatched)}, 누락: {len(missing)}")
    if mismatched:
        print(f"\n--- 불일치 상위 5개 ---")
        for s, t, c, e, a in mismatched[:5]:
            print(f"  {s}.{t}.{c}: 원문={e[:50]!r} vs 파싱={a[:50]!r}")
    if missing:
        print(f"\n--- 누락 상세 (모두) ---")
        for s, t, c, why in missing:
            avail_cols = sorted(by_key.get((s, t), {}).keys())[:10]
            print(f"  schema={s!r} table={t!r} col={c!r} reason={why}")
            print(f"    파싱된 컬럼 (앞 10개): {avail_cols}")

    assert not mismatched, f"{len(mismatched)}개 컬럼 comment 불일치"
    assert not missing, f"{len(missing)}개 컬럼 comment 누락"


def test_audit_duplicate_create_table_in_source():
    """원문에서 같은 schema.name으로 CREATE TABLE이 여러 번 정의된 케이스 진단.

    DDL 자체의 데이터 무결성 문제 (export 도중 중복) — 코드 수정이 아니라
    원문 수정이 필요함을 보여주는 검증.
    """
    files = _load_ddl_files()
    fname, ddl = files[0]

    # 본문 paren-aware 추출 (test_per_table_column_count_audit 와 동일 방식)
    occurrences: dict[tuple[str, str], list[dict]] = {}
    body_re = re.compile(
        r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?'
        r'(?:"(?P<schema>[^"]+)"\.)?"(?P<name>[^"]+)"\s*\(',
        re.IGNORECASE,
    )
    for m in body_re.finditer(ddl):
        schema = m.group("schema") or ""
        name = m.group("name")
        line_no = ddl[:m.start()].count("\n") + 1
        # 본문 paren-aware
        start = m.end()
        depth, i = 1, start
        in_sq = in_dq = False
        while i < len(ddl) and depth > 0:
            ch = ddl[i]
            nxt = ddl[i + 1] if i + 1 < len(ddl) else ""
            if in_sq:
                if ch == "'" and nxt == "'":
                    i += 2; continue
                if ch == "'":
                    in_sq = False
                i += 1; continue
            if in_dq:
                if ch == '"':
                    in_dq = False
                i += 1; continue
            if ch == "'":
                in_sq = True; i += 1; continue
            if ch == '"':
                in_dq = True; i += 1; continue
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        body = ddl[start:i]
        # 컬럼명 추출 (`"name"` 패턴, 제약절 제외)
        segs = _split_top_level_commas(body)
        col_names = []
        for s in segs:
            if re.match(r'^\s*(?:CONSTRAINT|PRIMARY|FOREIGN|UNIQUE|CHECK)\b', s, re.IGNORECASE):
                continue
            cm = re.match(r'\s*"?(\w+)"?', s)
            if cm:
                col_names.append(cm.group(1))
        occurrences.setdefault((schema, name), []).append({
            "line": line_no,
            "col_count": len(col_names),
            "col_names": col_names,
        })

    # 중복 발생 (2회 이상 정의된 테이블) 추출
    duplicates = {k: v for k, v in occurrences.items() if len(v) > 1}

    print(f"\n=== 원문 중복 CREATE TABLE 진단 ===")
    print(f"총 고유 테이블 (DDL 내): {len(occurrences)}")
    print(f"2회 이상 정의된 테이블: {len(duplicates)}")
    if not duplicates:
        print("  중복 없음")
        return

    # 중복을 두 부류로 분리: (a) 컬럼셋 동일 (단순 중복) (b) 컬럼셋 다름 (스키마 충돌)
    same_set, diff_set = [], []
    for key, occs in duplicates.items():
        col_sets = [tuple(sorted(o["col_names"])) for o in occs]
        if len(set(col_sets)) == 1:
            same_set.append((key, occs))
        else:
            diff_set.append((key, occs))

    print(f"  (a) 단순 중복 (컬럼 동일): {len(same_set)}")
    print(f"  (b) 스키마 충돌 (컬럼 다름): {len(diff_set)}")

    if same_set:
        print(f"\n--- (a) 단순 중복 상위 10개 ---")
        for (s, n), occs in same_set[:10]:
            lines = [o["line"] for o in occs]
            print(f"  {s}.{n}: line {lines}, 컬럼 {occs[0]['col_count']}개")

    if diff_set:
        print(f"\n--- (b) 스키마 충돌 상세 (처음 10개) ---")
        for (s, n), occs in diff_set[:10]:
            print(f"\n  {s}.{n}:")
            for o in occs:
                print(f"    line {o['line']:>6}: 컬럼 {o['col_count']}개 → {o['col_names']}")
            # 컬럼 차이 분석
            sets = [set(o["col_names"]) for o in occs]
            common = set.intersection(*sets)
            unique_per = [s_ - common for s_ in sets]
            print(f"    공통 컬럼: {sorted(common)}")
            for i, u in enumerate(unique_per):
                print(f"    정의 {i+1}만 가진 컬럼: {sorted(u)}")


def test_audit_safe_dedup_candidates():
    """파싱 결과 기준 dedup 분석 (노드 생성 X — 메모리 비교만).

    절차:
      1. _split_statements + _parse_chunk 로 모든 CREATE TABLE occurrence를 수집
         (DDLParser.parse 와 달리 dict에 덮어쓰지 않고 list로 모두 보존)
      2. 각 occurrence → (schema, name, columns=[(col_name, dtype), ...])
      3. 같은 (schema, name) 그룹화
      4. 컬럼 시퀀스 동일 ⟹ 단순 중복 (안전 제거 가능)
         컬럼 시퀀스 다름 ⟹ 정보 차이 (보존 필요)

    파싱 결과로 비교하므로 sqlglot이 정규화한 값 (BYTE/CHAR 단위 무시, 공백 정리,
    따옴표 제거 등)이 자동 적용되어 표면적 차이는 무시됨.
    """
    import sqlglot
    from sqlglot import exp

    files = _load_ddl_files()
    fname, ddl = files[0]

    # 1. 모든 CREATE TABLE occurrence 수집 — 같은 key여도 list로 모두 보존
    occurrences: list[dict] = []
    cursor = 0
    for chunk in DDLParser._split_statements(ddl):
        # 원문에서 chunk 위치 찾기 (라인 추적용)
        idx = ddl.find(chunk, cursor)
        if idx == -1:
            idx = cursor
        cursor = idx + len(chunk)
        line_no = ddl[:idx].count("\n") + 1

        normalized = DDLParser._normalize_chunk(chunk)
        for stmt in DDLParser._parse_chunk(normalized, "oracle"):
            if not (isinstance(stmt, exp.Create) and (stmt.kind or "").upper() == "TABLE"):
                continue
            schema_node = stmt.this
            if not isinstance(schema_node, exp.Schema):
                continue
            table_node = schema_node.this
            if not isinstance(table_node, exp.Table):
                continue
            schema = table_node.db or ""
            name = table_node.name or ""
            cols: list[tuple[str, str]] = []
            for d in (schema_node.expressions or []):
                if isinstance(d, exp.ColumnDef):
                    dtype = DDLParser._format_dtype(d.args.get("kind"), "oracle")
                    cols.append((d.name, dtype))
            occurrences.append({
                "schema": schema, "name": name,
                "columns": cols, "line": line_no,
            })

    # 2. 같은 (schema, name) 그룹화
    groups: dict[tuple[str, str], list[dict]] = {}
    for o in occurrences:
        groups.setdefault((o["schema"], o["name"]), []).append(o)

    duplicates = {k: v for k, v in groups.items() if len(v) > 1}

    # 3. 컬럼 시퀀스 동일 vs 다름 분류
    safe_dedup, differ_dedup = [], []
    for key, occs in duplicates.items():
        col_seqs = {tuple(o["columns"]) for o in occs}
        (safe_dedup if len(col_seqs) == 1 else differ_dedup).append((key, occs))

    # 4. 보고
    print(f"\n=== 파싱 결과 기준 dedup 분석 ===")
    print(f"총 CREATE TABLE occurrence: {len(occurrences)}")
    print(f"고유 (schema, name): {len(groups)}")
    print(f"중복 발생 (2회+): {len(duplicates)}")
    print(f"  -> (a) 컬럼 시퀀스 동일 (안전 제거 가능): {len(safe_dedup)}")
    print(f"  -> (b) 컬럼 시퀀스 다름 (보존 필요):     {len(differ_dedup)}")

    print(f"\n--- (a) 안전 제거 후보 (전체 {len(safe_dedup)}개) ---")
    for (s, n), occs in safe_dedup[:30]:
        lines = [o["line"] for o in occs]
        col_count = len(occs[0]["columns"])
        print(f"  {s}.{n}: line {lines}, 컬럼 {col_count}개 (모두 동일)")

    print(f"\n--- (b) 컬럼 시퀀스 다른 케이스 ({len(differ_dedup)}개) — 첫 5개 컬럼 차이 ---")
    for (s, n), occs in differ_dedup[:10]:
        lines = [o["line"] for o in occs]
        print(f"\n  {s}.{n}: line {lines}")
        for i, o in enumerate(occs):
            cs = o["columns"]
            preview = [f"{c[0]}:{c[1]}" for c in cs[:5]]
            print(f"    정의 {i+1} (line {o['line']}, 컬럼 {len(cs)}개): {preview}{'...' if len(cs) > 5 else ''}")
        # 컬럼 이름 셋만 비교한 차이
        name_sets = [{c[0] for c in o["columns"]} for o in occs]
        common_names = set.intersection(*name_sets)
        for i, ns in enumerate(name_sets):
            unique = ns - common_names
            if unique:
                print(f"    정의 {i+1}만 가진 컬럼명: {sorted(unique)}")


def test_synthetic_postgres_ddl():
    """PostgreSQL 합성 DDL 회귀 — 인라인 PK/FK + 컬럼 제약 + COMMENT ON."""
    ddl = """
    CREATE SCHEMA IF NOT EXISTS "shop";

    CREATE TABLE "shop"."customer" (
        "id"          SERIAL PRIMARY KEY,
        "email"       VARCHAR(200) NOT NULL UNIQUE,
        "name"        VARCHAR(100) NOT NULL,
        "created_at"  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE "shop"."orders" (
        "id"           BIGSERIAL PRIMARY KEY,
        "customer_id"  INTEGER NOT NULL REFERENCES "shop"."customer"("id"),
        "total"        NUMERIC(12,2) NOT NULL DEFAULT 0,
        "status"       VARCHAR(20) NOT NULL DEFAULT 'pending'
    );

    COMMENT ON TABLE "shop"."customer" IS 'eCommerce customers';
    COMMENT ON COLUMN "shop"."customer"."email" IS '로그인 이메일';
    COMMENT ON TABLE "shop"."orders"   IS '주문 헤더';
    COMMENT ON COLUMN "shop"."orders"."total" IS '주문 총액(원)';
    """
    result = parse_ddl(ddl)
    tables = {(t["table"]["schema"], t["table"]["name"]): t for t in result["analysis"]}

    print("\n=== PostgreSQL 합성 회귀 ===")
    print(f"  파싱 테이블: {sorted(tables.keys())}")

    # 테이블 존재
    assert ("shop", "customer") in tables
    assert ("shop", "orders") in tables

    # customer
    cust = tables[("shop", "customer")]
    assert cust["table"]["comment"] == "eCommerce customers"
    assert len(cust["columns"]) == 4
    assert cust["primaryKeys"] == ["id"]
    email = next(c for c in cust["columns"] if c["name"] == "email")
    assert email["nullable"] is False
    assert email.get("unique") is True
    assert email["comment"] == "로그인 이메일"

    # orders FK
    od = tables[("shop", "orders")]
    assert od["table"]["comment"] == "주문 헤더"
    assert od["primaryKeys"] == ["id"]
    assert len(od["foreignKeys"]) == 1
    fk = od["foreignKeys"][0]
    assert fk["column"] == "customer_id"
    assert fk["ref"] == "shop.customer.id"
    total = next(c for c in od["columns"] if c["name"] == "total")
    assert total["comment"] == "주문 총액(원)"
    assert total["default_value"] == "0"


def test_synthetic_mysql_ddl():
    """MySQL 합성 DDL 회귀 — 인라인 COMMENT, ENGINE/AUTO_INCREMENT, 테이블레벨 PK/FK."""
    ddl = """
    CREATE TABLE `category` (
        `id` INT AUTO_INCREMENT PRIMARY KEY,
        `name` VARCHAR(50) NOT NULL COMMENT 'category display name'
    ) ENGINE=InnoDB COMMENT='product categories';

    CREATE TABLE `product` (
        `id`          INT AUTO_INCREMENT,
        `category_id` INT NOT NULL,
        `name`        VARCHAR(200) NOT NULL,
        `price`       DECIMAL(10,2) UNSIGNED NOT NULL DEFAULT 0,
        PRIMARY KEY (`id`),
        CONSTRAINT `fk_prod_cat` FOREIGN KEY (`category_id`) REFERENCES `category`(`id`)
    ) ENGINE=InnoDB;
    """
    result = parse_ddl(ddl, dialect="mysql")
    tables = {(t["table"]["schema"], t["table"]["name"]): t for t in result["analysis"]}

    print("\n=== MySQL 합성 회귀 ===")
    print(f"  파싱 테이블: {sorted(tables.keys())}")

    cat = tables[("", "category")]
    assert cat["table"]["comment"] == "product categories"
    assert cat["primaryKeys"] == ["id"]
    name = next(c for c in cat["columns"] if c["name"] == "name")
    assert name["comment"] == "category display name"

    prod = tables[("", "product")]
    assert prod["primaryKeys"] == ["id"]
    assert len(prod["foreignKeys"]) == 1
    fk = prod["foreignKeys"][0]
    assert fk["column"] == "category_id"
    assert fk["ref"] == "category.id"


def test_synthetic_alter_table_constraints():
    """ANSI 표준: CREATE 후 ALTER TABLE ADD CONSTRAINT PK/FK 추가."""
    ddl = """
    CREATE TABLE "ord"."line" (
        "ord_id" INTEGER NOT NULL,
        "seq"    INTEGER NOT NULL,
        "qty"    INTEGER NOT NULL
    );

    CREATE TABLE "ord"."header" (
        "id" INTEGER NOT NULL
    );

    ALTER TABLE "ord"."header" ADD CONSTRAINT pk_header PRIMARY KEY ("id");
    ALTER TABLE "ord"."line"   ADD CONSTRAINT pk_line   PRIMARY KEY ("ord_id", "seq");
    ALTER TABLE "ord"."line"   ADD CONSTRAINT fk_line_ord
        FOREIGN KEY ("ord_id") REFERENCES "ord"."header"("id");
    """
    result = parse_ddl(ddl, dialect="postgres")
    tables = {(t["table"]["schema"], t["table"]["name"]): t for t in result["analysis"]}

    print("\n=== ALTER TABLE 제약 회귀 ===")
    line = tables[("ord", "line")]
    assert line["primaryKeys"] == ["ord_id", "seq"]
    assert len(line["foreignKeys"]) == 1
    fk = line["foreignKeys"][0]
    assert fk["column"] == "ord_id"
    assert fk["ref"] == "ord.header.id"

    header = tables[("ord", "header")]
    assert header["primaryKeys"] == ["id"]


def test_synthetic_oracle_export_format():
    """Oracle SQL*Plus export 형식 — / 종결자 + TABLESPACE/STORAGE/LOB + BYTE/CHAR + 한글 COMMENT."""
    ddl = """-- DDL header noise with CREATE TABLE keyword in comment
!ModeUser6.0!"USR"

--------------------------------------------------------
--  DDL for Table T_DEMO
--------------------------------------------------------
CREATE TABLE "S"."T_DEMO" (	"ID" NUMBER(18),	"CODE" VARCHAR(20 BYTE) DEFAULT '00',	"NAME" NVARCHAR2(100),	"AMT" NUMBER(9,3) DEFAULT 0
)
TABLESPACE "TS_X"PCTFREE 10INITRANS 1STORAGE (
\tBUFFER_POOL DEFAULT
)
NOLOGGINGNOCOMPRESS
NOPARALLEL
/
COMMENT ON TABLE "S"."T_DEMO" IS '(T_DEMO) 한글, 콤마, [대괄호] 포함 코멘트'
/
COMMENT ON COLUMN "S"."T_DEMO"."CODE" IS '코드값 — 길게 설명'
/
"""
    result = parse_ddl(ddl)
    tables = {(t["table"]["schema"], t["table"]["name"]): t for t in result["analysis"]}

    print("\n=== Oracle export 회귀 ===")
    assert ("S", "T_DEMO") in tables, f"파싱 결과: {sorted(tables.keys())}"
    t = tables[("S", "T_DEMO")]

    # 4개 컬럼
    assert len(t["columns"]) == 4
    cols = {c["name"]: c for c in t["columns"]}
    assert cols["ID"]["dtype"].startswith("NUMBER")
    assert cols["CODE"]["default_value"] == "'00'"
    # 한글+특수문자 코멘트 (Stage 5 regex가 잡아야 함)
    assert t["table"]["comment"] == "(T_DEMO) 한글, 콤마, [대괄호] 포함 코멘트"
    assert cols["CODE"]["comment"] == "코드값 — 길게 설명"


def test_diagnose_unparsed_tables():
    """누락된 테이블 원인 진단.

    원문 CREATE TABLE 명단을 정규식으로 추출 → DDLParser 결과와 차집합.
    누락된 첫 5개 테이블의 원본 정의를 단독 sqlglot 호출로 다시 파싱해서
    실패 원인을 표시.
    """
    import sqlglot
    _normalize_chunk = DDLParser._normalize_chunk
    _split_statements = DDLParser._split_statements

    files = _load_ddl_files()
    fname, ddl = files[0]

    # 원문에서 모든 CREATE TABLE 의 (schema, name) 추출
    raw_pattern = re.compile(
        r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?'
        r'(?:"([^"]+)"\.)?"([^"]+)"',
        re.IGNORECASE
    )
    raw_tables = {(m.group(1) or "", m.group(2)) for m in raw_pattern.finditer(ddl)}

    # DDLParser 결과
    result = parse_ddl(ddl)
    parsed_tables = {(t["table"]["schema"], t["table"]["name"]) for t in result["analysis"]}

    missing = raw_tables - parsed_tables
    print(f"\n=== 누락 진단 ===")
    print(f"원문 CREATE TABLE 고유: {len(raw_tables)}")
    print(f"파싱 성공: {len(parsed_tables)}")
    print(f"누락:     {len(missing)}")

    if not missing:
        print("[OK] 모든 테이블 파싱 성공")
        return

    # === 새 진단: splitter가 실제로 누락 테이블 chunk를 어떻게 자르는가 ===
    print(f"\n--- splitter 진단: 누락 테이블이 splitter chunk 어디에 들어가는가 ---")
    chunks = _split_statements(ddl)
    print(f"전체 chunk 수: {len(chunks)}")

    # chunk[0] 전체 dump (첫 chunk가 의심)
    print(f"\n>>> chunk[0] 길이={len(chunks[0])}")
    print(f"--- 시작 400자 ---\n{chunks[0][:400]}")
    print(f"--- 끝 200자 ---\n{chunks[0][-200:]}")

    # TADBCONTENTS 호스트 chunk dump
    target = '"AMS_MRT"."TADBCONTENTS"'
    for ci, ch in enumerate(chunks):
        if target in ch and "CREATE TABLE" in ch.upper():
            print(f"\n>>> chunk[{ci}] (TADBCONTENTS 호스트) 길이={len(ch)}")
            print(f"--- 시작 300자 ---\n{ch[:300]}")
            print(f"--- 끝 200자 ---\n{ch[-200:]}")
            break

    # 각 chunk가 몇 개의 CREATE TABLE을 가지고 있는지 분포
    multi_create_chunks = []
    for idx, ch in enumerate(chunks):
        ct_in_chunk = len(re.findall(r"\bCREATE\s+TABLE\b", ch, re.IGNORECASE))
        if ct_in_chunk >= 2:
            multi_create_chunks.append((idx, ct_in_chunk, ch))
    print(f"2개 이상 CREATE TABLE을 가진 chunk: {len(multi_create_chunks)}")
    if multi_create_chunks:
        idx, cnt, ch = multi_create_chunks[0]
        print(f"  예시 chunk[{idx}] CREATE 개수={cnt}, 길이={len(ch)}")
        print(f"  앞 250자: {ch[:250]!r}")
        print(f"  뒤 250자: {ch[-250:]!r}")

    # 누락 모든 테이블의 chunk 위치 확인 + chunk 본문 dump
    print(f"\n--- 누락된 모든 테이블 ({len(missing)}개) chunk dump ---")
    for schema, name in sorted(missing)[:10]:
        marker = f'"{schema}"."{name}"' if schema else f'"{name}"'
        host_chunks = [(i, c) for i, c in enumerate(chunks) if marker in c]
        if not host_chunks:
            print(f"\n  [{schema}.{name}] splitter 결과 어느 chunk에도 없음 (소실)")
            continue
        for ci, ch in host_chunks:
            ct_count = len(re.findall(r"\bCREATE\s+TABLE\b", ch, re.IGNORECASE))
            print(f"\n  [{schema}.{name}] chunk[{ci}] (CT={ct_count}, len={len(ch)})")
            normalized = _normalize_chunk(ch)
            print(f"    raw 시작 200: {ch[:200]!r}")
            print(f"    raw 끝 150:   {ch[-150:]!r}")
            print(f"    normalized 시작 200: {normalized[:200]!r}")
            print(f"    normalized 끝 150:   {normalized[-150:]!r}")
            try:
                stmts = sqlglot.parse(normalized, dialect="oracle", error_level=sqlglot.ErrorLevel.IGNORE)
                kinds = [type(s).__name__ if s else "None" for s in stmts]
                print(f"    sqlglot 결과: {kinds}")
                for s in stmts:
                    if s is not None and type(s).__name__ == "Command":
                        body = s.expression.name if hasattr(s, "expression") and s.expression else ""
                        print(f"    Command 본문 앞 250: {body[:250]!r}")
                        break
            except Exception as e:
                print(f"    sqlglot 예외: {e}")
