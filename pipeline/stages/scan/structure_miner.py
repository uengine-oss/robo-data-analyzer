"""미지 텍스트의 결정론 구조 마이너.

Drain(고정 깊이 파스 트리, He 2017)의 원리 — "줄을 토큰 클래스 시퀀스로 접어
군집화" — 를 offset 보존 방식으로 자체 구현한다. 기성 라이브러리(Drain3 등)는
strip/재토큰화로 원문 span 을 잃어 우리 불변식과 양립하지 않는다(연구 §1).

책임 분담: 이 모듈은 **후보 규칙 합성까지만** 한다. 의미 판정·명명·채택은 LLM
Agent 가, 승인은 결정론 검증기(held-out 포함)가 한다. 여기서 방출되는 모든 후보는
production 엔진(``probe_extraction_rule``)으로 자가 검증을 통과한 것만이다.

군집 원리:
- 줄 → 토큰 클래스 시퀀스 (W=문자/한글/_ 뭉치, N=숫자 뭉치, S=공백, 구두점=리터럴)
- 같은 시퀀스 = 같은 구조 후보. 위치별 값이 전부 같으면 상수(리터럴), 다르면 변수.
- key = 가장 이른 "식별자다운 유일 변수 run" (연속 변수 토큰 병합) — 상수 접두어가
  key 에 섞이는 오류가 구조적으로 불가능하다.
- 블록: 여는 군집과 닫는 군집이 엄격 교대하면 여는줄~닫는줄, 반복 헤더만 있으면
  헤더~다음 헤더 직전. 어느 것도 확신 못 하면 후보를 내지 않는다(fail-closed).
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import re

from pipeline.stages.external.parsers.span_common import is_reference_key
from pipeline.stages.scan.external_skills import (
    ExternalSkillValidationError,
    probe_extraction_rule,
)


_MIN_CLUSTER_LINES = 3
_MIN_KEY_UNIQUENESS = 0.8

_W = "W"                                     # 문자/한글/밑줄 뭉치
_N = "N"                                     # 숫자 뭉치
_S = "S"                                     # 공백 뭉치

_TOKEN_RE = re.compile(r"[A-Za-z가-힣_]+|\d+|[ \t]+|.", re.DOTALL)
_W_RE = re.compile(r"[A-Za-z가-힣_]+")
_N_RE = re.compile(r"\d+")


@dataclass(frozen=True)
class RuleCandidate:
    pattern: str
    key_group: str
    kind: str                                # "line_record" | "block" | "section"
    evidence_lines: int                      # 근거가 된 줄/블록 수
    sample_keys: tuple[str, ...]
    note: str


def _tokenize(line: str) -> list[tuple[str, str]]:
    """줄 → [(클래스, 원문토큰)]. 클래스는 분류용일 뿐 원문은 그대로 보존."""
    output: list[tuple[str, str]] = []
    for token in _TOKEN_RE.findall(line):
        if _W_RE.fullmatch(token):
            output.append((_W, token))
        elif _N_RE.fullmatch(token):
            output.append((_N, token))
        elif token.strip(" \t") == "":
            output.append((_S, token))
        else:
            output.append((token, token))    # 구두점 1문자: 클래스=자기 자신
    return output


def _signature(tokens: list[tuple[str, str]]) -> tuple[str, ...]:
    return tuple(cls for cls, _ in tokens)


def _piece(cls: str) -> str:
    if cls == _W:
        return "[A-Za-z가-힣_]+"
    if cls == _N:
        return r"\d+"
    if cls == _S:
        return "[ \\t]+"
    return re.escape(cls)


def _synthesize_line_patterns(
    rows: list[list[tuple[str, str]]],
) -> list[tuple[str, tuple[str, ...], bool]]:
    """같은 시퀀스의 줄들 → [(패턴, 표본 key, 넓힌 변형 여부)]. key 후보 없으면 []."""
    signature = _signature(rows[0])
    columns = list(zip(*rows))               # 위치별 (클래스, 값) 목록

    constant: list[bool] = []
    for position, cls in enumerate(signature):
        values = {value for _, value in columns[position]}
        constant.append(len(values) == 1 and cls not in (_S,))

    # key = 가장 이른 "연속 변수(W/N) run" 중 유일성·식별자성 충족 run
    key_span: tuple[int, int] | None = None
    position = 0
    while position < len(signature):
        if signature[position] in (_W, _N) and not constant[position]:
            end = position
            while (
                end + 1 < len(signature)
                and signature[end + 1] in (_W, _N)
                and not constant[end + 1]
            ):
                end += 1
            values = ["".join(row[i][1] for i in range(position, end + 1)) for row in rows]
            unique_ratio = len(set(values)) / len(values)
            if unique_ratio >= _MIN_KEY_UNIQUENESS and all(is_reference_key(v) for v in values):
                key_span = (position, end)
                break
            position = end + 1
        else:
            position += 1
    if key_span is None:
        return []

    def build(span: tuple[int, int]) -> tuple[str, tuple[str, ...]]:
        pieces: list[str] = []
        for position, cls in enumerate(signature):
            if position == span[0]:
                inner = "".join(_piece(signature[i]) for i in range(span[0], span[1] + 1))
                pieces.append(f"(?P<key>{inner})")
            elif span[0] < position <= span[1]:
                continue
            elif constant[position]:
                pieces.append(re.escape(columns[position][0][1]))
            else:
                pieces.append(_piece(cls))
        keys = tuple(dict.fromkeys(
            "".join(row[i][1] for i in range(span[0], span[1] + 1)) for row in rows
        ))[:5]
        return "^" + "".join(pieces) + "\\r?$", keys

    variants: list[tuple[str, tuple[str, ...], bool]] = [(*build(key_span), False)]
    widened = _widened_key_span(signature, constant, key_span)
    if widened is not None:
        variants.append((*build(widened), True))
    return variants


def _widened_key_span(
    signature: tuple[str, ...], constant: list[bool], key_span: tuple[int, int],
) -> tuple[int, int] | None:
    """key 직전에 붙은 같은 부류(W/N) '상수' 토큰을 key 로 흡수한 변형 span.

    train 파일이 하나뿐이면 'GRD01' 의 GRD 가 상수인지(공통 접두) 이 파일만의
    우연인지 증거로 구분할 수 없다. 넓힌 변형을 **대안 후보로 함께** 내면
    채택은 Agent 가, 최종 판정은 held-out 이 한다 — 추측을 굽지 않는 일반화.
    """
    start = key_span[0]
    if start == 0:
        return None
    if signature[start - 1] in (_W, _N) and constant[start - 1]:
        widened = start - 1
        while widened > 0 and signature[widened - 1] in (_W, _N) and constant[widened - 1]:
            widened -= 1
        return (widened, key_span[1])
    return None


def _verified(candidate: RuleCandidate, samples: list[str]) -> bool:
    """production 엔진으로 자가 검증 — 전 sample 합산 매치가 근거 수 이상이어야 방출."""
    total = 0
    try:
        for sample in samples:
            total += len(probe_extraction_rule(candidate.pattern, "key", sample))
    except ExternalSkillValidationError:
        return False
    return total >= max(2, candidate.evidence_lines // 2)


def line_shape_profile(text: str, min_count: int | None = None) -> frozenset[tuple[str, ...]]:
    """파일의 지배적 줄 모양(토큰 클래스 시퀀스) 집합.

    "같은 확장자 = 같은 형식" 가정을 내용 증거로 대체하기 위한 그룹핑·미끼 선정
    재료다. 잡음 기준은 비율 기반이다: 작은 파일(비공백 9줄 미만)은 모든
    줄이 유의미하고, 그 이상은 "2회 이상 + 전체의 5% 이상" 반복된 모양만 취한다 —
    절대 횟수 기준은 블록 형식(블록당 한 번씩 나오는 줄 모양)을 잡음으로 오인해
    같은 형식 파일끼리도 묶지 못하는 회귀를 냈다(실측).
    """
    counts: dict[tuple[str, ...], int] = defaultdict(int)
    for line in text.splitlines():
        if not line.strip():
            continue
        counts[_signature(_tokenize(line))] += 1
    if min_count is None:
        total = sum(counts.values())
        min_count = 1 if total < 9 else max(2, round(total * 0.05))
    return frozenset(shape for shape, count in counts.items() if count >= min_count)


def mine_rule_candidates(samples: list[str]) -> list[RuleCandidate]:
    """Return every deterministically verified rule candidate from all samples."""
    # 군집: 시퀀스 → [(sample_index, line_index, tokens)]
    clusters: dict[tuple[str, ...], list[tuple[int, int, list[tuple[str, str]]]]] = defaultdict(list)
    line_table: list[list[str]] = []
    for sample_index, sample in enumerate(samples):
        lines = sample.splitlines()
        line_table.append(lines)
        for line_index, line in enumerate(lines):
            if not line.strip():
                continue
            tokens = _tokenize(line)
            clusters[_signature(tokens)].append((sample_index, line_index, tokens))

    candidates: list[RuleCandidate] = []

    # 1) 줄 레코드 후보 — 두 파일 이상(가능하면)에서 반복되는 시퀀스만
    multi_sample = len(samples) > 1
    for signature, members in sorted(clusters.items(), key=lambda kv: -len(kv[1])):
        if len(members) < _MIN_CLUSTER_LINES:
            continue
        if multi_sample and len({m[0] for m in members}) < 2:
            continue                          # 한 파일에만 있는 구조는 일반화 근거 부족
        file_count = len({member[0] for member in members})
        for pattern, sample_keys, widened in _synthesize_line_patterns(
            [tokens for _, _, tokens in members]
        ):
            note = f"{len(members)}줄이 같은 구조 (파일 {file_count}개)"
            if widened:
                note += " · 접두 일반화 변형 — base 와 둘 중 하나만 채택"
            candidates.append(RuleCandidate(
                pattern=pattern, key_group="key", kind="line_record",
                evidence_lines=len(members), sample_keys=sample_keys,
                note=note,
            ))

    # 2) 블록 후보 — 여는 군집·닫는 군집의 엄격 교대
    candidates.extend(_mine_block_candidates(clusters, line_table))
    # 3) 섹션 후보 — 반복 헤더 ~ 다음 헤더 직전
    candidates.extend(_mine_section_candidates(clusters))

    verified = [c for c in candidates if _verified(c, samples)]
    # 블록/섹션(큰 단위) 우선, 다음 근거 수 순 — Agent 에게 판단 재료로 제공
    verified.sort(key=lambda c: (c.kind == "line_record", -c.evidence_lines))
    return verified


def _opener_pattern(rows: list[list[tuple[str, str]]]) -> tuple[str, tuple[str, ...]] | None:
    variants = _synthesize_line_patterns(rows)
    if not variants:
        return None
    pattern, keys, _ = variants[0]           # 블록/섹션 여는줄은 base 변형만 사용
    return pattern[1:-4], keys               # 앞 '^' 와 뒤 '\r?$' 제거한 본체


def _mine_block_candidates(clusters, line_table) -> list[RuleCandidate]:
    output: list[RuleCandidate] = []
    items = [(sig, members) for sig, members in clusters.items() if len(members) >= 2]
    for open_sig, open_members in items:
        for close_sig, close_members in items:
            if open_sig == close_sig or len(open_members) != len(close_members):
                continue
            # 닫는 군집은 전부 동일 원문(상수 줄)이어야 신뢰
            close_lines = {line_table[s][l] for s, l, _ in close_members}
            if len(close_lines) != 1:
                continue
            if not _strictly_alternating(open_members, close_members):
                continue
            opener = _opener_pattern([tokens for _, _, tokens in open_members])
            if opener is None:
                continue
            opener_body, sample_keys = opener
            closer = re.escape(next(iter(close_lines)).rstrip())
            pattern = f"^{opener_body}\\r?\\n[\\s\\S]*?^{closer}\\r?$"
            output.append(RuleCandidate(
                pattern=pattern, key_group="key", kind="block",
                evidence_lines=len(open_members), sample_keys=sample_keys,
                note=f"여는줄~닫는줄 블록 {len(open_members)}개 (엄격 교대 확인)",
            ))
    return output


def _strictly_alternating(open_members, close_members) -> bool:
    by_sample: dict[int, list[tuple[int, str]]] = defaultdict(list)
    for s, l, _ in open_members:
        by_sample[s].append((l, "open"))
    for s, l, _ in close_members:
        by_sample[s].append((l, "close"))
    for events in by_sample.values():
        events.sort()
        expected = "open"
        for _, kind in events:
            if kind != expected:
                return False
            expected = "close" if expected == "open" else "open"
        if expected == "close":              # 열림이 닫히지 않은 채 끝남
            return False
    return True


def _mine_section_candidates(clusters) -> list[RuleCandidate]:
    output: list[RuleCandidate] = []
    for signature, members in clusters.items():
        if len(members) < 2:
            continue
        opener = _opener_pattern([tokens for _, _, tokens in members])
        if opener is None:
            continue
        opener_body, sample_keys = opener
        # key 앞의 상수 접두 전체를 다음 헤더 감지 앵커로 사용. 변수로 시작하는
        # 헤더(접두 없음)는 다음 헤더 감지가 불안정하므로 후보를 내지 않는다.
        anchor_prefix = opener_body.split("(?P<key>", 1)[0]
        if not anchor_prefix:
            continue
        pattern = (
            f"^{opener_body}\\r?$"
            f"[\\s\\S]*?(?=^{anchor_prefix}|\\Z)"
        )
        output.append(RuleCandidate(
            pattern=pattern, key_group="key", kind="section",
            evidence_lines=len(members), sample_keys=sample_keys,
            note=f"반복 헤더 {len(members)}개 ~ 다음 헤더 직전",
        ))
    return output
