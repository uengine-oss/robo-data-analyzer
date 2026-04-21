"""L1 (exact) + L2 (구조 signature) 캐시 — sqlite 기반

L1: SHA256(전체 파일) → 파싱 결과 (같은 파일 재실행)
L2: 구조 특징 hash → 파서 코드 (같은 포맷의 다른 파일에 재사용)

중요: L2 signature 는 내용 hash 가 아니라 **구조 특징** hash.
     같은 구조의 다른 파일이 같은 signature 로 매칭되도록 설계.
"""

import hashlib
import json
import sqlite3
from pathlib import Path


CACHE_DB = "c:/tmp/universal_parser_cache.db"


def init_cache():
    """캐시 DB 및 테이블 초기화."""
    Path(CACHE_DB).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(CACHE_DB) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS l1_exact (
                file_hash TEXT PRIMARY KEY,
                result_json TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS l2_signature (
                signature TEXT PRIMARY KEY,
                parser_code TEXT NOT NULL,
                description TEXT,
                success_count INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)


def reset_cache():
    """캐시 파일 삭제 (데모 시작 시 호출)."""
    p = Path(CACHE_DB)
    if p.exists():
        p.unlink()
    init_cache()


# =============================================================================
# L1 — 파일 전체 hash 기반 exact match
# =============================================================================

def l1_key(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def l1_get(data: bytes):
    try:
        with sqlite3.connect(CACHE_DB) as conn:
            row = conn.execute(
                "SELECT result_json FROM l1_exact WHERE file_hash = ?",
                (l1_key(data),)
            ).fetchone()
        return json.loads(row[0]) if row else None
    except Exception:
        return None


def l1_put(data: bytes, result):
    try:
        with sqlite3.connect(CACHE_DB) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO l1_exact (file_hash, result_json) VALUES (?, ?)",
                (l1_key(data), json.dumps(result, ensure_ascii=False, default=str))
            )
    except Exception:
        pass


# =============================================================================
# L2 — 구조 특징 hash 기반 (같은 포맷 다른 파일 매칭)
# =============================================================================

def l2_signature(data: bytes) -> str:
    """구조적 특징만 추출해 hash.

    같은 포맷의 다른 파일이 같은 signature 를 갖도록 설계되었습니다.
    내용 자체가 아닌 "구조의 모양"을 지문화.
    """
    try:
        text = data[:4096].decode('utf-8', errors='replace')
    except Exception:
        text = ""

    lines = text.split('\n')
    non_empty = [l for l in lines if l.strip()]

    if not non_empty:
        return hashlib.md5(b'empty').hexdigest()[:16]

    features = {
        'xml_decl':         text.startswith('<?xml'),
        'json_start':       text.lstrip()[:1] in ('{', '['),
        'yaml_doc':         text.startswith('---'),
        'shebang':          text.startswith('#!'),
        # 특수문자 줄당 빈도 (소숫점 1자리로 버킷)
        'colon_per_line':   round(text.count(':') / max(len(non_empty), 1), 1),
        'equals_per_line':  round(text.count('=') / max(len(non_empty), 1), 1),
        'pipe_per_line':    round(text.count('|') / max(len(non_empty), 1), 1),
        'semicolon_per_line': round(text.count(';') / max(len(non_empty), 1), 1),
        # 구조적 단서
        'has_indent':       any(l.startswith((' ', '\t')) for l in non_empty),
        'has_section_headers': any(
            l.strip().startswith('[') and l.strip().endswith(']')
            for l in non_empty
        ),
        'has_record_markers': any(
            l.strip().split()[0] in ('RECORD', 'BEGIN', 'END')
            for l in non_empty[:20] if l.strip().split()
        ),
    }
    feature_str = json.dumps(features, sort_keys=True)
    return hashlib.md5(feature_str.encode()).hexdigest()[:16]


def l2_get(data: bytes):
    """구조 매칭으로 parser 코드 조회."""
    try:
        with sqlite3.connect(CACHE_DB) as conn:
            row = conn.execute(
                "SELECT parser_code FROM l2_signature WHERE signature = ?",
                (l2_signature(data),)
            ).fetchone()
        return row[0] if row else None
    except Exception:
        return None


def l2_put(data: bytes, parser_code: str, description: str = ""):
    try:
        with sqlite3.connect(CACHE_DB) as conn:
            conn.execute("""
                INSERT INTO l2_signature (signature, parser_code, description)
                VALUES (?, ?, ?)
                ON CONFLICT(signature) DO UPDATE SET
                    success_count = success_count + 1,
                    parser_code = excluded.parser_code
            """, (l2_signature(data), parser_code, description))
    except Exception:
        pass
