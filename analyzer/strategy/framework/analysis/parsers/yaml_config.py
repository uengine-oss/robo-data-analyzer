"""YAML 설정 파서.

ruamel.yaml 로 key-path 추출 (라인 정보 보존).
ruamel.yaml 은 requirements.txt 의 필수 의존성이므로 ImportError 폴백 없음.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List

from ruamel.yaml import YAML

from analyzer.strategy.framework.common.models.node_models import ConfigSectionChunk

logger = logging.getLogger(__name__)


def parse_yaml_config(path: str) -> List[ConfigSectionChunk]:
    """YAML 파일을 평탄화하여 ConfigSectionChunk 리스트 반환.

    잘못된 YAML 입력은 fail-fast — 호출자(parse_config_and_artifacts)가 잡거나 전파.
    """
    chunks: List[ConfigSectionChunk] = []
    yaml_loader = YAML()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml_loader.load(f)
    except Exception as e:
        logger.error(
            "[PHASE:1:ARTIFACT] YAML 로드 실패 | path=%s | %s: %s",
            Path(path).name, type(e).__name__, e, exc_info=True,
        )
        raise RuntimeError(
            f"YAML 로드 실패: {path} ({type(e).__name__}: {e})"
        ) from e

    def walk(node, prefix: str = "") -> None:
        line = node.lc.line if hasattr(node, "lc") and hasattr(node.lc, "line") else 0
        if isinstance(node, dict):
            for k, v in node.items():
                key_path = f"{prefix}.{k}" if prefix else str(k)
                walk(v, key_path)
                if not isinstance(v, (dict, list)) or (isinstance(v, (dict, list)) and not v):
                    chunks.append(
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
    return chunks
