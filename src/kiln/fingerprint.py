from __future__ import annotations

import hashlib
import json
import math

from .models import ColumnProfile, DriftDistance, SchemaFingerprint, SourceProfile


def _hash(payload: object) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _scale_bucket(value: float | None) -> float | None:
    if value is None:
        return None
    if value == 0:
        return 0.0
    return round(math.log10(abs(value)), 2)


def _structural_payload(profile: SourceProfile) -> dict[str, object]:
    return {
        "columns": [
            {
                "name": column.normalized_name,
                "physical_type": column.physical_type,
            }
            for column in sorted(profile.columns, key=lambda item: item.normalized_name)
        ]
    }


def _semantic_payload(profile: SourceProfile) -> dict[str, object]:
    return {
        "columns": [
            {
                "name": column.normalized_name,
                "logical_types": sorted(column.logical_types),
                "null_ratio": round(column.null_ratio, 2),
                "uniqueness_ratio": round(column.uniqueness_ratio, 2),
                "date_formats": sorted(column.date_format_candidates),
                "patterns": sorted(column.regex_patterns),
                "length_range": [column.min_length, column.max_length],
                "numeric_scale": _scale_bucket(column.numeric_scale),
                "character_classes": sorted(column.character_classes),
            }
            for column in sorted(profile.columns, key=lambda item: item.normalized_name)
        ]
    }


def schema_fingerprint(profile: SourceProfile) -> SchemaFingerprint:
    structural_payload = _structural_payload(profile)
    semantic_payload = _semantic_payload(profile)
    return SchemaFingerprint(
        structural_hash=_hash(structural_payload),
        semantic_hash=_hash(semantic_payload),
        structural_payload=structural_payload,
        semantic_payload=semantic_payload,
    )


def fingerprint(profile: SourceProfile) -> SchemaFingerprint:
    """Return both structural and semantic fingerprints."""
    return schema_fingerprint(profile)


def structural_fingerprint(profile: SourceProfile) -> str:
    """Compatibility helper used by the current exact-match registry."""
    return schema_fingerprint(profile).structural_hash


def semantic_fingerprint(profile: SourceProfile) -> str:
    return schema_fingerprint(profile).semantic_hash


def _jaccard_distance(left: set[str], right: set[str]) -> float:
    union = left | right
    if not union:
        return 0.0
    return 1 - len(left & right) / len(union)


def _relative_distance(left: float | None, right: float | None) -> float:
    if left is None and right is None:
        return 0.0
    if left is None or right is None:
        return 1.0
    denominator = max(abs(left), abs(right), 1.0)
    return min(abs(left - right) / denominator, 1.0)


def _top_value_distance(left: ColumnProfile, right: ColumnProfile) -> float:
    if left.uniqueness_ratio > 0.8 or right.uniqueness_ratio > 0.8:
        return 0.0
    left_total = sum(item.count for item in left.top_values)
    right_total = sum(item.count for item in right.top_values)
    if left_total == 0 and right_total == 0:
        return 0.0
    if left_total == 0 or right_total == 0:
        return 1.0
    left_distribution = {item.value: item.count / left_total for item in left.top_values}
    right_distribution = {item.value: item.count / right_total for item in right.top_values}
    values = set(left_distribution) | set(right_distribution)
    return min(
        0.5
        * sum(
            abs(left_distribution.get(value, 0) - right_distribution.get(value, 0))
            for value in values
        ),
        1.0,
    )


def _distribution_distance(left: ColumnProfile, right: ColumnProfile) -> float:
    uniqueness = abs(left.uniqueness_ratio - right.uniqueness_ratio)
    mean_length = _relative_distance(left.mean_length, right.mean_length)
    if left.numeric_scale in (None, 0) and right.numeric_scale in (None, 0):
        numeric_scale = 0.0
    elif left.numeric_scale in (None, 0) or right.numeric_scale in (None, 0):
        numeric_scale = 1.0
    else:
        numeric_scale = min(
            abs(math.log10(abs(left.numeric_scale / right.numeric_scale))),
            1.0,
        )
    top_values = _top_value_distance(left, right)
    return min(
        0.25 * uniqueness + 0.2 * mean_length + 0.3 * numeric_scale + 0.25 * top_values,
        1.0,
    )


