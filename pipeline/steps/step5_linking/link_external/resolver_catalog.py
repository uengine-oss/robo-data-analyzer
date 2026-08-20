"""해석기(resolver) 카탈로그 — "키로 외부 내용을 가져오는 호출"이 무엇인지 (spec 081).

판별은 문자열이 아니라 **호출 지점**에서 한다. 외부 파일 내용이 코드로 들어오려면 반드시
어떤 호출이 있어야 하고, 파일을 읽는 방법은 런타임이 정한 유한 집합이기 때문이다.
그래서 조회 호출에 들어가는 문자열만 후보가 되고, ``map.put("orderId")`` 같은 것은
후보 자체가 생기지 않는다 — 대조할 기회가 없으니 우연 일치가 원천 차단된다.

카탈로그는 두 갈래로 채워진다.
- **선언**: 런타임 I/O 프리미티브와 널리 쓰이는 프레임워크 API. 프레임워크 소스는 우리
  저장소에 없어 추적할 수 없으므로 표로 적는다.
- **자동 승격**: 사내 래퍼. 소스가 우리 안에 있으므로 본문을 열어 "카탈로그 호출을 하고
  키 인자를 가공 없이 그대로 넘기는가"를 결정론으로 확인해 카탈로그에 더한다.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

from graph.schema.graph_nodes import CodeNode
from pipeline.languages import external_resolvers
from pipeline.steps.step5_linking.link_external.key_index import mask_comments_and_literals
from shared.files.source_excerpt import raw_node_text


_PARAMETERS = re.compile(r"\(([^)]*)\)")
_TRAILING_NAME = re.compile(r"([A-Za-z_]\w*)\s*$")
_PLAIN_NAME = re.compile(r"[A-Za-z_]\w*")


@dataclass(frozen=True)
class CallSite:
    """해석기 호출 한 건 — 키 인자의 표현식과 그 위치."""
    method: str
    argument_index: int
    expression: str
    line: int
    offset: int                              # 같은 호출에서 나온 인자들을 묶는 식별자


def parameter_names(node: CodeNode) -> list[str]:
    """선언부에서 파라미터 이름을 순서대로 뽑는다."""
    head = node.signature or raw_node_text(node).split("{", 1)[0]
    match = _PARAMETERS.search(head)
    if not match:
        return []
    names: list[str] = []
    for piece in match.group(1).split(","):
        found = _TRAILING_NAME.search(piece.strip())
        names.append(found.group(1) if found else "")
    return names


def call_sites(
    masked: str, source: str, resolvers: dict[str, tuple[int, ...]], base_line: int,
    own_name: str = "",
) -> list[CallSite]:
    """본문에서 카탈로그 호출을 찾아 키 인자 표현식을 뽑는다.

    호출 **탐색**은 마스킹본으로 한다 — 주석이나 문자열 안의 ``lookup(`` 을 호출로 오인하지
    않기 위해서다. 인자 **표현식**은 같은 오프셋의 원문에서 읽는다. 마스킹이 길이를
    보존하므로 오프셋이 일치하며, 이렇게 해야 리터럴 값이 지워지지 않는다.

    ``own_name`` 은 이 본문을 소유한 루틴의 이름이다. 승격된 래퍼는 **자기 선언부가 자기
    이름의 호출처럼 보이므로**(``EJBLocalHome getLocalHome(String jndiName)``) 그 첫
    등장을 건너뛴다. 루틴 노드의 범위는 선언부에서 시작하므로 첫 등장이 곧 선언부이고,
    본문의 재귀 호출은 그 뒤에 오므로 살아남는다. 선언부를 호출로 세면 파라미터 이름이
    맨이름인 언어에서 같은 이름의 상수로 해석돼 **오연결이 난다.**

    step2 의 ``call_sites`` 를 쓰지 않는 이유는 ``linker`` 의 docstring 에 적었다.
    """
    output: list[CallSite] = []
    for method, positions in resolvers.items():
        matches = list(re.finditer(rf"(?<!\w){re.escape(method)}\s*\(", masked))
        if method == own_name and matches:
            matches = matches[1:]
        for match in matches:
            arguments = _arguments(source, match.end())
            if arguments is None:
                continue
            line = base_line + masked.count("\n", 0, match.start())
            for index in positions:
                if index < len(arguments):
                    output.append(
                        CallSite(method, index, arguments[index].strip(), line, match.start()))
    return output


def _arguments(code: str, start: int) -> list[str] | None:
    """여는 괄호 다음부터 짝이 맞는 닫는 괄호까지를 최상위 콤마로 나눈다."""
    depth = 1
    index = start
    current: list[str] = []
    parts: list[str] = []
    while index < len(code):
        char = code[index]
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
            if depth == 0:
                parts.append("".join(current))
                return parts
        if char == "," and depth == 1:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
        index += 1
    return None                              # 괄호가 닫히지 않음 — 잘린 본문


def promote_wrappers(routines: list[CodeNode]) -> dict[str, tuple[int, ...]]:
    """사내 래퍼를 결정론으로 승격해 카탈로그를 확장한다.

    승격 조건은 셋이다: ① 본문에 카탈로그 호출이 있고 ② 그 키 인자가 이 메서드의
    파라미터와 **같은 심볼**이며 ③ 그 파라미터가 본문에서 재대입되지 않는다.
    캐시·널검사·조건분기는 값을 바꾸지 않으므로 통과한다. 값을 이어붙이거나 조건에 따라
    다른 것을 넘기면 ②에서 걸러진다.

    같은 이름의 루틴이 여럿일 때는 **전부가 같은 위치로 승격될 때만** 채택한다.
    하나라도 아니면 그 이름은 폐기한다 — ``getString`` 처럼 한쪽은 리소스 조회이고
    다른 쪽은 행 값 꺼내기인 이름이 섞이는 것을 막는다.
    """
    catalog = dict(external_resolvers())
    # Every successful round adds at least one previously absent routine name. The routine
    # population is finite, so this monotone fixpoint terminates without a semantic hop cap.
    while True:
        qualified: dict[str, set[int]] = {}
        for node in routines:
            if not node.name:
                continue
            positions = _forwarded_positions(node, catalog)
            if positions:
                qualified.setdefault(node.name, set()).update(positions)
        added = False
        for name, positions in qualified.items():
            if name in catalog:
                continue
            # 같은 이름의 루틴이 전부 승격 자격을 갖췄는지 — 하나라도 아니면 폐기.
            same_name = [node for node in routines if node.name == name]
            if any(not _forwarded_positions(node, catalog) for node in same_name):
                continue
            catalog[name] = tuple(sorted(positions))
            added = True
        if not added:
            break
    return catalog


def _forwarded_positions(node: CodeNode, catalog: dict[str, tuple[int, ...]]) -> set[int]:
    """이 루틴이 파라미터를 가공 없이 카탈로그 호출로 넘기는 위치들.

    카탈로그 호출이 **본문에 둘 이상**이면 승격하지 않는다. 조건에 따라 다른 것을 조회하는
    래퍼(``if (f) lookup(a) else lookup(b)``)를 승격하면 실행 시 하나만 쓰이는데 둘 다
    잇게 되어 한쪽이 반드시 틀린 연결이 된다.
    """
    names = parameter_names(node)
    raw_code = raw_node_text(node)
    if not names or not raw_code:
        return set()
    body = mask_comments_and_literals(raw_code)
    sites = call_sites(body, raw_code, catalog, node.start_line or 1, node.name or "")
    if len({site.offset for site in sites}) != 1:
        return set()
    positions: set[int] = set()
    for site in sites:
        expression = site.expression.strip()
        if not _PLAIN_NAME.fullmatch(expression) or expression not in names:
            continue                         # 리터럴·결합식·다른 변수는 전달이 아니다
        if _reassigned(body, expression):
            continue
        positions.add(names.index(expression))
    return positions


def _reassigned(body: str, name: str) -> bool:
    pattern = rf"(?<![\w.]){re.escape(name)}(?!\w)\s*(?:\+\+|--|[+\-*/%]=|=(?!=))"
    return bool(re.search(pattern, body))
