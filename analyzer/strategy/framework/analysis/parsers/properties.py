"""properties 파일 파서."""
from __future__ import annotations

import logging
from typing import List

from analyzer.strategy.framework.common.models.node_models import ConfigSectionChunk
from util.encoding_reader import read_text_with_fallback_sync as read_text_with_fallback

logger = logging.getLogger(__name__)


def parse_properties(path: str) -> List[ConfigSectionChunk]:
    """properties 직접 파싱 (라인 유지)."""
    text = read_text_with_fallback(path)
    chunks: List[ConfigSectionChunk] = []
    for line_no, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("!"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            chunks.append(
                ConfigSectionChunk(
                    config_key_path=key,
                    start_line=line_no,
                    end_line=line_no,
                    raw_snippet=value[:200],
                    file_path=path,
                )
            )
    return chunks