def drift_distance(previous: SourceProfile, current: SourceProfile) -> DriftDistance:
    old_columns = {column.normalized_name: column for column in previous.columns}
    new_columns = {column.normalized_name: column for column in current.columns}
    old_names = set(old_columns)
    new_names = set(new_columns)
    all_names = old_names | new_names
    common = old_names & new_names

    if not all_names:
        return DriftDistance(
            total=0.0,
            header=0.0,
            logical_type=0.0,
            null_rate=0.0,
            format_pattern=0.0,
            value_distribution=0.0,
            classification="EXACT",
        )
    if not common:
        return DriftDistance(
            total=1.0,
            header=1.0,
            logical_type=1.0,
            null_rate=1.0,
            format_pattern=1.0,
            value_distribution=1.0,
            classification="UNKNOWN",
            changed_columns=sorted(all_names),
        )

    header = _jaccard_distance(old_names, new_names)
    logical_type = sum(
        _jaccard_distance(
            set(old_columns[name].logical_types),
            set(new_columns[name].logical_types),
        )
        for name in common
    ) / len(common)
    null_rate = sum(
        min(abs(old_columns[name].null_ratio - new_columns[name].null_ratio), 1.0)
        for name in common
    ) / len(common)
    format_pattern = sum(
        0.7
        * (
            set(old_columns[name].date_format_candidates)
            != set(new_columns[name].date_format_candidates)
        )
        + 0.3
        * _jaccard_distance(
            set(old_columns[name].regex_patterns),
            set(new_columns[name].regex_patterns),
        )
        for name in common
    ) / len(common)
    value_distribution = sum(
        _distribution_distance(old_columns[name], new_columns[name]) for name in common
    ) / len(common)

    total = min(
        0.35 * header
        + 0.2 * logical_type
        + 0.1 * null_rate
        + 0.25 * format_pattern
        + 0.1 * value_distribution,
        1.0,
    )
    old_fingerprint = schema_fingerprint(previous)
    new_fingerprint = schema_fingerprint(current)
    date_representation_changed = any(
        set(old_columns[name].date_format_candidates)
        != set(new_columns[name].date_format_candidates)
        for name in common
    )
    if (
        old_fingerprint.structural_hash == new_fingerprint.structural_hash
        and old_fingerprint.semantic_hash == new_fingerprint.semantic_hash
    ):
        classification = "EXACT"
    elif date_representation_changed:
        classification = "DRIFTED"
    elif total < 0.2:
        classification = "COMPATIBLE"
    else:
        classification = "DRIFTED"

    changed_columns = sorted(old_names ^ new_names)
    for name in sorted(common):
        old = old_columns[name]
        new = new_columns[name]
        if (
            set(old.logical_types) != set(new.logical_types)
            or abs(old.null_ratio - new.null_ratio) >= 0.1
            or set(old.date_format_candidates) != set(new.date_format_candidates)
            or set(old.regex_patterns) != set(new.regex_patterns)
            or _distribution_distance(old, new) >= 0.2
        ):
            changed_columns.append(name)

    return DriftDistance(
        total=round(total, 6),
        header=round(header, 6),
        logical_type=round(logical_type, 6),
        null_rate=round(null_rate, 6),
        format_pattern=round(format_pattern, 6),
        value_distribution=round(value_distribution, 6),
        classification=classification,
        changed_columns=sorted(set(changed_columns)),
    )


def schema_distance(previous: SourceProfile, current: SourceProfile) -> float:
    """Compatibility helper returning only the aggregate distance."""
    return drift_distance(previous, current).total
