from __future__ import annotations

import hashlib
import json
import re

from .models import SourceProfile


def normalize_header(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")


def structural_fingerprint(profile: SourceProfile) -> str:
    payload = [
        {
            "name": normalize_header(c.name),
            "date_formats": c.date_format_candidates,
            "shape": c.value_shape,
        }
        for c in profile.columns
    ]
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def schema_distance(a: SourceProfile, b: SourceProfile) -> float:
    a_cols = {normalize_header(c.name): c for c in a.columns}
    b_cols = {normalize_header(c.name): c for c in b.columns}
    all_names = set(a_cols) | set(b_cols)
    if not all_names:
        return 0.0
    header_diff = 1 - (len(set(a_cols) & set(b_cols)) / len(all_names))
    common = set(a_cols) & set(b_cols)
    if not common:
        return 1.0
    format_diff = sum(
        a_cols[n].date_format_candidates != b_cols[n].date_format_candidates for n in common
    ) / len(common)
    null_diff = sum(
        min(abs(a_cols[n].null_ratio - b_cols[n].null_ratio), 1.0) for n in common
    ) / len(common)
    return min(1.0, 0.6 * header_diff + 0.25 * format_diff + 0.15 * null_diff)
