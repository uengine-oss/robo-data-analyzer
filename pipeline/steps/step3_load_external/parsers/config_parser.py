"""Deterministic config/data parsing into unbounded semantic entries.

Chunk limits belong to ``chunking.py``.  Parsers must not truncate values and
thereby make the persisted content hash lie about the source.
"""
from __future__ import annotations

import configparser
import csv
from dataclasses import dataclass
import io
import json
import re
import tomllib

import javaproperties
import yaml


_IDENTIFIER = re.compile(r"^[A-Za-z_가-힣][\w.\-가-힣]*$")


@dataclass(frozen=True)
class ConfigEntry:
    key: str
    value: str
    line: int


class ConfigParseError(ValueError):
    pass


def _flatten(prefix: str, value: object, output: list[tuple[str, str]]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            _flatten(f"{prefix}.{key}" if prefix else str(key), child, output)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _flatten(f"{prefix}[{index}]", child, output)
    else:
        output.append((prefix, "" if value is None else str(value)))


def _line_for_key(text: str, key: str) -> int:
    simple = key.rsplit(".", 1)[-1]
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.lstrip()
        if stripped.startswith(key) or stripped.startswith(simple):
            return number
    return 0


def _dedupe(entries: list[ConfigEntry]) -> list[ConfigEntry]:
    """Use format order with last-value semantics while keeping IDs unique."""
    by_key: dict[str, ConfigEntry] = {}
    for entry in entries:
        if entry.key.strip():
            by_key[entry.key] = entry
    return list(by_key.values())


def _properties(text: str) -> list[ConfigEntry]:
    try:
        data = javaproperties.loads(text)
    except Exception as error:
        raise ConfigParseError(f"invalid properties data: {type(error).__name__}") from error
    return [ConfigEntry(str(key), str(value), _line_for_key(text, str(key))) for key, value in data.items()]


def _structured(value: object, text: str) -> list[ConfigEntry]:
    flat: list[tuple[str, str]] = []
    _flatten("", value, flat)
    return [ConfigEntry(key, item, _line_for_key(text, key)) for key, item in flat if key]


def _ini(text: str) -> list[ConfigEntry]:
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        parser.read_string(text)
    except configparser.Error:
        return _properties(text)
    entries = [
        ConfigEntry(f"{section}.{key}", value, _line_for_key(text, key))
        for section in parser.sections()
        for key, value in parser.items(section)
    ]
    entries.extend(ConfigEntry(key, value, _line_for_key(text, key)) for key, value in parser.defaults().items())
    return entries or _properties(text)


def _delimited(text: str, delimiter: str) -> list[ConfigEntry]:
    try:
        rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    except csv.Error as error:
        raise ConfigParseError(f"invalid delimited data: {error}") from error
    if len(rows) < 2:
        return []
    header = rows[0]
    entries: list[ConfigEntry] = []
    for line, row in enumerate(rows[1:], 2):
        if not row or not _IDENTIFIER.fullmatch(row[0].strip()):
            continue
        value = "; ".join(f"{name}={cell}" for name, cell in zip(header, row) if cell.strip())
        entries.append(ConfigEntry(row[0].strip(), value, line))
    return entries


def parse_config(text: str, suffix: str) -> list[ConfigEntry]:
    """Parse one already-decoded config file; unsupported suffixes fail closed."""
    suffix = suffix.lower()
    try:
        if suffix in (".properties", ".env"):
            entries = _properties(text)
        elif suffix == ".json":
            entries = _structured(json.loads(text), text)
        elif suffix == ".jsonl":
            entries = []
            for index, line in enumerate(text.splitlines()):
                if line.strip():
                    parsed = _structured(json.loads(line), line)
                    entries.extend(
                        ConfigEntry(f"[{index}].{entry.key}", entry.value, index + 1)
                        for entry in parsed
                    )
        elif suffix == ".toml":
            entries = _structured(tomllib.loads(text), text)
        elif suffix in (".ini", ".cfg", ".conf"):
            entries = _ini(text)
        elif suffix in (".yaml", ".yml"):
            entries = []
            for document in yaml.safe_load_all(text):
                if isinstance(document, (dict, list)):
                    entries.extend(_structured(document, text))
        elif suffix == ".csv":
            entries = _delimited(text, ",")
        elif suffix == ".tsv":
            entries = _delimited(text, "\t")
        else:
            raise ConfigParseError(f"unsupported config suffix: {suffix!r}")
    except ConfigParseError:
        raise
    except (json.JSONDecodeError, tomllib.TOMLDecodeError, yaml.YAMLError) as error:
        raise ConfigParseError(f"invalid {suffix} data: {type(error).__name__}") from error
    return _dedupe(entries)
