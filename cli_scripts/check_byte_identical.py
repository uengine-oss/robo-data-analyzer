"""byte-identical CI 체크 — 시스템 프롬프트 / yaml SHA256 비교.

캐시 깨짐 방지의 보호장치. ``prompts/`` 안 .md / .yaml 변경 시 CI 빌드 실패.

사용::

    # 최초 baseline 기록
    python cli_scripts/check_byte_identical.py --update

    # CI 에서 검증
    python cli_scripts/check_byte_identical.py
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


_ROOT = Path(__file__).resolve().parent.parent
PROMPTS_DIR = _ROOT / "prompts"
HASHES_PATH = _ROOT / "cli_scripts" / "prompt_hashes.json"


def compute_hashes() -> dict[str, str]:
    """``prompts/`` 안 모든 ``.md`` / ``.yaml`` / ``.yml`` 파일의 SHA256."""
    hashes: dict[str, str] = {}
    for p in sorted(PROMPTS_DIR.rglob("*")):
        if not p.is_file():
            continue
        if p.suffix.lower() not in (".md", ".yaml", ".yml"):
            continue
        rel = str(p.relative_to(PROMPTS_DIR)).replace("\\", "/")
        hashes[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return hashes


def main(update: bool = False) -> int:
    current = compute_hashes()

    if update or not HASHES_PATH.exists():
        HASHES_PATH.write_text(
            json.dumps(current, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"Wrote baseline {len(current)} hashes to {HASHES_PATH}")
        return 0

    expected = json.loads(HASHES_PATH.read_text(encoding="utf-8"))
    failed: list[str] = []

    for rel, h in current.items():
        if expected.get(rel) is None:
            failed.append(f"{rel} (new — add to baseline)")
        elif expected[rel] != h:
            failed.append(f"{rel} (modified)")
    for rel in expected:
        if rel not in current:
            failed.append(f"{rel} (deleted)")

    if failed:
        print("BYTE-IDENTICAL CHECK FAILED:")
        for rel in failed:
            print(f"  - {rel}")
        print("\nIf intentional, run with --update to refresh baseline.")
        return 1

    print(f"OK - {len(current)} prompts byte-identical")
    return 0


if __name__ == "__main__":
    update = "--update" in sys.argv
    sys.exit(main(update=update))
