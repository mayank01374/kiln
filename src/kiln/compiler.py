from __future__ import annotations

from collections import defaultdict, deque
from typing import Any

from .models import (
    CompilerDiagnostic,
    Expression,
    SourceProfile,
    StaticAnalysis,
    TargetContract,
    TransformationProgram,
)


class CompileError(ValueError):
    pass


def _children(expr: Expression) -> list[Expression]:
    if expr.op in {"source", "target", "literal"}:
        return []
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
        return [expr.value]
    if expr.op in {"concat", "coalesce"}:
        return list(expr.values)
    if expr.op in {"add", "subtract", "multiply", "divide"}:
        return [expr.left, expr.right]
    if expr.op == "conditional":
        children = [expr.condition.left, expr.if_true, expr.if_false]
        if expr.condition.right is not None:
            children.append(expr.condition.right)
        return children
    raise CompileError(f"Unsupported operation: {expr.op}")


def referenced_columns(expr: Expression) -> set[str]:
    if expr.op == "source":
        return {expr.column}
    columns: set[str] = set()
    for child in _children(expr):
        columns |= referenced_columns(child)
    return columns


def referenced_targets(expr: Expression) -> set[str]:
    if expr.op == "target":
        return {expr.field}
    targets: set[str] = set()
    for child in _children(expr):
        targets |= referenced_targets(child)
    return targets


def dependency_order(program: TransformationProgram) -> tuple[list[str], list[str]]:
    fields = set(program.mappings)
    dependencies: dict[str, set[str]] = {}
    reverse: dict[str, set[str]] = defaultdict(set)
    errors: list[str] = []

    for target, expression in program.mappings.items():
        target_dependencies = referenced_targets(expression)
        unknown = target_dependencies - fields
        if unknown:
            errors.append(f"{target}: references unknown target fields {sorted(unknown)}")
        dependencies[target] = target_dependencies & fields
        for dependency in dependencies[target]:
            reverse[dependency].add(target)

    indegree = {target: len(deps) for target, deps in dependencies.items()}
    queue = deque(sorted(target for target, degree in indegree.items() if degree == 0))
    order: list[str] = []
    while queue:
        node = queue.popleft()
        order.append(node)
        for dependent in sorted(reverse[node]):
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                queue.append(dependent)

    if len(order) != len(fields):
        cycle = sorted(target for target, degree in indegree.items() if degree > 0)
        errors.append(f"cyclic target dependencies: {cycle}")
    return order, errors


def _scalar_type(value: Any) -> str:
    if value is None:
        return "unknown"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "float"
    return "string"


def infer_type(expr: Expression, target_types: dict[str, str]) -> str:
    if expr.op == "source":
        return "unknown"
    if expr.op == "target":
        return target_types.get(expr.field, "unknown")
    if expr.op == "literal":
        return _scalar_type(expr.value)
    if expr.op == "cast":
        return expr.type
    if expr.op == "parse_date":
        return "date"
    if expr.op in {
        "trim",
        "lowercase",
        "uppercase",
        "normalize_whitespace",
        "split",
        "substring",
        "regex_extract",
        "concat",
    }:
        return "string"
    if expr.op == "coalesce":
        types = {infer_type(value, target_types) for value in expr.values} - {"unknown"}
        return next(iter(types)) if len(types) == 1 else "unknown"
    if expr.op in {"map_values", "lookup"}:
        values = (
            list(expr.mapping.values()) if expr.op == "map_values" else list(expr.table.values())
        )
        if expr.default is not None:
            values.append(expr.default)
        types = {_scalar_type(value) for value in values} - {"unknown"}
        return next(iter(types)) if len(types) == 1 else "unknown"
    if expr.op in {"add", "subtract", "multiply", "divide", "unit_convert"}:
        return "float"
    if expr.op == "conditional":
        true_type = infer_type(expr.if_true, target_types)
        false_type = infer_type(expr.if_false, target_types)
        return true_type if true_type == false_type else "unknown"
    return "unknown"


