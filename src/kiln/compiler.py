from __future__ import annotations

from dataclasses import dataclass

from .models import Expression, TargetContract, TransformationProgram


class CompileError(ValueError):
    pass


def referenced_columns(expr: Expression) -> set[str]:
    if expr.op == "source":
        return {expr.column}
    if expr.op in {"literal"}:
        return set()
    if expr.op in {
        "cast",
        "parse_date",
        "trim",
        "lowercase",
        "uppercase",
        "normalize_whitespace",
        "split",
        "map_values",
        "regex_extract",
    }:
        return referenced_columns(expr.value)
    if expr.op in {"concat", "coalesce"}:
        out: set[str] = set()
        for child in expr.values:
            out |= referenced_columns(child)
        return out
    raise CompileError(f"Unsupported operation: {expr.op}")


@dataclass(frozen=True)
class StaticCheck:
    ok: bool
    errors: list[str]


def static_check(
    program: TransformationProgram, contract: TargetContract, source_columns: set[str]
) -> StaticCheck:
    errors: list[str] = []
    missing_targets = contract.required_fields - set(program.mappings)
    if missing_targets:
        errors.append(f"Missing required target mappings: {sorted(missing_targets)}")

    target_names = {f.name for f in contract.fields}
    unknown_targets = set(program.mappings) - target_names
    if unknown_targets:
        errors.append(f"Mappings contain fields absent from contract: {sorted(unknown_targets)}")

    for target, expr in program.mappings.items():
        missing_sources = referenced_columns(expr) - source_columns
        if missing_sources:
            errors.append(f"{target}: missing source columns {sorted(missing_sources)}")

    return StaticCheck(ok=not errors, errors=errors)


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
    if expr.op == "coalesce":
        return pl.coalesce([compile_polars_expr(v) for v in expr.values])
    if expr.op == "regex_extract":
        return (
            compile_polars_expr(expr.value)
            .cast(pl.String)
            .str.extract(expr.pattern, group_index=expr.group)
        )
    raise CompileError(f"Unsupported operation: {expr.op}")


def execute_polars(
    rows: list[dict[str, object]], program: TransformationProgram
) -> list[dict[str, object]]:
    import polars as pl

    if not rows:
        return []
    lf = pl.DataFrame(rows).lazy()
    expressions = [
        compile_polars_expr(expr).alias(target) for target, expr in program.mappings.items()
    ]
    return lf.select(expressions).collect().to_dicts()
