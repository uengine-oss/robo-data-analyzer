"""Safe YAML prompt value assembly."""
from __future__ import annotations

# Prompt rules use different identifier names. Keep the precedence alongside the
# extractor so the final-tree split cannot leave the helper without its data.
_TARGET_ID_KEYS = (
    "id", "component_id", "unit_id", "table_fqn", "case_id", "term", "name", "file",
)

def _safe_format(template: str, values: dict) -> str:
    """``user_template`` 안전 치환 — yaml 안 자유 텍스트의 ``{...}`` 영향 0.

    표준 ``str.format`` / ``str.format_map`` 은 yaml 안 자유 텍스트의 ``{key:val}``
    형식도 placeholder 로 해석해서 ``KeyError`` (모르는 키) 또는 ``ValueError``
    (``:`` 다음 문자열을 format spec 으로 검증) 를 낸다. format 메커니즘 자체를
    회피하고 알려진 키만 ``re.sub`` 한 번 패스로 치환한다.

    - 알려진 placeholder 만 치환 (``values.keys()`` 에 있는 키).
    - 모르는 ``{...}`` 는 원형 보존 (yaml 출력 미리보기 같은 자유 텍스트 안전).
    - ``re.sub`` 한 번 패스라 값 안의 ``{key}`` 가 다시 치환되는 일도 없다.
    - ``{{`` → ``{`` (``}}`` → ``}``) escape 규칙은 그대로 작동 (yaml 작성자가
      의도한 literal brace 표기 보존).
    """
    # Render the template in one pass. Applying brace unescaping *after* value
    # substitution corrupts injected JSON: every nested ``}}`` in the value loses
    # a closing brace. Arbitrary evidence values are opaque to prompt assembly.
    rendered: list[str] = []
    index = 0
    length = len(template)
    while index < length:
        if template.startswith("{{", index):
            rendered.append("{")
            index += 2
            continue
        if template.startswith("}}", index):
            rendered.append("}")
            index += 2
            continue
        if template[index] == "{":
            closing = template.find("}", index + 1)
            if closing >= 0:
                key = template[index + 1 : closing]
                if key in values:
                    rendered.append(str(values[key]))
                    index = closing + 1
                    continue
        rendered.append(template[index])
        index += 1
    return "".join(rendered)


def _extract_target_and_sort(user_values: dict) -> tuple[str | None, str | None]:
    """``user_values`` 에서 target_id + sort_key 자동 추출.

    target_id: ctrl+F 검색 / 가독성용. 첫 매칭 키 값.
    sort_key: 한 파일 안 records 정렬용 — file 이 있으면 ``<file>:<start_line>`` 권장.
    """
    target_id: str | None = None
    for k in _TARGET_ID_KEYS:
        v = user_values.get(k)
        if v:
            target_id = str(v)
            break

    file_v = user_values.get("file") or user_values.get("file_path")
    start_v = user_values.get("start_line") or user_values.get("line")
    if file_v and start_v:
        return target_id, f"{file_v}:{start_v}"
    return target_id, target_id