def _walk_type_errors(expr: Expression, target: str, diagnostics: list[CompilerDiagnostic]) -> None:
    if expr.op in {
        "split",
        "substring",
        "regex_extract",
        "trim",
        "lowercase",
        "uppercase",
        "normalize_whitespace",
    } and expr.value.op in {"add", "subtract", "multiply", "divide", "unit_convert"}:
        diagnostics.append(
            CompilerDiagnostic(
                code="E201",
                target_field=target,
                message=f"{expr.op} requires string-like input",
            )
        )
    if expr.op in {"add", "subtract", "multiply", "divide"}:
        for child in (expr.left, expr.right):
            if child.op == "literal" and isinstance(child.value, str):
                diagnostics.append(
                    CompilerDiagnostic(
                        code="E202",
                        target_field=target,
                        message=f"{expr.op} received an obvious string literal",
                    )
                )
    for child in _children(expr):
        _walk_type_errors(child, target, diagnostics)


def static_check(
    program: TransformationProgram,
    contract: TargetContract,
    source_columns: set[str],
    profile: SourceProfile | None = None,
) -> StaticAnalysis:
    del profile  # Reserved for profile-aware type inference.
    diagnostics: list[CompilerDiagnostic] = []
    missing_targets = (
        contract.required_fields - set(program.mappings) - set(program.unresolved_fields)
    )
    if missing_targets:
        diagnostics.append(
            CompilerDiagnostic(
                code="E001",
                message=f"Missing required target mappings: {sorted(missing_targets)}",
            )
        )

    target_names = set(contract.field_map)
    unknown_targets = set(program.mappings) - target_names
    if unknown_targets:
        diagnostics.append(
            CompilerDiagnostic(
                code="E002",
                message=f"Mappings contain fields absent from contract: {sorted(unknown_targets)}",
            )
        )

    order, dependency_errors = dependency_order(program)
    diagnostics.extend(
        CompilerDiagnostic(code="E003", message=error) for error in dependency_errors
    )

    target_types = {name: field.type for name, field in contract.field_map.items()}
    inferred_types: dict[str, str] = {}
    for target, expression in program.mappings.items():
        missing_sources = referenced_columns(expression) - source_columns
        if missing_sources:
            diagnostics.append(
                CompilerDiagnostic(
                    code="E101",
                    target_field=target,
                    message=f"missing source columns {sorted(missing_sources)}",
                )
            )
        inferred_type = infer_type(expression, target_types)
        inferred_types[target] = inferred_type
        expected = target_types.get(target)
        compatible = (
            inferred_type == "unknown"
            or expected is None
            or inferred_type == expected
            or (expected == "enum" and inferred_type == "string")
            or (expected == "float" and inferred_type == "integer")
        )
        if not compatible:
            diagnostics.append(
                CompilerDiagnostic(
                    code="E102",
                    target_field=target,
                    message=(
                        f"target {target} requires {expected}; expression produces {inferred_type}"
                    ),
                )
            )
        _walk_type_errors(expression, target, diagnostics)

    direct_sources = {
        target: referenced_columns(expression) for target, expression in program.mappings.items()
    }
    target_dependencies = {
        target: referenced_targets(expression) for target, expression in program.mappings.items()
    }
    lineage: dict[str, list[str]] = {}
    for target in order:
        sources = set(direct_sources.get(target, set()))
        for dependency in target_dependencies.get(target, set()):
            sources.update(lineage.get(dependency, []))
        lineage[target] = sorted(sources)

    return StaticAnalysis(
        ok=not diagnostics,
        diagnostics=diagnostics,
        order=order,
        lineage=lineage,
        inferred_types=inferred_types,
    )


