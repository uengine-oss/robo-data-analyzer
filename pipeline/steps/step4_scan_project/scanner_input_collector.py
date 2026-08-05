"""scanner_input_collector — Scan Agent 입력 단서 수집.

매니페스트 + 확장자 빈도 + 카테고리별 파일 목록 수집.

Plan §4 M2 명세:
- ``extensions_count``: 상위 20개
- ``manifest_content``: 우선순위 1위 매니페스트의 앞 60줄
- ``scanner_categories``: 카테고리당 최대 50개

★ 제거: ``representative_file`` (자바 식 가정 — 범용 X. Agent 가 도구로 직접 탐색)
★ 제거: 코드노드 시그니처 인덱스 / Import 빈도 (Agent 가 ``list_code_nodes`` / ``grep`` 로 직접 봄)
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path

from pipeline.languages import scanner_hints


def collect_scanner_input(project_root: Path) -> dict:
    """프로젝트 디렉토리 → Scan Agent 입력 단서.

    Returns:
        {
            "project_root": str,
            "extensions_count": {ext: count},          # 상위 20개
            "manifest_content": "...앞 60줄",
            "scanner_categories": {cat: [path, ...]},  # 카테고리당 최대 50개
        }
    """
    ext_counter: Counter = Counter()
    manifests, category_extensions = scanner_hints()
    categories: dict[str, list[str]] = {k: [] for k in category_extensions}

    for f in sorted(project_root.rglob("*")):
        if not f.is_file():
            continue
        ext = f.suffix.lower()
        if ext:
            ext_counter[ext.lstrip(".")] += 1
        for cat, exts in category_extensions.items():
            if ext in exts and len(categories[cat]) < 50:
                categories[cat].append(str(f.relative_to(project_root)))

    # 매니페스트 앞 60줄
    manifest_content = ""
    for m in manifests:
        m_path = project_root / m
        if m_path.is_file():
            lines = m_path.read_text(encoding="utf-8").splitlines()
            manifest_content = "\n".join(lines[:60])
            break

    return {
        "project_root": str(project_root),
        "extensions_count": dict(ext_counter.most_common(20)),
        "manifest_content": manifest_content,
        "scanner_categories": categories,
    }
