"""1·2단계 결정론적 파싱 (라이브러리 기반)

각 핸들러는 `(data: bytes, encoding: str) -> dict | list | None` 시그니처.
실패하면 None 반환 → 3단계(LLM 코드생성)로 승격.

문자열 정규화 규칙:
  - 모든 value 는 문자열로 통일 (downstream 이 타입 변환 책임)
  - None 은 빈 문자열로, bool 은 "True"/"False" 로
"""

import csv
import io
import json
import logging
from configparser import ConfigParser

logger = logging.getLogger(__name__)


# =============================================================================
# 값 정규화 (모든 value → 문자열)
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
# 1단계 — 표준 포맷
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
    """XML → 평탄한 태그 리스트.

    라이브러리는 의견 없이 **모든 태그를** 순회해서 (tag, attrs, text, path) 를 뽑음.
    "어느 것이 코드에서 참조되는 의미 있는 anchor 인지" 는 LLM 의 판단 영역.

    Returns:
        {"root": str, "elements": [{"tag", "path", "attrs", "text"}, ...]}
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
    """모든 element 를 재귀 순회해 평탄한 리스트로 축적."""
    tag = _strip_ns(elem.tag)
    # 속성명도 namespace 제거
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

    # 동일 tag 여러 개면 index 부여
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
        # None 값 제거 (주석만 있는 줄 등)
        result = {k: v for k, v in result.items() if v is not None}
        return _stringify(result) if result else None
    except Exception as e:
        logger.debug("parse_dotenv failed: %s", e)
        return None


def parse_csv(data: bytes, encoding: str) -> dict | list | None:
    try:
        text = data.decode(encoding, errors='replace')
        # Sniffer 로 dialect 추정
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


def parse_properties(data: bytes, encoding: str) -> dict | list | None:
    """Java .properties 파일 (key=value, key:value)."""
    try:
        text = data.decode(encoding, errors='replace')
        result = {}
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith(('#', '!')):
                continue
            for sep in ('=', ':'):
                if sep in line:
                    key, _, value = line.partition(sep)
                    result[key.strip()] = value.strip()
                    break
        return result if result else None
    except Exception as e:
        logger.debug("parse_properties failed: %s", e)
        return None


def parse_gitignore(data: bytes, encoding: str) -> dict | list | None:
    try:
        text = data.decode(encoding, errors='replace')
        patterns = [
            line.strip()
            for line in text.splitlines()
            if line.strip() and not line.strip().startswith('#')
        ]
        return {"patterns": patterns} if patterns else None
    except Exception as e:
        logger.debug("parse_gitignore failed: %s", e)
        return None


# =============================================================================
# 2단계 — 반구조 포맷
# =============================================================================

def parse_dockerfile(data: bytes, encoding: str) -> dict | list | None:
    """Dockerfile → {instruction: [args, ...]}."""
    try:
        # dockerfile-parse 는 디렉토리를 받지만, content 모드도 있음
        from dockerfile_parse import DockerfileParser
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
    except ImportError:
        # dockerfile-parse 미설치 시 간단 파싱
        return _parse_dockerfile_simple(data, encoding)
    except Exception as e:
        logger.debug("parse_dockerfile failed: %s", e)
        return _parse_dockerfile_simple(data, encoding)


def _parse_dockerfile_simple(data: bytes, encoding: str) -> dict | list | None:
    """dockerfile-parse 실패 시 fallback (간단 파싱)."""
    try:
        text = data.decode(encoding, errors='replace')
        result = {}
        current_line = ''
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith('#'):
                continue

            # 다음 줄 계속 (\ 로 끝나면)
            if current_line:
                current_line += ' ' + stripped.rstrip('\\').strip()
            else:
                current_line = stripped.rstrip('\\').strip()

            if not stripped.endswith('\\'):
                parts = current_line.split(None, 1)
                if len(parts) >= 2:
                    instr = parts[0].upper()
                    value = parts[1]
                    result.setdefault(instr, []).append(value)
                current_line = ''

        return result if result else None
    except Exception as e:
        logger.debug("_parse_dockerfile_simple failed: %s", e)
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
    'properties': parse_properties,
    # 'dockerfile' / 'gitignore' 는 일단 제외 — 필요 시 재추가
}


def library_parse(fmt: str, data: bytes, encoding: str) -> dict | list | None:
    """포맷에 맞는 핸들러를 호출. 해당 포맷이 없거나 실패하면 None."""
    handler = LIBRARY_HANDLERS.get(fmt)
    if not handler:
        return None
    return handler(data, encoding)
