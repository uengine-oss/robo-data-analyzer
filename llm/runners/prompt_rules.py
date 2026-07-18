"""YAML prompt rule loading and inheritance."""
from __future__ import annotations

from pathlib import Path
import yaml

def _load_rule(prompts_dir: Path, rule_name: str) -> dict:
    """yaml 룰 로딩 + ``extends`` 처리 → ``{system, user_template, model}`` 반환.

    ``extends: <name>`` 가 있으면 ``<name>.yaml`` 의 ``system`` + ``user_template_inputs`` 를
    공통 prefix 로 합쳐 분할 그룹의 system 본문 byte-identical 을 강제한다 (KV 캐시
    prefix 적중 보장). ``model`` 은 분할 고유 > 공통 순으로 우선.
    """
    rule = yaml.safe_load((prompts_dir / f"{rule_name}.yaml").read_text(encoding="utf-8"))

    static_values: dict[str, str] = {}
    prompts_root = prompts_dir.parent.resolve()
    for key, relative_path in (rule.get("shared_values") or {}).items():
        path = (prompts_dir / str(relative_path)).resolve()
        if path != prompts_root and prompts_root not in path.parents:
            raise ValueError(f"shared prompt value escapes prompts root: {relative_path}")
        static_values[str(key)] = path.read_text(encoding="utf-8").strip()

    extends = rule.get("extends")
    if extends is None:
        rule["static_values"] = static_values
        return rule

    common = yaml.safe_load((prompts_dir / f"{extends}.yaml").read_text(encoding="utf-8"))
    return {
        "system": common.get("system", ""),
        "user_template": (
            common.get("user_template_inputs", "") + rule.get("user_template_specific", "")
        ),
        "model": rule.get("model") or common.get("model"),
        "max_tokens": rule.get("max_tokens") or common.get("max_tokens"),
        "static_values": static_values,
    }
