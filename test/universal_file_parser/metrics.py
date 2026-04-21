"""Ground truth 대비 정확도 측정

Key-level precision/recall + value exact match 계산.
"""

from .flatten import flatten


def measure_accuracy(parsed, expected) -> dict:
    """
    Returns:
        {
          "precision": float,
          "recall": float,
          "f1": float,
          "value_exact_match_rate": float,  # 공통 key 중 값까지 정확 일치하는 비율
          "missed_keys": [str],             # expected 에는 있고 parsed 에 없는 key (최대 5)
          "extra_keys": [str],              # parsed 에는 있고 expected 에 없는 key (최대 5)
        }
    """
    parsed_flat = flatten(parsed) if parsed else {}
    expected_flat = flatten(expected) if expected else {}

    parsed_keys = set(parsed_flat.keys())
    expected_keys = set(expected_flat.keys())

    tp = len(parsed_keys & expected_keys)
    precision = tp / len(parsed_keys) if parsed_keys else 0.0
    recall = tp / len(expected_keys) if expected_keys else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

    # 값 정확 일치
    common = parsed_keys & expected_keys
    exact = sum(1 for k in common if str(parsed_flat[k]) == str(expected_flat[k]))
    exact_rate = exact / len(common) if common else 0.0

    return {
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
        "value_exact_match_rate": round(exact_rate, 3),
        "parsed_key_count": len(parsed_keys),
        "expected_key_count": len(expected_keys),
        "missed_keys": sorted(expected_keys - parsed_keys)[:5],
        "extra_keys": sorted(parsed_keys - expected_keys)[:5],
    }
