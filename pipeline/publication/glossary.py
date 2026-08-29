"""DB-independent publication of the optional human-provided glossary."""
from __future__ import annotations

import hashlib

from graph.product_write_batch import (
    NodeCreateOperation,
    NodeIdentity,
    ProductRecordExpectation,
    ProductWriteBatch,
)
from pipeline.models.human_glossary import HumanGlossary


GLOSSARY_TERM_PROPERTY_KEYS = frozenset({
    "stable_id",
    "analysis_scope_id",
    "generation_id",
    "canonical_term",
    "aliases",
    "meaning",
    "category",
})


class GlossaryProjectionError(ValueError):
    """The validated human glossary cannot be projected without loss."""


def glossary_term_stable_id(canonical_term: str) -> str:
    """Return a stable opaque identity without copying term text into the ID."""
    if not isinstance(canonical_term, str) or not canonical_term:
        raise GlossaryProjectionError("canonical glossary term must be non-blank")
    digest = hashlib.sha256(canonical_term.encode("utf-8")).hexdigest()
    return f"glossary-term:{digest}"


def build_glossary_write_batch(
    *,
    analysis_scope_id: str,
    generation_id: str,
    glossary: HumanGlossary,
) -> ProductWriteBatch:
    """Create one closed GlossaryTerm product per supplied human term.

    File provenance already belongs to the sealed run manifest.  Repeating it
    on every term would add no product meaning, so only the exact user-provided
    term fields are published.
    """
    if not isinstance(glossary, HumanGlossary):
        raise GlossaryProjectionError("glossary must be a validated HumanGlossary")
    if not glossary.supplied and glossary.terms:
        raise GlossaryProjectionError("unsupplied glossary contains terms")
    if glossary.supplied and (
        not glossary.schema_version or not glossary.sha256
    ):
        raise GlossaryProjectionError(
            "supplied glossary is missing sealed schema/hash provenance"
        )

    operations: list[NodeCreateOperation] = []
    expectations: list[ProductRecordExpectation] = []
    seen_spellings: set[str] = set()
    seen_ids: set[str] = set()
    for term in glossary.terms:
        spellings = (term.canonical, *term.aliases)
        if any(not isinstance(value, str) or not value for value in (
            *spellings, term.meaning, term.category,
        )):
            raise GlossaryProjectionError("glossary term contains a blank field")
        folded = {value.casefold() for value in spellings}
        if len(folded) != len(spellings) or seen_spellings & folded:
            raise GlossaryProjectionError(
                f"glossary spelling is not globally unique: {term.canonical!r}"
            )
        seen_spellings.update(folded)

        stable_id = glossary_term_stable_id(term.canonical)
        if stable_id in seen_ids:
            raise GlossaryProjectionError(
                f"duplicate glossary product identity: {stable_id!r}"
            )
        seen_ids.add(stable_id)
        identity = NodeIdentity("GlossaryTerm", stable_id)
        properties = {
            "stable_id": stable_id,
            "analysis_scope_id": analysis_scope_id,
            "generation_id": generation_id,
            "canonical_term": term.canonical,
            "aliases": term.aliases,
            "meaning": term.meaning,
            "category": term.category,
        }
        operations.append(NodeCreateOperation.from_properties(identity, properties))
        expectations.append(ProductRecordExpectation.from_properties(
            identity=identity,
            closed_property_keys=GLOSSARY_TERM_PROPERTY_KEYS,
            present_properties=properties,
        ))

    return ProductWriteBatch.build(
        stage_name="glossary",
        analysis_scope_id=analysis_scope_id,
        generation_id=generation_id,
        node_create_operations=operations,
        expectations=expectations,
    )
