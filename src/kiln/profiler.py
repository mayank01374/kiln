from __future__ import annotations

import re
from collections import Counter
from datetime import datetime

from .models import ColumnProfile, SourceProfile

DATE_FORMATS = ["%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y%m%d"]


def _shape(value: str) -> str:
    s = str(value)
    s = re.sub(r"[A-Z]", "A", s)
    s = re.sub(r"[a-z]", "a", s)
    s = re.sub(r"\d", "9", s)
    s = re.sub(r"(.)\1+", r"\1+", s)
    return s[:80]


def _date_candidates(values: list[str]) -> list[str]:
    candidates = []
    for fmt in DATE_FORMATS:
        checked = 0
        hits = 0
        for value in values[:50]:
            if value in (None, ""):
                continue
            checked += 1
            try:
                # Only the date layout is being detected; no timezone is needed.
                datetime.strptime(str(value).strip(), fmt)  # noqa: DTZ007
                hits += 1
            except ValueError:
                pass
        if checked and hits / checked >= 0.8:
            candidates.append(fmt)
    return candidates


def profile_rows(rows: list[dict[str, object]]) -> SourceProfile:
    if not rows:
        return SourceProfile(row_count=0, columns=[])
    names = list(rows[0].keys())
    profiles: list[ColumnProfile] = []
    n = len(rows)
    for name in names:
        vals = [row.get(name) for row in rows]
        non_null = [str(v) for v in vals if v not in (None, "")]
        counter = Counter(non_null)
        profiles.append(
            ColumnProfile(
                name=name,
                physical_type="string",
                null_ratio=(n - len(non_null)) / n,
                uniqueness_ratio=(len(counter) / len(non_null)) if non_null else 0.0,
                top_values=counter.most_common(5),
                sample_values=non_null[:5],
                date_format_candidates=_date_candidates(non_null),
                value_shape=_shape(non_null[0]) if non_null else "",
            )
        )
    return SourceProfile(row_count=n, columns=profiles)
