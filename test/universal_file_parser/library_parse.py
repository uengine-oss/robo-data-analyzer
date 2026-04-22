"""경로 1 — 라이브러리 기반 결정론 파싱

화이트리스트(LIBRARY_FORMATS)에 있는 포맷은 여기서 처리. LLM 호출 0회.
실패 시 None 반환 → 경로 2 (DeepAgent) 로 승격.

문자열 정규화 규칙:
  - 모든 scalar value 는 문자열로 통일 (downstream 이 타입 변환 책임)
  - None → "", bool → "True"/"False", int/float → 문자열
"""

import csv
import io
import json
import logging
from configparser import ConfigParser

logger = logging.getLogger(__name__)


# =============================================================================
# 화이트리스트 — 확장자가 이 Set 에 있으면 라이브러리 경로로 직행.
# 없으면 경로 2 (DeepAgent).
# =============================================================================

LIBRARY_FORMATS = {
    'json', 'yaml', 'xml', 'toml', 'ini',
    'dotenv', 'dockerfile', 'csv',
}


# =============================================================================
# 값 정규화
# =============================================================================

def _stringify(obj):
    """재귀적으로 모든 scalar value 를 문자열로 변환."""
    if isinstance(obj, dict):
        return {str(k): _stringify(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_stringify(v) for v in obj]
    if obj is None:
        return ""
    if isinstance(obj, bool):
        return str(obj)
    if isinstance(obj, (int, float)):
        return str(obj)
    return str(obj)


# =============================================================================
# 포맷별 핸들러
# =============================================================================

def parse_json(data: bytes, encoding: str) -> dict | list | None:
    try:
        return _stringify(json.loads(data.decode(encoding, errors='replace')))
    except Exception as e:
        logger.debug("parse_json failed: %s", e)
        return None


def parse_yaml(data: bytes, encoding: str) -> dict | list | None:
    try:
        from ruamel.yaml import YAML
        yaml = YAML(typ='safe')
        text = data.decode(encoding, errors='replace')
        result = yaml.load(text)
        return _stringify(result) if result is not None else None
    except Exception as e:
        logger.debug("parse_yaml failed: %s", e)
        return None


def parse_xml(data: bytes, encoding: str) -> dict | list | None:
    """XML → 평탄한 element 리스트.

    defusedxml 로 모든 태그를 순회해 (tag, path, attrs, text) 를 flat list 로 반환.
    어떤 태그/속성이 anchor 인가는 downstream 판단 영역.

    Returns:
        {"root": str, "element_count": int, "elements": [{tag, path, attrs, text}, ...]}
    """
    try:
        from defusedxml import ElementTree as ET
        text = data.decode(encoding, errors='replace')
        root = ET.fromstring(text)
        root_tag = _strip_ns(root.tag)
        elements: list[dict] = []
        _walk_xml(root, "/" + root_tag, elements)
        return {
            "root": root_tag,
            "element_count": len(elements),
            "elements": elements,
        }
    except Exception as e:
        logger.debug("parse_xml failed: %s", e)
        return None


def _strip_ns(tag: str) -> str:
    """XML 네임스페이스 제거: '{http://...}tag' → 'tag'."""
    if isinstance(tag, str) and tag.startswith('{'):
        end = tag.find('}')
        if end != -1:
            return tag[end + 1:]
    return tag


def _walk_xml(elem, path: str, out: list, max_text: int = 500):
    tag = _strip_ns(elem.tag)
    attrs = {_strip_ns(k): str(v) for k, v in elem.attrib.items()}
    text = (elem.text or "").strip()
    if len(text) > max_text:
        text = text[:max_text] + f"... [truncated, {len(elem.text or '')} chars]"

    out.append({
        "tag": tag,
        "path": path,
        "attrs": attrs,
        "text": text,
    })

    tag_counts: dict[str, int] = {}
    for child in elem:
        child_tag = _strip_ns(child.tag)
        idx = tag_counts.get(child_tag, 0)
        tag_counts[child_tag] = idx + 1
        child_path = f"{path}/{child_tag}[{idx}]"
        _walk_xml(child, child_path, out, max_text)


def parse_toml(data: bytes, encoding: str) -> dict | list | None:
    try:
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib
        return _stringify(tomllib.loads(data.decode(encoding, errors='replace')))
    except Exception as e:
        logger.debug("parse_toml failed: %s", e)
        return None


def parse_ini(data: bytes, encoding: str) -> dict | list | None:
    try:
        parser = ConfigParser()
        parser.read_string(data.decode(encoding, errors='replace'))
        result = {
            section: dict(parser.items(section))
            for section in parser.sections()
        }
        return _stringify(result) if result else None
    except Exception as e:
        logger.debug("parse_ini failed: %s", e)
        return None


def parse_dotenv(data: bytes, encoding: str) -> dict | list | None:
    try:
        from dotenv import dotenv_values
        text = data.decode(encoding, errors='replace')
        result = dict(dotenv_values(stream=io.StringIO(text)))
        result = {k: v for k, v in result.items() if v is not None}
        return _stringify(result) if result else None
    except Exception as e:
        logger.debug("parse_dotenv failed: %s", e)
        return None


def parse_csv(data: bytes, encoding: str) -> dict | list | None:
    try:
        text = data.decode(encoding, errors='replace')
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=',;\t|')
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(io.StringIO(text), dialect=dialect)
        rows = [_stringify(dict(row)) for row in reader]
        return rows if rows else None
    except Exception as e:
        logger.debug("parse_csv failed: %s", e)
        return None


def parse_dockerfile(data: bytes, encoding: str) -> dict | list | None:
    """Dockerfile → {instruction: [args, ...]}. dockerfile-parse 없으면 None → DeepAgent."""
    try:
        from dockerfile_parse import DockerfileParser
    except ImportError:
        logger.debug("dockerfile-parse 미설치 — DeepAgent 로 fallback")
        return None

    try:
        text = data.decode(encoding, errors='replace')
        dfp = DockerfileParser(fileobj=io.BytesIO(text.encode('utf-8')))
        dfp.content = text

        result = {}
        for entry in dfp.structure:
            instr = entry.get('instruction', '').upper()
            value = entry.get('value', '')
            if instr and instr != 'COMMENT':
                result.setdefault(instr, []).append(value)

        return result if result else None
    except Exception as e:
        logger.debug("parse_dockerfile failed: %s", e)
        return None


# =============================================================================
# 핸들러 레지스트리
# =============================================================================

LIBRARY_HANDLERS = {
    'json':       parse_json,
    'yaml':       parse_yaml,
    'xml':        parse_xml,
    'toml':       parse_toml,
    'ini':        parse_ini,
    'dotenv':     parse_dotenv,
    'csv':        parse_csv,
    'dockerfile': parse_dockerfile,
}


def is_library_supported(fmt: str) -> bool:
    """확장자가 라이브러리 화이트리스트에 있는가."""
    return fmt in LIBRARY_FORMATS and fmt in LIBRARY_HANDLERS


def library_parse(fmt: str, data: bytes, encoding: str) -> dict | list | None:
    """포맷에 맞는 핸들러 호출. 미지원 포맷이거나 실패 시 None."""
    if not is_library_supported(fmt):
        return None
    handler = LIBRARY_HANDLERS[fmt]
    return handler(data, encoding)
