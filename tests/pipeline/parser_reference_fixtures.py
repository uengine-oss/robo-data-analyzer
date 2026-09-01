from __future__ import annotations


def with_identifier_contract(root: dict) -> dict:
    """Mark hand-authored Parser AST nodes as having an empty reference ledger."""
    for child in root.get("children") or ():
        child.setdefault("identifierReferenceVersion", 1)
        child.setdefault("identifierReferences", [])
        with_identifier_contract(child)
    return root
