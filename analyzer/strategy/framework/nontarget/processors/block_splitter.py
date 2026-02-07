"""Pass 2 규칙 엔진 - 결정적(deterministic) 블록 분리

ChunkerAgent(Pass 1)가 학습한 규칙/경계후보를 받아서
시스템이 파일을 한 번 더 스캔하며 최종 블록을 분리합니다.

핵심 원칙:
- 시스템은 "판단"하지 않음 — LLM이 학습한 규칙을 "그대로 적용"만 함
- 500줄 = 분석 윈도우일 뿐, 블록 크기와 무관
- 최종 블록은 의미 경계로 결정됨
"""

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from analyzer.strategy.framework.nontarget.utils.nontarget_logger import log_nontarget


# ============================================================================
# 데이터 구조
# ============================================================================

@dataclass
class ChunkingRule:
    """ChunkerAgent가 학습한 분리 규칙"""
    rule_id: str
    pattern: str                    # 정규식 또는 문자열 패턴
    rule_type: str                  # header | delimiter | indent | key_value | block_end
    priority: int = 5              # 1(낮음) ~ 10(높음)
    confidence: float = 0.5        # 0.0 ~ 1.0
    examples: List[str] = field(default_factory=list)

    def compile(self) -> Optional[re.Pattern]:
        """정규식 컴파일 (실패 시 None)"""
        try:
            return re.compile(self.pattern)
        except re.error:
            return None


@dataclass
class BoundaryCandidate:
    """경계 후보"""
    line_no: int
    reason: str
    rule_id: str
    confidence: float = 0.5


@dataclass
class OpenBlockState:
    """아직 닫히지 않은 블록 상태"""
    start_line: int = 0
    expected_end_pattern: str = ""
    depth: int = 0


@dataclass
class ChunkerContext:
    """Pass 1에서 축적된 전체 컨텍스트"""
    rules: List[ChunkingRule] = field(default_factory=list)
    boundaries: List[BoundaryCandidate] = field(default_factory=list)
    open_block: Optional[OpenBlockState] = None

    def to_dict(self) -> Dict[str, Any]:
        """JSON 직렬화용"""
        return {
            "rules": [
                {
                    "rule_id": r.rule_id,
                    "pattern": r.pattern,
                    "rule_type": r.rule_type,
                    "priority": r.priority,
                    "confidence": r.confidence,
                    "examples": r.examples,
                }
                for r in self.rules
            ],
            "boundaries": [
                {
                    "line_no": b.line_no,
                    "reason": b.reason,
                    "rule_id": b.rule_id,
                    "confidence": b.confidence,
                }
                for b in self.boundaries
            ],
            "open_block": (
                {
                    "start_line": self.open_block.start_line,
                    "expected_end_pattern": self.open_block.expected_end_pattern,
                    "depth": self.open_block.depth,
                }
                if self.open_block
                else None
            ),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ChunkerContext":
        """JSON 역직렬화"""
        rules = [
            ChunkingRule(**r) for r in data.get("rules", [])
        ]
        boundaries = [
            BoundaryCandidate(**b) for b in data.get("boundaries", [])
        ]
        ob = data.get("open_block")
        open_block = OpenBlockState(**ob) if ob else None
        return cls(rules=rules, boundaries=boundaries, open_block=open_block)


@dataclass
class BlockJSON:
    """최종 블록 결과"""
    name: str
    start_line: int
    end_line: int
    node_code: str
    role_hint: str = ""
    file_path: str = ""
    file_name: str = ""
    file_ext: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "node_code": self.node_code,
            "role_hint": self.role_hint,
            "file_path": self.file_path,
            "file_name": self.file_name,
            "file_ext": self.file_ext,
        }


# ============================================================================
# 파일 읽기 유틸리티
# ============================================================================

def read_file_with_line_numbers(file_path: str) -> Tuple[List[str], str]:
    """파일을 읽고 라인번호를 부착합니다.

    Returns:
        (raw_lines, numbered_text)
        raw_lines: 원본 라인 리스트 (번호 없음)
        numbered_text: "1: 내용\\n2: 내용\\n..." 형식
    """
    with open(file_path, "r", encoding="utf-8") as f:
        raw_lines = f.readlines()

    numbered_lines = [
        f"{i + 1}: {line.rstrip()}" for i, line in enumerate(raw_lines)
    ]
    numbered_text = "\n".join(numbered_lines)
    return raw_lines, numbered_text


def make_overlap_windows(
    raw_lines: List[str],
    window_size: int = 500,
    overlap: int = 50,
) -> List[Dict[str, Any]]:
    """오버랩 윈도우로 분할합니다.

    Args:
        raw_lines: 원본 라인 리스트
        window_size: 윈도우 크기 (줄 수)
        overlap: 겹치는 줄 수

    Returns:
        [{"window_idx": 0, "start_line": 1, "end_line": 500, "text": "1: ...\\n2: ..."}]
    """
    total = len(raw_lines)
    if total == 0:
        return []

    windows: List[Dict[str, Any]] = []
    step = window_size - overlap
    idx = 0
    start = 0

    while start < total:
        end = min(start + window_size, total)
        numbered = "\n".join(
            f"{start + i + 1}: {raw_lines[start + i].rstrip()}"
            for i in range(end - start)
        )
        windows.append({
            "window_idx": idx,
            "start_line": start + 1,
            "end_line": end,
            "text": numbered,
            "is_last": end >= total,
        })
        if end >= total:
            break
        start += step
        idx += 1

    log_nontarget(
        "SPLITTER",
        f"오버랩 윈도우 생성: {len(windows)}개 (window={window_size}, overlap={overlap}, total={total}줄)",
    )
    return windows


