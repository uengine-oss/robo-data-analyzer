"""정밀도 우선 REFER_TO 링커 (spec 081): 판별은 문자열이 아니라 호출 지점에서 한다.

순서가 핵심이다. 먼저 **코드만** 보고 해석기 호출부를 찾아 키를 확정한 뒤, 그 키가
**주소성 있는 조각**을 가리킬 때만 잇는다. 문자열부터 대조하면 ``put("orderId")`` 도
후보가 되어 스키마 필드명과 우연히 맞아버리는데, 호출부부터 보면 그런 후보가 애초에
생기지 않는다.

키가 정적으로 확정되지 않는 호출(런타임 조립·분기·외부입력)은 **연결도 추정도 하지
않는다.** 미연결이 정답이며 건수만 관측치로 남긴다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from graph.schema.graph_edges import ReferTo, References
from graph.schema.graph_nodes import CodeNode, Constant, ExternalChunk, ProjectFile
from pipeline.steps.step5_linking.link_external.key_index import (
    mask_comments_and_literals,
    quoted_literals,
)
from pipeline.steps.step5_linking.link_external.resolver_catalog import (
    call_sites,
    promote_wrappers,
)
from pipeline.steps.step5_linking.link_external.literal_constants import LiteralConstantIndex
from shared.files.source_excerpt import raw_node_text
from pipeline.languages import external_resolvers
from pipeline.steps.step5_linking.link_external.target_index import (
    MAX_VALUE_LENGTH,
    TargetIndex,
)


class ExternalLinkContractError(ValueError):
    pass


@dataclass(frozen=True)
class LinkOutcome:
    external_edges: list[ReferTo]
    #: 해석기 호출인데 키가 정적으로 확정되지 않은 건수 (동적 조립·분기·외부입력).
    dynamic_key_sites: int = 0
    #: 주소성이 있는데 아무도 부르지 않은 조각 — 다음에 무엇을 확장할지 알려준다.
    orphan_chunks: list[str] = field(default_factory=list)
    #: 자동 승격된 사내 래퍼 (이름 → 키 인자 위치).
    promoted_wrappers: dict[str, tuple[int, ...]] = field(default_factory=dict)


def _normalized(path: str) -> str:
    return path.replace("\\", "/").strip().strip("/")


def link_external(
    *,
    code_nodes: list[CodeNode],
    constants: list[Constant],
    chunks: list[ExternalChunk],
    routine_ids: set[str],
    project_files: list[ProjectFile] | None = None,
    references_edges: list[References] | None = None,
) -> LinkOutcome:
    """해석기 호출부에서 확정된 키를 주소성 있는 대상에 잇는다.

    "이 루틴이 어느 리터럴 상수를 쓰는가"는 step2 의 REFERENCES 를 그대로 쓴다.
    지역 대입이나 상수 연결식을 실행 결과처럼 접지 않는다.

    호출 탐지에만 step2 의 ``call_sites`` 를 쓰지 않는다. 실측 결과 그 목록은 단순
    ``obj.method()`` 일부만 담고 ``context.lookup(x)`` 처럼 캐스팅·체이닝이 붙은 호출은
    누락한다(loan-ejb-app 440건 중 해석기 호출 0건). 불완전한 목록으로 거르면 놓치므로
    본문에서 직접 찾는다.
    """
    index = TargetIndex(chunks, project_files or [])
    constants_by_routine = LiteralConstantIndex(constants, references_edges)
    routines = sorted(
        (node for node in code_nodes if node.id in routine_ids), key=lambda node: node.id,
    )
    catalog = promote_wrappers(routines)
    promoted = {name: positions for name, positions in catalog.items()
                if name not in _declared_names()}

    edges: list[ReferTo] = []
    seen: set[tuple[str, str]] = set()
    dynamic = 0
    used_keys: set[str] = set()

    for code in routines:
        raw_code = raw_node_text(code)
        if not raw_code:
            continue
        source_file = _normalized(code.file_path)
        masked = mask_comments_and_literals(raw_code)
        for site in call_sites(
            masked, raw_code, catalog, code.start_line or 1, code.name or "",
        ):
            value = _determined_value(site.expression, code, constants_by_routine)
            if value is None:
                dynamic += 1
                continue
            hits = index.lookup(value, source_file=source_file)
            if not hits:
                continue
            for hit in hits:
                used_keys.add(hit.matched_key)
                identity = (code.id, hit.target_id)
                if identity in seen:
                    continue
                seen.add(identity)
                edges.append(ReferTo(
                    source_id=code.id,
                    target_id=hit.target_id,
                    matched_key=hit.matched_key,
                    evidence_file=code.file_path,
                    evidence_line=max(0, site.line),
                    via_symbol=_symbol_name(site.expression),
                ))

    return LinkOutcome(
        external_edges=edges,
        dynamic_key_sites=dynamic,
        orphan_chunks=_orphans(chunks, used_keys),
        promoted_wrappers=promoted,
    )


def _declared_names() -> frozenset[str]:
    return frozenset(external_resolvers())


def _determined_value(
    expression: str, code: CodeNode, constants: LiteralConstantIndex,
) -> str | None:
    """키 인자 표현식을 확정 문자열로. 확정 못 하면 None(동적)."""
    raw = expression.strip()
    if not raw:
        return None
    literals = quoted_literals(raw)
    if len(literals) == 1 and literals[0].start == 0 and literals[0].end == len(raw):
        value = literals[0].value
        return value if 0 < len(value) <= MAX_VALUE_LENGTH else None
    name = _symbol_name(raw)
    if not name:
        return None
    return constants.value_for(code.id, name)


def _symbol_name(expression: str) -> str:
    """``Class.CONST`` / ``CONST`` 에서 마지막 마디. 그 밖이면 빈 문자열."""
    raw = expression.strip()
    if not raw or not all(part.isidentifier() for part in raw.split(".") if part):
        return ""
    return raw.rsplit(".", 1)[-1]


def _orphans(chunks: list[ExternalChunk], used_keys: set[str]) -> list[str]:
    """주소성이 있는데 아무 호출부도 부르지 않은 조각의 대표 키."""
    output: list[str] = []
    for chunk in chunks:
        if chunk.provenance != "source_span" or not chunk.addressable_keys:
            continue
        if not any(key in used_keys for key in chunk.addressable_keys):
            output.append(f"{chunk.file_path}#{chunk.addressable_keys[0]}")
    return sorted(output)
