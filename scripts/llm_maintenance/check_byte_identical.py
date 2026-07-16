"""byte-identical CI 체크 — 시스템 프롬프트 / yaml SHA256 비교.

캐시 깨짐 방지의 보호장치. ``llm/prompts/`` 규칙과 ``llm/agents/`` system prompt의
.md/.yaml 변경 시 CI 빌드 실패.

사용::

    # 최초 baseline 기록
    python scripts/llm_maintenance/check_byte_identical.py --update

    # CI 에서 검증
    python scripts/llm_maintenance/check_byte_identical.py
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


_ROOT = Path(__file__).resolve().parent.parent.parent
PROMPTS_DIR = _ROOT / "llm" / "prompts"
AGENTS_DIR = _ROOT / "llm" / "agents"
HASHES_PATH = PROMPTS_DIR / "prompt_hashes.json"
PROMPT_ROOTS = (("prompts", PROMPTS_DIR), ("agents", AGENTS_DIR))


def compute_hashes() -> dict[str, str]:
    """규칙 prompt와 agent system prompt의 SHA256을 경로와 함께 계산한다."""
    hashes: dict[str, str] = {}
    for owner, root in PROMPT_ROOTS:
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            if path.suffix.lower() not in (".md", ".yaml", ".yml"):
                continue
            rel = str(path.relative_to(root)).replace("\\", "/")
            hashes[f"{owner}/{rel}"] = hashlib.sha256(path.read_bytes()).hexdigest()
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
            failed.append(f"{rel} (new - add to baseline)")
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
