"""Authoritative project glossary input.

Automatic business-term inference is deliberately outside Analyzer's code
summary contract.  Operators may provide ``analysis/glossary.yaml`` under the
corpus root; absent input means an explicitly empty glossary.  The loader is
strict so malformed human input cannot be silently normalized into graph data.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictStr,
    ValidationError,
    model_validator,
)
import yaml

from shared.files.run_cache_context import read_run_bytes


GLOSSARY_RELATIVE_PATH = Path("analysis") / "glossary.yaml"


class _HumanGlossaryTermInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    canonical: StrictStr = Field(..., min_length=1)
    aliases: list[StrictStr]
    meaning: StrictStr = Field(..., min_length=1)
    category: StrictStr

    @model_validator(mode="after")
    def _reject_implicit_cleanup(self) -> "_HumanGlossaryTermInput":
        values = [self.canonical, self.meaning, self.category, *self.aliases]
        if any(value != value.strip() for value in values):
            raise ValueError("glossary strings must not have surrounding whitespace")
        if any(not alias for alias in self.aliases):
            raise ValueError("glossary aliases must not be empty")
        folded = [alias.casefold() for alias in self.aliases]
        if len(folded) != len(set(folded)):
            raise ValueError(f"duplicate aliases for glossary term {self.canonical!r}")
        if self.canonical.casefold() in set(folded):
            raise ValueError(
                f"glossary alias duplicates canonical term {self.canonical!r}"
            )
        return self


class _HumanGlossaryInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal["1.0"]
    terms: list[_HumanGlossaryTermInput]

    @model_validator(mode="after")
    def _reject_cross_term_collisions(self) -> "_HumanGlossaryInput":
        owners: dict[str, str] = {}
        for term in self.terms:
            for spelling in (term.canonical, *term.aliases):
                key = spelling.casefold()
                previous = owners.get(key)
                if previous is not None:
                    raise ValueError(
                        f"glossary spelling {spelling!r} collides with {previous!r}"
                    )
                owners[key] = term.canonical
        return self


@dataclass(frozen=True)
class HumanGlossaryTerm:
    canonical: str
    aliases: tuple[str, ...]
    meaning: str
    category: str

    def graph_dict(self) -> dict:
        return {
            "canonical": self.canonical,
            "colloquial": list(self.aliases),
            "meaning": self.meaning,
            "category": self.category,
        }


@dataclass(frozen=True)
class HumanGlossary:
    source_path: Path
    supplied: bool
    schema_version: str | None
    sha256: str | None
    terms: tuple[HumanGlossaryTerm, ...]

    def manifest_dict(self, corpus_root: Path) -> dict:
        return {
            "supplied": self.supplied,
            "source": self.source_path.relative_to(corpus_root).as_posix(),
            "schema_version": self.schema_version,
            "sha256": self.sha256,
            "term_count": len(self.terms),
        }


def load_human_glossary(corpus_root: Path) -> HumanGlossary:
    """Load the canonical optional glossary without modifying filesystem state."""
    root = corpus_root.expanduser().resolve()
    path = root / GLOSSARY_RELATIVE_PATH
    if not path.exists():
        return HumanGlossary(path, False, None, None, ())
    if not path.is_file():
        raise ValueError(f"human glossary path is not a file: {path}")

    payload = read_run_bytes(path)
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ValueError(f"human glossary must be strict UTF-8: {path}") from error
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as error:
        mark = getattr(error, "problem_mark", None)
        location = (
            f" line={mark.line + 1} column={mark.column + 1}"
            if mark is not None else ""
        )
        raise ValueError(f"invalid human glossary YAML: {path}:{location}") from error

    try:
        parsed = _HumanGlossaryInput.model_validate(raw)
    except ValidationError as error:
        # Pydantic's default text includes rejected values.  A glossary may be
        # customer data, so surface only structural locations and error types.
        violations = [
            {
                "location": ".".join(str(part) for part in item["loc"]),
                "type": item["type"],
            }
            for item in error.errors(include_url=False, include_context=False, include_input=False)
        ]
        raise ValueError(
            f"invalid human glossary contract: {path}; violations={violations}"
        ) from error
    terms = tuple(
        HumanGlossaryTerm(
            canonical=item.canonical,
            aliases=tuple(item.aliases),
            meaning=item.meaning,
            category=item.category,
        )
        for item in parsed.terms
    )
    return HumanGlossary(
        source_path=path,
        supplied=True,
        schema_version=parsed.schema_version,
        sha256=hashlib.sha256(payload).hexdigest(),
        terms=terms,
    )
