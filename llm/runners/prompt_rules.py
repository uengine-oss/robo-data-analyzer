"""YAML prompt rule loading and inheritance."""
from __future__ import annotations

from pathlib import Path
import yaml

from pipeline.languages import module_stereotype_prompt_options, stereotype_prompt_options


def _load_rule(prompts_dir: Path, rule_name: str) -> dict:
    """yaml 룰 로딩 + ``extends`` 처리 → ``{system, user_template, model}`` 반환.

    ``extends: <name>`` 가 있으면 ``<name>.yaml`` 의 ``system`` 뒤에 분할별 고정 지침,
    마지막에 변동 입력을 둔다. KV prefix 는 **변화율이 낮은 순서**여야 한다:
    공통 system → 같은 rule 모든 호출에 고정인 specific → 프로젝트/대상별 inputs.
    동적 입력을 고정 지침 앞에 두면 함수가 바뀔 때마다 그 뒤의 긴 지침까지 cache miss가
    되므로 금지한다. ``model`` 은 분할 고유 > 공통 순으로 우선.
    """
    rule = yaml.safe_load((prompts_dir / f"{rule_name}.yaml").read_text(encoding="utf-8"))

    static_values: dict[str, str] = {}
    prompts_root = prompts_dir.parent.resolve()
    for key, relative_path in (rule.get("shared_values") or {}).items():
        path = (prompts_dir / str(relative_path)).resolve()
        if path != prompts_root and prompts_root not in path.parents:
            raise ValueError(f"shared prompt value escapes prompts root: {relative_path}")
        static_values[str(key)] = path.read_text(encoding="utf-8").strip()
    rendered_templates = (
        str(rule.get("user_template_specific", ""))
        + str(rule.get("user_template", ""))
    )
    if "{stereotype_options}" in rendered_templates:
        static_values["stereotype_options"] = stereotype_prompt_options(prompts_dir.name)
    if "{module_stereotype_options}" in rendered_templates:
        static_values["module_stereotype_options"] = module_stereotype_prompt_options(
            prompts_dir.name
        )

    extends = rule.get("extends")
    if extends is None:
        rule["static_values"] = static_values
        return rule

    common = yaml.safe_load((prompts_dir / f"{extends}.yaml").read_text(encoding="utf-8"))
    return {
        "system": common.get("system", ""),
        "user_template": (
            rule.get("user_template_specific", "")
            + common.get("user_template_inputs", "")
            + rule.get("user_template_evidence", "")
        ),
        "model": rule.get("model") or common.get("model"),
        "max_tokens": rule.get("max_tokens") or common.get("max_tokens"),
        "static_values": static_values,
    }
