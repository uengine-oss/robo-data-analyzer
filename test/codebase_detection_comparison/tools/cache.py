"""분석 결과 캐시 — skills/auto_generated 아래 JSON 저장.

키는 프로젝트 지문(확장자 top3 + 매니페스트 이름 + import top5) 의 SHA-1.
동일 프로젝트 재실행 시 즉시 히트.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

CACHE_DIR = Path(__file__).resolve().parent.parent / "skills" / "auto_generated"


def _ensure_dir() -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def cache_key_from_info(info: Any) -> str:
    data = asdict(info) if is_dataclass(info) else dict(info)
    ext_top3 = sorted(
        (data.get("extensions") or {}).items(),
        key=lambda kv: (-kv[1], kv[0]),
    )[:3]
    imp_top5 = sorted(
        (data.get("import_frequency") or {}).items(),
        key=lambda kv: (-kv[1], kv[0]),
    )[:5]
    fingerprint = {
        "ext": ext_top3,
        "manifest": data.get("manifest_name", ""),
        "imports": imp_top5,
    }
    payload = json.dumps(fingerprint, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def read_cache(key: str) -> Optional[dict]:
    path = CACHE_DIR / f"{key}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def write_cache(key: str, summary: str, meta: Optional[dict] = None) -> str:
    _ensure_dir()
    payload = {
        "key": key,
        "summary": summary,
        "meta": meta or {},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    path = CACHE_DIR / f"{key}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)
