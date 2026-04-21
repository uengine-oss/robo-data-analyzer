"""중첩 dict/list → dot-notation flat dict

{"a": {"b": 1}} → {"a.b": "1"}
[1, 2] → {"[0]": "1", "[1]": "2"}
"""


def flatten(obj, parent: str = "", max_value_len: int = 200) -> dict[str, str]:
    """재귀적으로 평탄화. 값은 모두 문자열로 변환, 너무 길면 절삭."""
    items: dict[str, str] = {}

    if isinstance(obj, dict):
        if not obj and parent:
            items[parent] = ""
            return items
        for k, v in obj.items():
            key = f"{parent}.{k}" if parent else str(k)
            items.update(flatten(v, key, max_value_len))
    elif isinstance(obj, list):
        if not obj and parent:
            items[parent] = ""
            return items
        for i, v in enumerate(obj):
            key = f"{parent}[{i}]" if parent else f"[{i}]"
            items.update(flatten(v, key, max_value_len))
    else:
        s = "" if obj is None else str(obj)
        if len(s) > max_value_len:
            s = s[:max_value_len] + f"... [TRUNCATED, {len(str(obj))} chars]"
        items[parent] = s

    return items