def compile_polars_expr(expr: Expression):
    """Compile a validated Kiln AST node into a Polars expression."""
    try:
        import polars as pl
    except ImportError as exc:  # pragma: no cover - dependency installed in normal use
        raise RuntimeError(
            "Polars is required for the production executor: pip install -e ."
        ) from exc

    if expr.op == "source":
        return pl.col(expr.column)
    if expr.op == "target":
        return pl.col(expr.field)
    if expr.op == "literal":
        return pl.lit(expr.value)
    if expr.op == "cast":
        dtype = {
            "string": pl.String,
            "integer": pl.Int64,
            "float": pl.Float64,
            "boolean": pl.Boolean,
        }[expr.type]
        return compile_polars_expr(expr.value).cast(dtype, strict=False)
    if expr.op == "parse_date":
        base = compile_polars_expr(expr.value).cast(pl.String)
        parsed = [base.str.strptime(pl.Date, fmt, strict=False) for fmt in expr.formats]
        return pl.coalesce(parsed)
    if expr.op == "trim":
        return compile_polars_expr(expr.value).cast(pl.String).str.strip_chars()
    if expr.op == "lowercase":
        return compile_polars_expr(expr.value).cast(pl.String).str.to_lowercase()
    if expr.op == "uppercase":
        return compile_polars_expr(expr.value).cast(pl.String).str.to_uppercase()
    if expr.op == "normalize_whitespace":
        return (
            compile_polars_expr(expr.value)
            .cast(pl.String)
            .str.replace_all(r"\s+", " ")
            .str.strip_chars()
        )
    if expr.op == "split":
        return (
            compile_polars_expr(expr.value)
            .cast(pl.String)
            .str.split(expr.delimiter)
            .list.get(expr.index, null_on_oob=True)
        )
    if expr.op == "substring":
        return compile_polars_expr(expr.value).cast(pl.String).str.slice(expr.start, expr.length)
    if expr.op == "regex_extract":
        return (
            compile_polars_expr(expr.value)
            .cast(pl.String)
            .str.extract(expr.pattern, group_index=expr.group)
        )
    if expr.op == "concat":
        return pl.concat_str(
            [compile_polars_expr(v) for v in expr.values],
            separator=expr.separator,
            ignore_nulls=False,
        )
    if expr.op == "map_values":
        return (
            compile_polars_expr(expr.value)
            .cast(pl.String)
            .replace_strict(expr.mapping, default=expr.default)
        )
    if expr.op == "lookup":
        return (
            compile_polars_expr(expr.value)
            .cast(pl.String)
            .replace_strict(expr.table, default=expr.default)
        )
    if expr.op == "coalesce":
        return pl.coalesce([compile_polars_expr(v) for v in expr.values])
    if expr.op in {"add", "subtract", "multiply", "divide"}:
        left = compile_polars_expr(expr.left).cast(pl.Float64, strict=False)
        right = compile_polars_expr(expr.right).cast(pl.Float64, strict=False)
        if expr.op == "add":
            return left + right
        if expr.op == "subtract":
            return left - right
        if expr.op == "multiply":
            return left * right
        return left / right
    if expr.op == "unit_convert":
        return (
            compile_polars_expr(expr.value).cast(pl.Float64, strict=False) * expr.factor
            + expr.offset
        )
    if expr.op == "conditional":
        left = compile_polars_expr(expr.condition.left)
        right = (
            compile_polars_expr(expr.condition.right) if expr.condition.right is not None else None
        )
        operator = expr.condition.operator
        if operator == "==":
            condition = left == right
        elif operator == "!=":
            condition = left != right
        elif operator == ">":
            condition = left > right
        elif operator == ">=":
            condition = left >= right
        elif operator == "<":
            condition = left < right
        elif operator == "<=":
            condition = left <= right
        elif operator == "is_null":
            condition = left.is_null()
        elif operator == "not_null":
            condition = left.is_not_null()
        elif operator == "in":
            condition = left.is_in(right)
        elif operator == "not_in":
            condition = ~left.is_in(right)
        else:
            raise CompileError(f"Unsupported predicate: {operator}")
        return (
            pl.when(condition)
            .then(compile_polars_expr(expr.if_true))
            .otherwise(compile_polars_expr(expr.if_false))
        )
    raise CompileError(f"Unsupported operation: {expr.op}")


def execute_polars(
    rows: list[dict[str, object]], program: TransformationProgram
) -> list[dict[str, object]]:
    import polars as pl

    if not rows:
        return []
    analysis_order, errors = dependency_order(program)
    if errors:
        raise CompileError("; ".join(errors))
    lf = pl.DataFrame(rows).lazy()
    for target in analysis_order:
        lf = lf.with_columns(compile_polars_expr(program.mappings[target]).alias(target))
    return lf.select(list(program.mappings)).collect().to_dicts()
