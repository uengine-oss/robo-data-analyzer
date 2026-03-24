"""YAML 설정 파서."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List

from analyzer.strategy.framework.models import ConfigSectionChunk
from util.text_utils import read_text_with_fallback

logger = logging.getLogger(__name__)


def parse_yaml_config(path: str) -> List[ConfigSectionChunk]:
    """ruamel.yaml로 key-path 추출 (라인 유지)."""
    try:
        from ruamel.yaml import YAML  # type: ignore[import]
    except ImportError:
        try:
            import yaml  # type: ignore[import]
        except ImportError:
            logger.warning("ruamel.yaml / PyYAML 미설치: YAML 스킵")
            logger.info(
                "[FW:STEP3] 스킵 | parser=yaml file=%s reason=no_yaml_lib",
                Path(path).name,
            )
            return []
        text = read_text_with_fallback(path)
        data = yaml.safe_load(text)
        chunks: List[ConfigSectionChunk] = []

        def flatten(d: dict, prefix: str = "") -> None:
            for k, v in d.items():
                key_path = f"{prefix}.{k}" if prefix else k
                if isinstance(v, dict) and v:
                    flatten(v, key_path)
                else:
                    chunks.append(
                        ConfigSectionChunk(
                            config_key_path=key_path,
                            start_line=0,
                            end_line=0,
                            raw_snippet=str(v)[:200],
                            file_path=path,
                        )
                    )

        if isinstance(data, dict):
            flatten(data)
        return chunks

    chunks_yaml: List[ConfigSectionChunk] = []
    yaml_loader = YAML()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml_loader.load(f)
    except Exception as e:
        logger.info(
            "[FW:STEP3] 스킵 | parser=yaml file=%s reason=load_error error=%s",
            Path(path).name,
            e,
        )
        return []

    def walk(node, prefix: str = ""):
        if hasattr(node, "lc") and hasattr(node.lc, "line"):
            line = node.lc.line
        else:
            line = 0
        if isinstance(node, dict):
            for k, v in node.items():
                key_path = f"{prefix}.{k}" if prefix else str(k)
                walk(v, key_path)
                if not isinstance(v, (dict, list)) or (isinstance(v, (dict, list)) and not v):
                    chunks_yaml.append(
                        ConfigSectionChunk(
                            config_key_path=key_path,
                            start_line=line,
                            end_line=line,
                            raw_snippet=str(v)[:200] if v is not None else "",
                            file_path=path,
                        )
                    )
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{prefix}[{i}]")

    if isinstance(data, dict):
        walk(data)
    return chunks_yaml
