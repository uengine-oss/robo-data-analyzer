"""네거티브 매칭 — 독점/커스텀 프레임워크 자동 탐지.

알고리즘:
1. import/include 에서 prefix 추출 (_extract_prefix)
2. rules/known_prefixes.json 의 표준 라이브러리 화이트리스트와 대조
3. 매칭 안 되는 prefix 중 반복 등장(임계값 이상)을 "독점 프레임워크 후보" 로 플래그
4. rules/known_proprietary.json 맵으로 힌트 추가 (pfm → ProFrame)
5. 루트 아래 내부 패키지 폴더는 augment_known_with_local_packages 로 자동 화이트리스트 보강
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


_DEFAULT_THRESHOLDS = {
    "score_min": 2,
    "proprietary_score": 5,
    "proprietary_file_count": 3,
    "max_results": 15,
}


class NegativeMatcher:
    """네거티브 매칭 엔진. rules/*.json 을 로드해 구성."""

    def __init__(self, rules_dir: str | Path):
        rules_dir = Path(rules_dir)

        # 언어별 화이트리스트를 모두 합쳐 하나의 set 로.
        prefixes_raw = json.loads(
            (rules_dir / "known_prefixes.json").read_text(encoding="utf-8")
        )
        self.known_prefixes: set[str] = set()
        for _, plist in prefixes_raw.items():
            self.known_prefixes.update(p.lower() for p in plist)

        # 독점 프레임워크 힌트 맵.
        self.known_proprietary: dict[str, str] = json.loads(
            (rules_dir / "known_proprietary.json").read_text(encoding="utf-8")
        )

        # 임계값 로드.
        thresholds_raw = json.loads(
            (rules_dir / "thresholds.json").read_text(encoding="utf-8")
        )
        nm_cfg = thresholds_raw.get("negative_matcher", {})
        self.score_min = int(nm_cfg.get("score_min", _DEFAULT_THRESHOLDS["score_min"]))
        self.proprietary_score = int(
            nm_cfg.get("proprietary_score", _DEFAULT_THRESHOLDS["proprietary_score"])
        )
        self.proprietary_file_count = int(
            nm_cfg.get(
                "proprietary_file_count", _DEFAULT_THRESHOLDS["proprietary_file_count"]
            )
        )
        self.max_results = int(
            nm_cfg.get("max_results", _DEFAULT_THRESHOLDS["max_results"])
        )

    def augment_with_local_packages(self, root: str | Path) -> None:
        """루트 바로 아래 폴더명을 런타임에 화이트리스트 추가.

        자기 프로젝트 내부 패키지(analyzer, client, config 등)를
        독점 프레임워크로 오탐하는 것 방지.
        """

        root = Path(root)
        if not root.is_dir():
            return
        for sub in root.iterdir():
            if sub.is_dir() and not sub.name.startswith("."):
                self.known_prefixes.add(sub.name.lower())

    def detect_unknown_namespaces(
        self,
        import_frequency: dict[str, int],
        file_imports: dict[str, list[str]] | None = None,
    ) -> list[dict[str, Any]]:
        """import 빈도 맵을 받아 독점 프레임워크 후보 리스트 반환.

        각 후보: {prefix, total_occurrences, file_count, is_likely_proprietary, evidence}
        """

        prefix_scores: Counter[str] = Counter()
        prefix_files: dict[str, set[str]] = {}

        # file_imports 가 있으면 파일별 prefix 집합도 수집 (file_count 계산용).
        if file_imports:
            for file_path, imports in file_imports.items():
                seen: set[str] = set()
                for imp in imports:
                    p = _extract_prefix(imp)
                    if p and p.lower() not in self.known_prefixes:
                        seen.add(p)
                for p in seen:
                    prefix_files.setdefault(p, set()).add(file_path)

        for imp, count in import_frequency.items():
            prefix = _extract_prefix(imp)
            if not prefix:
                continue
            if prefix.lower() in self.known_prefixes:
                continue
            prefix_scores[prefix] += count

        results: list[dict[str, Any]] = []
        for prefix, score in prefix_scores.most_common(self.max_results):
            if score < self.score_min:
                continue
            file_count = len(prefix_files.get(prefix, []))
            is_proprietary = (
                score >= self.proprietary_score or file_count >= self.proprietary_file_count
            )
            evidence = self._gather_evidence(prefix)
            results.append(
                {
                    "prefix": prefix,
                    "total_occurrences": score,
                    "file_count": file_count,
                    "is_likely_proprietary": is_proprietary,
                    "evidence": evidence,
                }
            )
        return results

    def _gather_evidence(self, prefix: str) -> list[str]:
        """알려진 독점 맵에서 힌트 수집."""

        evidence: list[str] = []
        lower = prefix.lower()
        if lower in self.known_proprietary:
            evidence.append(f"알려진 독점 프레임워크: {self.known_proprietary[lower]}")
        # 부분 매칭(lower 가 known key 로 시작하는 경우): 느슨한 힌트.
        for known_key, desc in self.known_proprietary.items():
            if known_key != lower and lower.startswith(known_key):
                evidence.append(f"추정 관련: {desc}")
                break
        return evidence


def _extract_prefix(import_str: str) -> str | None:
    """import/include 문자열에서 최상위 prefix 추출.

    지원 형식:
    - C:       '#include <pfmcom.h>'      → 'pfm'  (파일명에서 숫자 전까지의 알파벳)
    - C:       '#include <pdb_order_s1.h>' → 'pdb'  (_ 앞부분)
    - Python:  'django.db'                 → 'django'
    - Java:    'org.springframework.boot'  → 'org.springframework'
    - Go:      'github.com/gin-gonic/gin'  → 'github.com/gin-gonic'
    - TS:      '@nestjs/common'            → '@nestjs'
    - TS:      './relative'                → None (상대 경로 제외)
    """

    if not import_str:
        return None

    s = import_str.strip()
    if s.startswith("./") or s.startswith("../"):
        return None

    # C: #include <name.h> 형식이면 name 만 추출.
    m = re.match(r'[#\s]*include\s*[<"]([^>"]+)[>"]', s)
    if m:
        name = m.group(1)
        # 디렉토리가 있으면 첫 디렉토리를 prefix 로.
        if "/" in name:
            return name.split("/", 1)[0]
        # 파일명에서 확장자 제거.
        name = re.sub(r"\.\w+$", "", name)
        # '_' 가 있으면 첫 토큰이 의미 있는 prefix.
        if "_" in name:
            return name.split("_", 1)[0]
        # 영문+숫자 혼합이면 앞의 알파벳 부분만.
        alpha_match = re.match(r"^([A-Za-z]+)", name)
        if alpha_match:
            alpha = alpha_match.group(1)
            # 길면 앞 3글자로 축약 (pfmcom → pfm).
            if len(alpha) > 5:
                return alpha[:3].lower()
            return alpha.lower()
        return name.lower()

    # TS/JS 스코프 패키지: @nestjs/common → @nestjs.
    if s.startswith("@") and "/" in s:
        return s.split("/", 1)[0]

    # Go: github.com/gin-gonic/gin → github.com/gin-gonic.
    if s.startswith("github.com/") or s.startswith("gitlab.com/"):
        parts = s.split("/")
        if len(parts) >= 3:
            return "/".join(parts[:3])
        return parts[0]

    # 점 표기: 맨 앞 두 토큰 (java/python).
    if "." in s:
        parts = s.split(".")
        if len(parts) >= 2 and parts[0] in {"org", "io", "com", "net"}:
            return ".".join(parts[:2])
        return parts[0]

    return s
