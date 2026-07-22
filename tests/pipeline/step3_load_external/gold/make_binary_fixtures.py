"""Generate byte-controlled gold fixtures (CRLF, CP949) deterministically.

Run once from the analyzer root; results are committed. The Write path cannot
control encodings/line endings, so these fixtures are produced here.
"""
from __future__ import annotations

from pathlib import Path

GOLD = Path(__file__).resolve().parent

CRLF_YAML = "first:\r\n  enabled: true\r\nsecond:\r\n  enabled: false\r\n"
CP949_XML = (
    '<?xml version="1.0" encoding="euc-kr"?>\n'
    '<쿼리모음>\n'
    '  <질의 id="주문조회">SELECT 1 -- 주문</질의>\n'
    '</쿼리모음>\n'
)


def _big_sql_fixture() -> tuple[str, dict]:
    """One semantic unit far beyond the legacy 1200-char split limit."""
    body = "\n".join(
        f"    SELECT col{i}, col{i + 1} FROM orders WHERE order_id = #{{id}} AND status = 'S{i}'"
        for i in range(40)
    )
    element = f'<select id="hugeStatement">\n{body}\n  </select>'
    source = f'<mapper namespace="BigMapper">\n  {element}\n</mapper>\n'
    expected = {
        "format": "xml",
        "fail_closed": False,
        "chunks": [{
            "text": element,
            "keys": ["BigMapper.hugeStatement", "hugeStatement"],
            "semantic_kind": "xml_element",
        }],
    }
    assert len(element) > 1200, "fixture must exceed the legacy split limit"
    return source, expected


def main() -> None:
    import json

    crlf = GOLD / "fixtures" / "yaml" / "crlf.yaml"
    crlf.write_bytes(CRLF_YAML.encode("utf-8"))
    cp949 = GOLD / "fixtures" / "xml" / "cp949.xml"
    cp949.write_bytes(CP949_XML.encode("cp949"))
    big_source, big_expected = _big_sql_fixture()
    big = GOLD / "fixtures" / "xml" / "big_sql.xml"
    big.write_bytes(big_source.encode("utf-8"))
    (GOLD / "expected" / "big_sql.json").write_text(
        json.dumps(big_expected, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {crlf} ({crlf.stat().st_size}B), {cp949} ({cp949.stat().st_size}B), "
          f"{big} ({big.stat().st_size}B)")


if __name__ == "__main__":
    main()