# ============================================================================
# Pass 2: 결정적 블록 분리
# ============================================================================

def split_blocks(
    file_path: str,
    raw_lines: List[str],
    ctx: ChunkerContext,
) -> List[BlockJSON]:
    """축적된 규칙과 경계 후보를 적용해 최종 블록을 생성합니다.

    시스템은 판단하지 않음 — ChunkerAgent 규칙을 그대로 적용.

    Args:
        file_path: 파일 경로
        raw_lines: 원본 라인 리스트
        ctx: Pass 1에서 축적된 ChunkerContext

    Returns:
        최종 BlockJSON 리스트
    """
    import os

    total_lines = len(raw_lines)
    if total_lines == 0:
        return []

    file_name = os.path.basename(file_path)
    file_ext = os.path.splitext(file_name)[1].lower()

    # 1. 경계 확정 — confidence 순 + line_no 순 정렬
    confirmed_boundaries = _confirm_boundaries(raw_lines, ctx)

    # 2. 경계가 없으면 규칙 기반 스캔으로 경계 생성
    if not confirmed_boundaries:
        confirmed_boundaries = _scan_with_rules(raw_lines, ctx.rules)

    # 3. 경계가 여전히 없으면 전체 파일 = 1블록
    if not confirmed_boundaries:
        confirmed_boundaries = [1]  # 파일 시작만

    # 4. 경계 라인 → 블록 범위 변환
    boundaries = sorted(set(confirmed_boundaries))
    # 첫 번째 경계가 1이 아니면 추가
    if boundaries[0] != 1:
        boundaries.insert(0, 1)

    blocks: List[BlockJSON] = []
    for i, start in enumerate(boundaries):
        end = boundaries[i + 1] - 1 if i + 1 < len(boundaries) else total_lines
        if end < start:
            continue

        # 라인번호 포함 node_code 생성
        node_code = "\n".join(
            f"{start + j}: {raw_lines[start + j - 1].rstrip()}"
            for j in range(end - start + 1)
        )

        block_name = f"{file_name}:L{start}-{end}"
        blocks.append(BlockJSON(
            name=block_name,
            start_line=start,
            end_line=end,
            node_code=node_code,
            role_hint="",  # Summarizer가 채울 예정
            file_path=file_path,
            file_name=file_name,
            file_ext=file_ext,
        ))

    log_nontarget(
        "SPLITTER",
        f"블록 분리 완료: {file_path} → {len(blocks)}개 블록 "
        f"(규칙 {len(ctx.rules)}개, 경계후보 {len(ctx.boundaries)}개)",
    )
    return blocks


def _confirm_boundaries(
    raw_lines: List[str],
    ctx: ChunkerContext,
) -> List[int]:
    """경계 후보를 confidence 기준으로 확정합니다."""
    if not ctx.boundaries:
        return []

    # confidence >= 0.5 인 후보만 사용
    strong = [b for b in ctx.boundaries if b.confidence >= 0.5]
    if not strong:
        # confidence가 전부 낮으면 상위 50%만
        sorted_by_conf = sorted(ctx.boundaries, key=lambda b: b.confidence, reverse=True)
        half = max(1, len(sorted_by_conf) // 2)
        strong = sorted_by_conf[:half]

    # 라인번호 유효성 검증
    total = len(raw_lines)
    valid = [b.line_no for b in strong if 1 <= b.line_no <= total]

    return sorted(set(valid))


def _scan_with_rules(
    raw_lines: List[str],
    rules: List[ChunkingRule],
) -> List[int]:
    """규칙을 파일에 적용하여 경계를 생성합니다."""
    if not rules:
        return []

    # confidence × priority 기준 정렬 (높은 것 우선)
    sorted_rules = sorted(
        rules,
        key=lambda r: r.confidence * r.priority,
        reverse=True,
    )

    boundaries: List[int] = [1]  # 파일 시작

    compiled_rules = []
    for rule in sorted_rules:
        pat = rule.compile()
        if pat:
            compiled_rules.append((rule, pat))

    if not compiled_rules:
        return boundaries

    for i, line in enumerate(raw_lines):
        line_no = i + 1
        stripped = line.rstrip()

        for rule, pat in compiled_rules:
            if pat.search(stripped):
                # 같은 위치에 이미 경계가 있으면 스킵
                if not boundaries or abs(boundaries[-1] - line_no) > 2:
                    boundaries.append(line_no)
                break  # 한 줄에 하나의 규칙만 적용

    return sorted(set(boundaries))

