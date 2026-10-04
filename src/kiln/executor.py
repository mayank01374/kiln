from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from .models import Expression, TransformationProgram


def _cast(value: Any, kind: str) -> Any:
    if value is None or value == "":
        return None
    if kind == "string":
        return str(value)
    if kind == "integer":
        return int(float(value))
    if kind == "float":
        return float(value)
    if kind == "boolean":
        if isinstance(value, bool):
            return value
        s = str(value).strip().lower()
        if s in {"true", "1", "yes", "y"}:
            return True
        if s in {"false", "0", "no", "n"}:
            return False
        return None
    raise ValueError(kind)


def eval_expr(expr: Expression, row: dict[str, Any]) -> Any:
    if expr.op == "source":
        return row.get(expr.column)
    if expr.op == "literal":
        return expr.value
    if expr.op == "cast":
        return _cast(eval_expr(expr.value, row), expr.type)
    if expr.op == "parse_date":
        value = eval_expr(expr.value, row)
        if value in (None, ""):
            return None
        for fmt in expr.formats:
            try:
                # The DSL parses calendar dates, so timezone information is not applicable.
                return datetime.strptime(str(value).strip(), fmt).date().isoformat()  # noqa: DTZ007
            except ValueError:
                continue
        return None
    if expr.op == "trim":
        value = eval_expr(expr.value, row)
        return None if value is None else str(value).strip()
    if expr.op == "lowercase":
        value = eval_expr(expr.value, row)
        return None if value is None else str(value).lower()
    if expr.op == "uppercase":
        value = eval_expr(expr.value, row)
        return None if value is None else str(value).upper()
    if expr.op == "normalize_whitespace":
        value = eval_expr(expr.value, row)
        return None if value is None else " ".join(str(value).split())
    if expr.op == "split":
        value = eval_expr(expr.value, row)
        if value is None:
            return None
        pieces = str(value).split(expr.delimiter)
        try:
            return pieces[expr.index]
        except IndexError:
            return None
    if expr.op == "concat":
        values = [eval_expr(v, row) for v in expr.values]
        if any(v is None for v in values):
            return None
        return expr.separator.join(str(v) for v in values)
    if expr.op == "map_values":
        value = eval_expr(expr.value, row)
        if value is None:
            return expr.default
        return expr.mapping.get(str(value), expr.default)
    if expr.op == "coalesce":
        for child in expr.values:
            value = eval_expr(child, row)
            if value not in (None, ""):
                return value
        return None
    if expr.op == "regex_extract":
        value = eval_expr(expr.value, row)
        if value is None:
            return None
        match = re.search(expr.pattern, str(value))
        return match.group(expr.group) if match else None
    raise ValueError(f"Unsupported op {expr.op}")


def execute_python(
    rows: list[dict[str, Any]], program: TransformationProgram
) -> list[dict[str, Any]]:
    return [
        {target: eval_expr(expr, row) for target, expr in program.mappings.items()} for row in rows
    ]
