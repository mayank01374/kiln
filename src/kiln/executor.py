from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from .models import (
    ErrorPolicy,
    ExecutionResult,
    Expression,
    RowOutcome,
    TransformationProgram,
)


class QuarantineRow(Exception):
    pass


class FailRun(Exception):
    pass


def _handle_error(policy: ErrorPolicy, message: str) -> None:
    if policy == "set_null":
        return
    if policy == "quarantine_row":
        raise QuarantineRow(message)
    raise FailRun(message)


def _cast(value: Any, kind: str) -> Any:
    if value in (None, ""):
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
        if s in {"true", "1", "yes", "y", "a", "active"}:
            return True
        if s in {"false", "0", "no", "n", "i", "inactive"}:
            return False
        raise ValueError(f"cannot cast {value!r} to boolean")
    raise ValueError(kind)


def _number(value: Any) -> float:
    if value in (None, ""):
        raise ValueError("numeric input is null")
    return float(value)


def _predicate(expr, row: dict[str, Any], targets: dict[str, Any]) -> bool:
    left = eval_expr(expr.left, row, targets)
    right = eval_expr(expr.right, row, targets) if expr.right is not None else None
    if expr.operator == "==":
        return left == right
    if expr.operator == "!=":
        return left != right
    if expr.operator == ">":
        return left > right
    if expr.operator == ">=":
        return left >= right
    if expr.operator == "<":
        return left < right
    if expr.operator == "<=":
        return left <= right
    if expr.operator == "is_null":
        return left is None
    if expr.operator == "not_null":
        return left is not None
    if expr.operator == "in":
        return left in (right or [])
    if expr.operator == "not_in":
        return left not in (right or [])
    raise ValueError(expr.operator)


def eval_expr(expr: Expression, row: dict[str, Any], targets: dict[str, Any] | None = None) -> Any:
    if targets is None:
        targets = {}
    if expr.op == "source":
        return row.get(expr.column)
    if expr.op == "target":
        return targets.get(expr.field)
    if expr.op == "literal":
        return expr.value
    if expr.op == "cast":
        try:
            return _cast(eval_expr(expr.value, row, targets), expr.type)
        except (TypeError, ValueError, OverflowError) as exc:
            return _handle_error(expr.on_error, str(exc))
    if expr.op == "parse_date":
        value = eval_expr(expr.value, row, targets)
        if value in (None, ""):
            return None
        for fmt in expr.formats:
            try:
                # The DSL parses calendar dates, so timezone information is not applicable.
                return datetime.strptime(str(value).strip(), fmt).date().isoformat()  # noqa: DTZ007
            except ValueError:
                continue
        return _handle_error(expr.on_error, f"no date format matched {value!r}")
    if expr.op == "trim":
        value = eval_expr(expr.value, row, targets)
        return None if value is None else str(value).strip()
    if expr.op == "lowercase":
        value = eval_expr(expr.value, row, targets)
        return None if value is None else str(value).lower()
    if expr.op == "uppercase":
        value = eval_expr(expr.value, row, targets)
        return None if value is None else str(value).upper()
    if expr.op == "normalize_whitespace":
        value = eval_expr(expr.value, row, targets)
        return None if value is None else re.sub(r"\s+", " ", str(value)).strip()
    if expr.op == "split":
        value = eval_expr(expr.value, row, targets)
        if value is None:
            return None
        pieces = str(value).split(expr.delimiter)
        try:
            return pieces[expr.index]
        except IndexError:
            return None
    if expr.op == "substring":
        value = eval_expr(expr.value, row, targets)
        if value is None:
            return None
        text = str(value)
        return (
            text[expr.start :]
            if expr.length is None
            else text[expr.start : expr.start + expr.length]
        )
    if expr.op == "regex_extract":
        value = eval_expr(expr.value, row, targets)
        if value is None:
            return None
        match = re.search(expr.pattern, str(value))
        if not match:
            return None
        try:
            return match.group(expr.group)
        except IndexError:
            return None
    if expr.op == "concat":
        values = [eval_expr(value, row, targets) for value in expr.values]
        if any(v is None for v in values):
            return None
        return expr.separator.join(str(v) for v in values)
    if expr.op == "coalesce":
        for child in expr.values:
            value = eval_expr(child, row, targets)
            if value not in (None, ""):
                return value
        return None
    if expr.op in {"map_values", "lookup"}:
        value = eval_expr(expr.value, row, targets)
        if value is None:
            return expr.default
        mapping = expr.mapping if expr.op == "map_values" else expr.table
        return mapping.get(str(value), expr.default)
    if expr.op in {"add", "subtract", "multiply", "divide"}:
        try:
            left = _number(eval_expr(expr.left, row, targets))
            right = _number(eval_expr(expr.right, row, targets))
            if expr.op == "add":
                return left + right
            if expr.op == "subtract":
                return left - right
            if expr.op == "multiply":
                return left * right
            if right == 0:
                raise ZeroDivisionError("division by zero")
            return left / right
        except (TypeError, ValueError, ZeroDivisionError, OverflowError) as exc:
            return _handle_error(expr.on_error, str(exc))
    if expr.op == "unit_convert":
        try:
            return _number(eval_expr(expr.value, row, targets)) * expr.factor + expr.offset
        except (TypeError, ValueError, OverflowError) as exc:
            return _handle_error(expr.on_error, str(exc))
    if expr.op == "conditional":
        branch = expr.if_true if _predicate(expr.condition, row, targets) else expr.if_false
        return eval_expr(branch, row, targets)
    raise ValueError(f"Unsupported expression {expr.op}")


