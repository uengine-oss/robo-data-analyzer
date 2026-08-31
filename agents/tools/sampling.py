"""External Format Agent 의 sample 탐색 도구.

미리보기(프롬프트 발췌)는 파일의 앞부분일 뿐이다. 큰 파일·뒤에서 구조가 바뀌는
파일을 다루려면 에이전트가 전체를 능동적으로 탐색할 수 있어야 한다:

- ``sample_overview``   — 파일별 크기·줄 모양 분포 (구조가 몇 종류인지, 어디서 바뀌는지)
- ``read_sample_range`` — 임의 구간 정독 (미리보기 밖 구조 확인)
- ``grep_samples``      — 토큰/패턴이 전체 어디에 나오는지

세 도구 모두 **파일 전문**을 대상으로 한다 (미리보기 절단과 무관).
"""
from __future__ import annotations

import re
from collections import Counter

import regex
from langchain.tools import tool

_OVERVIEW_PAGE_SIZE = 20
_MAX_RANGE_LINES = 50
_MAX_GREP_HITS = 20
_SHAPE_WIDTH = 48
_TOP_SHAPES = 6


def _effective_grep_timeout(text_length: int) -> float:
    """Allow one linear full-text pass without a fixed wall-clock ceiling."""
    return 0.1 + text_length * 1e-6


def _shape(line: str) -> str:
    """줄의 '골격' — 문자/숫자 뭉치를 A/9 로 접어 구조만 남긴다."""
    folded = re.sub(r"\d+", "9", line)
    folded = re.sub(r"[A-Za-z가-힣_]+", "A", folded)
    folded = re.sub(r"\s+", " ", folded).strip()
    return folded[:_SHAPE_WIDTH] or "(빈 줄)"


def make_sample_exploration_tools(samples: list[str]):
    """samples(전문)를 묶은 탐색 도구 3종을 만든다."""
    full = list(samples)
    line_lists = [text.splitlines() for text in full]

    @tool
    def sample_overview(offset: int = 0) -> str:
        """파일별 크기와 '줄 모양' 분포를 요약한다.

        **언제 사용**: 첫 관찰 단계에서 1회 — 구조가 몇 종류인지, 미리보기 밖
        (파일 뒤쪽)에 다른 구조가 있는지를 여기서 판단한다.

        **결과 읽는 법**: 모양(A=문자 뭉치, 9=숫자 뭉치)별 줄 수와 최초/최후 등장
        줄 번호. 미리보기에서 못 본 모양이 있으면 그 줄 번호를 `read_sample_range`
        로 정독하라.
        """
        if offset < 0:
            return "INVALID_OFFSET: offset must be non-negative"
        end = min(offset + _OVERVIEW_PAGE_SIZE, len(line_lists))
        next_offset = end if end < len(line_lists) else None
        lines_out: list[str] = [
            f"PAGE total={len(line_lists)} "
            f"next_offset={next_offset if next_offset is not None else 'null'}"
        ]
        for index in range(offset, end):
            lines = line_lists[index]
            lines_out.append(
                f"sample[{index}]: {len(full[index]):,}자 · {len(lines):,}줄")
            counts: Counter[str] = Counter(_shape(line) for line in lines)
            first_last: dict[str, tuple[int, int]] = {}
            for number, line in enumerate(lines, 1):
                shape = _shape(line)
                start, _ = first_last.get(shape, (number, number))
                first_last[shape] = (start, number)
            for shape, count in counts.most_common(_TOP_SHAPES):
                start, end = first_last[shape]
                lines_out.append(
                    f"  {count:>5}줄  모양 '{shape}'  (첫 {start}줄 ~ 끝 {end}줄)")
            if len(counts) > _TOP_SHAPES:
                lines_out.append(f"  … 그 외 모양 {len(counts) - _TOP_SHAPES}종")
        return "\n".join(lines_out)

    @tool
    def read_sample_range(file_index: int, start_line: int) -> str:
        """sample 파일의 임의 구간을 정독한다 (start_line 부터 50줄).

        **언제 사용**: overview 가 보여준 미리보기 밖 구조, verify 결과의
        '이후 매치 없음' 구간을 직접 확인할 때.
        """
        if not 0 <= file_index < len(line_lists):
            return f"INVALID: file_index 는 0~{len(line_lists) - 1}"
        lines = line_lists[file_index]
        if not lines:
            return "빈 파일"
        start = max(1, min(int(start_line), len(lines)))
        window = lines[start - 1:start - 1 + _MAX_RANGE_LINES]
        body = "\n".join(f"{start + offset:>6}| {line}" for offset, line in enumerate(window))
        last = start + len(window) - 1
        next_start = last + 1 if last < len(lines) else None
        return (
            f"sample[{file_index}] total_lines={len(lines)} "
            f"next_start_line={next_start if next_start is not None else 'null'}\n{body}"
        )

    @tool
    def grep_samples(pattern: str, offset: int = 0) -> str:
        """정규식/토큰이 sample 전체 어디에 나오는지 찾는다 (파일·줄 번호 목록).

        **언제 사용**: 특정 토큰(구분자·헤더 후보)이 파일 전반에 고르게 나오는지,
        앞부분에만 있는지 확인할 때.
        """
        try:
            compiled = regex.compile(pattern, regex.MULTILINE)
        except regex.error as error:
            return f"INVALID_REGEX: {error}"
        if offset < 0:
            return "INVALID_OFFSET: offset must be non-negative"
        hits: list[str] = []
        for index, text in enumerate(full):
            try:
                for match in compiled.finditer(
                    text, timeout=_effective_grep_timeout(len(text)),
                ):
                    line_number = text.count("\n", 0, match.start()) + 1
                    excerpt = match.group(0).replace("\n", "\\n")[:80]
                    hits.append(f"  sample[{index}] {line_number}줄: '{excerpt}'")
            except TimeoutError:
                return "TIMEOUT: 패턴이 너무 느리다 — 더 구체적으로"
        if not hits:
            return "매치 0건"
        page = hits[offset:offset + _MAX_GREP_HITS]
        next_offset = offset + len(page) if offset + len(page) < len(hits) else None
        head = (
            f"PAGE total={len(hits)} "
            f"next_offset={next_offset if next_offset is not None else 'null'}"
        )
        return head + "\n" + "\n".join(page)

    return [sample_overview, read_sample_range, grep_samples]
