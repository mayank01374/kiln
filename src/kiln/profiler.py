from __future__ import annotations

import math
import re
from collections import Counter
from datetime import datetime
from typing import Any

from .models import ColumnProfile, Relationship, SourceProfile, ValueCount

DATE_FORMATS = ["%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y%m%d"]
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
UUID = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-"
    r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)


def normalize_header(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")


def _shape(value: str) -> str:
    shape = re.sub(r"[A-Z]", "A", str(value))
    shape = re.sub(r"[a-z]", "a", shape)
    shape = re.sub(r"\d", "9", shape)
    shape = re.sub(r"(.)\1+", r"\1+", shape)
    return shape[:80]


def _date_candidates(values: list[str]) -> list[str]:
    candidates: list[str] = []
    sample = values[:100]
    for date_format in DATE_FORMATS:
        hits = 0
        for value in sample:
            try:
                datetime.strptime(value.strip(), date_format)  # noqa: DTZ007
                hits += 1
            except ValueError:
                pass
        if sample and hits / len(sample) >= 0.8:
            candidates.append(date_format)
    return candidates


def _physical_type(values: list[Any]) -> str:
    kinds: set[str] = set()
    for value in values:
        if isinstance(value, bool):
            kinds.add("boolean")
        elif isinstance(value, int):
            kinds.add("integer")
        elif isinstance(value, float):
            kinds.add("float")
        elif isinstance(value, str):
            kinds.add("string")
        else:
            kinds.add(type(value).__name__.lower())
    if not kinds:
        return "unknown"
    if kinds <= {"integer", "float"}:
        return "float" if "float" in kinds else "integer"
    return next(iter(kinds)) if len(kinds) == 1 else "mixed"


def _numeric_values(values: list[str]) -> tuple[list[float], bool]:
    numbers: list[float] = []
    all_integers = True
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(number):
            continue
        numbers.append(number)
        all_integers = all_integers and number.is_integer()
    return numbers, all_integers


def _quantile(sorted_values: list[float], fraction: float) -> float:
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = (len(sorted_values) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[lower]
    weight = position - lower
    return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight


def _logical_types(
    values: list[str], numbers: list[float], all_integers: bool, date_formats: list[str]
) -> list[str]:
    if not values:
        return []
    types: list[str] = []
    threshold = max(1, math.ceil(len(values) * 0.8))
    lowered = {value.strip().lower() for value in values}
    if lowered <= {
        "true",
        "false",
        "1",
        "0",
        "yes",
        "no",
        "y",
        "n",
        "active",
        "inactive",
    }:
        types.append("boolean")
    if len(numbers) >= threshold:
        types.append("integer" if all_integers else "float")
    if date_formats:
        types.append("date")
    if sum(bool(EMAIL.match(value)) for value in values) >= threshold:
        types.append("email")
    if sum(bool(UUID.match(value)) for value in values) >= threshold:
        types.append("uuid")
    if sum(value.startswith(("http://", "https://")) for value in values) >= threshold:
        types.append("url")
    if not types:
        types.append("string")
    return types


def _character_classes(values: list[str]) -> list[str]:
    checks = {
        "digit": lambda value: any(character.isdigit() for character in value),
        "uppercase": lambda value: any(character.isupper() for character in value),
        "lowercase": lambda value: any(character.islower() for character in value),
        "whitespace": lambda value: any(character.isspace() for character in value),
        "punctuation": lambda value: any(
            not character.isalnum() and not character.isspace() for character in value
        ),
    }
    return [name for name, check in checks.items() if any(check(value) for value in values)]


def _column_names(rows: list[dict[str, object]]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for name in row:
            if name not in seen:
                seen.add(name)
                names.append(name)
    return names


def profile_rows(rows: list[dict[str, object]]) -> SourceProfile:
    if not rows:
        return SourceProfile(row_count=0, columns=[])

    row_count = len(rows)
    profiles: list[ColumnProfile] = []
    relationships: list[Relationship] = []
    for name in _column_names(rows):
        raw_values = [row.get(name) for row in rows]
        present = [value for value in raw_values if value not in (None, "")]
        values = [str(value) for value in present]
        counts = Counter(values)
        date_formats = _date_candidates(values)
        numbers, all_integers = _numeric_values(values)
        numeric_enough = bool(values) and len(numbers) / len(values) >= 0.8
        sorted_numbers = sorted(numbers)
        lengths = [len(value) for value in values]
        shapes = Counter(_shape(value) for value in values)

        if numeric_enough:
            minimum: float | str | None = min(numbers)
            maximum: float | str | None = max(numbers)
            mean = sum(numbers) / len(numbers)
            quantiles = {
                "0.25": _quantile(sorted_numbers, 0.25),
                "0.5": _quantile(sorted_numbers, 0.5),
                "0.75": _quantile(sorted_numbers, 0.75),
            }
            nonzero = sorted(abs(number) for number in numbers if number != 0)
            numeric_scale = _quantile(nonzero, 0.5) if nonzero else 0.0
        else:
            minimum = min(values) if values else None
            maximum = max(values) if values else None
            mean = None
            quantiles = {}
            numeric_scale = None

        sample_values = list(dict.fromkeys(values))[:5]
        profile = ColumnProfile(
            name=name,
            normalized_name=normalize_header(name),
            physical_type=_physical_type(present),
            logical_types=_logical_types(values, numbers, all_integers, date_formats),
            null_ratio=(row_count - len(present)) / row_count,
            approximate_cardinality=len(counts),
            uniqueness_ratio=(len(counts) / len(values)) if values else 0.0,
            min_value=minimum,
            max_value=maximum,
            mean=mean,
            quantiles=quantiles,
            min_length=min(lengths) if lengths else None,
            max_length=max(lengths) if lengths else None,
            mean_length=(sum(lengths) / len(lengths)) if lengths else None,
            top_values=[
                ValueCount(value=value, count=count) for value, count in counts.most_common(5)
            ],
            regex_patterns=[shape for shape, _ in shapes.most_common(5)],
            date_format_candidates=date_formats,
            numeric_scale=numeric_scale,
            character_classes=_character_classes(values),
            sample_values=sample_values,
        )
        profiles.append(profile)

        if profile.null_ratio == 0 and profile.uniqueness_ratio == 1:
            relationships.append(
                Relationship(
                    kind="candidate_key",
                    columns=[name],
                    score=1.0,
                    detail="non-null values are unique in the observed data",
                )
            )

    return SourceProfile(
        row_count=row_count,
        columns=profiles,
        relationships=relationships,
    )
