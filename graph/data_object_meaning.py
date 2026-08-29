"""Closed product-property patch for TABLE and COLUMN Analyzer meanings."""
from __future__ import annotations

import re


_ALLOWED_KEYS = {
    "id",
    "logical_name",
    "analyzed_description",
    "meaning_status",
    "missing_context_count",
    "missing_context_artifact_hash",
}


def validated_data_object_meaning_item(value: dict, *, entity_kind: str) -> dict:
    """Reject extra, missing and coupled-presence violations before graph mutation."""
    item = dict(value)
    extra = set(item) - _ALLOWED_KEYS
    if extra:
        raise ValueError(f"{entity_kind} meaning patch has extra fields: {sorted(extra)}")
    entity_id = str(item.get("id") or "").strip()
    status = item.get("meaning_status")
    count = item.get("missing_context_count")
    logical_name = str(item.get("logical_name") or "").strip()
    description = str(item.get("analyzed_description") or "").strip()
    artifact_hash = str(item.get("missing_context_artifact_hash") or "")
    if not entity_id:
        raise ValueError(f"{entity_kind} meaning patch id is blank")
    if status not in {"sufficient", "insufficient"}:
        raise ValueError(f"{entity_kind} meaning status is invalid: {entity_id}")
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        raise ValueError(f"{entity_kind} missing-context count is invalid: {entity_id}")
    if status == "sufficient":
        if not logical_name or not description or count != 0 or artifact_hash:
            raise ValueError(
                f"{entity_kind} sufficient meaning presence is invalid: {entity_id}"
            )
    elif logical_name or description or count < 1 or not re.fullmatch(
        r"[0-9a-f]{64}", artifact_hash
    ):
        raise ValueError(
            f"{entity_kind} insufficient meaning presence is invalid: {entity_id}"
        )
    if status == "insufficient":
        item.pop("logical_name", None)
        item.pop("analyzed_description", None)
    if not artifact_hash:
        item.pop("missing_context_artifact_hash", None)
    return item
