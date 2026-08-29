"""YAML prompt rule loading and inheritance."""
from __future__ import annotations

from pathlib import Path
import re
import yaml

from shared.files.run_cache_context import read_run_text

from llm.runners.prompt_values import _safe_format
from pipeline.execution.run_output_context import get_response_locale

from pipeline.languages import (
    stereotype_priority_text,
    stereotype_prompt_options,
)


def _load_rule(prompts_dir: Path, rule_name: str) -> dict:
    """yaml 룰 로딩 + ``extends`` 처리 → ``{system, user_template, model}`` 반환.

    ``extends: <name>`` 가 있으면 ``<name>.yaml`` 의 byte-identical ``system`` 다음에
    **분할별 고정 지침**, 공통 대상 입력, 분할별 고변동 입력 순으로 둔다.

    KV prefix 캐시는 앞에서 처음 달라지는 지점까지만 재사용한다. 대상별 SOURCE를 고정 지침
    앞에 두면 같은 rule의 수백 호출이 target_id에서 갈라져 긴 고정 지침을 매번 prefill한다.
    같은 노드의 서로 다른 concern 2~3개가 입력을 공유하는 국소 이득보다, 같은 concern의 전체
    대상이 고정 지침을 공유하는 전역 이득이 우선이다. 변동성이 큰 행 할당·분기 입력은 항상
    맨 뒤에 둔다. 실제 서버 reuse와 벽시계 효과는 API input/cached_tokens가 아니라 서버 로그와
    cold run으로 판정한다.

    ``model`` 은 분할 고유 > 공통 순으로 우선.
    """
    rule = yaml.safe_load(read_run_text(
        prompts_dir / f"{rule_name}.yaml", encoding="utf-8",
    ))

    static_values: dict[str, str] = {}
    prompts_root = prompts_dir.parent.resolve()
    for key, relative_path in (rule.get("shared_values") or {}).items():
        path = (prompts_dir / str(relative_path)).resolve()
        if path != prompts_root and prompts_root not in path.parents:
            raise ValueError(f"shared prompt value escapes prompts root: {relative_path}")
        static_values[str(key)] = read_run_text(path, encoding="utf-8").strip()
    rendered_templates = "".join(str(rule.get(key, "")) for key in (
        "user_template_specific",
        "user_template_inputs",
        "user_template_dynamic_inputs",
        "user_template",
    ))
    if "{stereotype_options}" in rendered_templates:
        static_values["stereotype_options"] = stereotype_prompt_options(prompts_dir.name)
    if "{stereotype_priority}" in rendered_templates:
        static_values["stereotype_priority"] = stereotype_priority_text()
    fixed_template = str(rule.get("user_template_specific", ""))
    fixed_placeholders = set(re.findall(
        r"\{([A-Za-z_][A-Za-z0-9_]*)\}", fixed_template
    ))
    volatile_in_fixed_prefix = fixed_placeholders - set(static_values)
    if volatile_in_fixed_prefix:
        raise ValueError(
            "volatile placeholders must not appear in user_template_specific: "
            f"{rule_name}: {sorted(volatile_in_fixed_prefix)}"
        )

    extends = rule.get("extends")
    if extends is None:
        split_keys = {
            "user_template_specific",
            "user_template_inputs",
            "user_template_dynamic_inputs",
        } & set(rule)
        if split_keys:
            if "user_template" in rule:
                raise ValueError(
                    f"prompt mixes ordered sections with legacy user_template: {rule_name}"
                )
            rule["user_template"] = (
                rule.get("user_template_specific", "")
                + rule.get("user_template_inputs", "")
                + rule.get("user_template_dynamic_inputs", "")
            )
        rule["static_values"] = static_values
        return rule

    common = yaml.safe_load(read_run_text(
        prompts_dir / f"{extends}.yaml", encoding="utf-8",
    ))
    inherit_inputs = rule.get("inherit_inputs", True)
    if not isinstance(inherit_inputs, bool):
        raise ValueError(f"inherit_inputs must be boolean: {rule_name}")
    return {
        "system": common.get("system", ""),
        "user_template": (
            rule.get("user_template_specific", "")
            + (common.get("user_template_inputs", "") if inherit_inputs else "")
            + rule.get("user_template_dynamic_inputs", "")
        ),
        "model": rule.get("model") or common.get("model"),
        "max_tokens": rule.get("max_tokens") or common.get("max_tokens"),
        "structured_output_method": (
            rule.get("structured_output_method")
            or common.get("structured_output_method")
        ),
        "static_values": static_values,
    }


def render_rule_user_content(rule: dict, user_values: dict) -> str:
    """Render the exact user message used by the YAML runner.

    Callers that schedule or split finite work may inspect its size without
    maintaining a second, inevitably drifting approximation of the prompt.
    """
    return _safe_format(
        str(rule.get("user_template", "")),
        {
            **rule.get("static_values", {}),
            "response_locale": get_response_locale(),
            **user_values,
        },
    )