def _target_references(expr: Expression) -> set[str]:
    if expr.op == "target":
        return {expr.field}
    if expr.op in {"source", "literal"}:
        return set()
    if expr.op in {
        "cast",
        "parse_date",
        "trim",
        "lowercase",
        "uppercase",
        "normalize_whitespace",
        "split",
        "substring",
        "regex_extract",
        "map_values",
        "lookup",
        "unit_convert",
    }:
        return _target_references(expr.value)
    if expr.op in {"concat", "coalesce"}:
        return set().union(*(_target_references(value) for value in expr.values))
    if expr.op in {"add", "subtract", "multiply", "divide"}:
        return _target_references(expr.left) | _target_references(expr.right)
    if expr.op == "conditional":
        references = _target_references(expr.condition.left)
        if expr.condition.right is not None:
            references |= _target_references(expr.condition.right)
        return references | _target_references(expr.if_true) | _target_references(expr.if_false)
    raise ValueError(f"Unsupported expression {expr.op}")


def _dependency_order(program: TransformationProgram) -> tuple[list[str], list[str]]:
    targets = set(program.mappings)
    dependencies = {
        target: _target_references(expression) for target, expression in program.mappings.items()
    }
    errors = [
        f"{target}: unknown target reference {reference!r}"
        for target, references in dependencies.items()
        for reference in sorted(references - targets)
    ]
    order: list[str] = []
    state: dict[str, int] = {}

    def visit(target: str, path: list[str]) -> None:
        if state.get(target) == 2:
            return
        if state.get(target) == 1:
            cycle = " -> ".join([*path, target])
            errors.append(f"cyclic target reference: {cycle}")
            return
        state[target] = 1
        for dependency in dependencies[target]:
            if dependency in targets:
                visit(dependency, [*path, target])
        state[target] = 2
        order.append(target)

    for target in program.mappings:
        visit(target, [])
    return order, errors


def execute(rows: list[dict[str, Any]], program: TransformationProgram) -> ExecutionResult:
    order, errors = _dependency_order(program)
    if errors:
        raise FailRun("; ".join(errors))

    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    outcomes: list[RowOutcome] = []
    for index, row in enumerate(rows):
        targets: dict[str, Any] = {}
        try:
            for target in order:
                targets[target] = eval_expr(program.mappings[target], row, targets)
            accepted.append({target: targets.get(target) for target in program.mappings})
            outcomes.append(RowOutcome(row_index=index, status="ACCEPTED"))
        except QuarantineRow as exc:
            rejected.append({"_row_index": index, "_reason": str(exc), **row})
            outcomes.append(
                RowOutcome(
                    row_index=index,
                    status="QUARANTINED",
                    reason_code="EXPRESSION_ERROR",
                )
            )

    return ExecutionResult(
        rows=accepted,
        rejected_rows=rejected,
        outcomes=outcomes,
        rows_input=len(rows),
        rows_output=len(accepted),
        rows_quarantined=len(rejected),
        rows_filtered=0,
    )


def execute_python(
    rows: list[dict[str, Any]], program: TransformationProgram
) -> list[dict[str, Any]]:
    """Compatibility helper returning accepted rows only."""
    return execute(rows, program).rows
