from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from .compiler import static_check
from .domains import get_domain_plugin
from .executor import execute
from .models import (
    Counterexample,
    ExecutionResult,
    Invariant,
    RowOutcome,
    SourceProfile,
    TargetContract,
    TransformationProgram,
    VerificationResult,
)

EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _accepted_source_indexes(execution: ExecutionResult) -> list[int]:
    return [outcome.row_index for outcome in execution.outcomes if outcome.status == "ACCEPTED"]


def _counterexample(
    invariant: Invariant,
    output_index: int | None,
    rows_in: list[dict[str, Any]],
    execution: ExecutionResult,
    lineage: dict[str, list[str]],
    observed: dict[str, Any],
    expected: str,
    message: str,
    target_field: str | None = None,
) -> Counterexample:
    source_index: int | None = None
    source: dict[str, Any] = {}
    transformed: dict[str, Any] = {}
    if output_index is not None and output_index < len(execution.rows):
        accepted = _accepted_source_indexes(execution)
        if output_index < len(accepted):
            source_index = accepted[output_index]
            source = rows_in[source_index]
        transformed = execution.rows[output_index]

    fields = [
        field
        for field in [target_field, invariant.field, invariant.left, invariant.right]
        if field and field in lineage
    ]
    dependency_slice = {field: lineage[field] for field in fields}
    return Counterexample(
        invariant_id=invariant.id,
        target_field=target_field or invariant.field,
        row_index=source_index,
        source_row=source,
        transformed=transformed,
        observed=observed,
        expected=expected,
        dependency_slice=dependency_slice,
        message=message,
    )


def expanded_invariants(contract: TargetContract) -> list[Invariant]:
    invariants = list(contract.invariants)
    existing = {(invariant.type, invariant.field) for invariant in invariants}
    for field in contract.fields:
        if field.required and ("required", field.name) not in existing:
            invariants.append(
                Invariant(id=f"required_{field.name}", type="required", field=field.name)
            )
        if field.type == "enum" and field.values and ("enum", field.name) not in existing:
            invariants.append(Invariant(id=f"enum_{field.name}", type="enum", field=field.name))
        for index, constraint in enumerate(field.constraints):
            if (
                constraint.kind in {"unique", "email", "regex", "range"}
                and (
                    constraint.kind,
                    field.name,
                )
                not in existing
            ):
                invariants.append(
                    Invariant(
                        id=f"{constraint.kind}_{field.name}_{index}",
                        type=constraint.kind,
                        field=field.name,
                        pattern=constraint.pattern,
                        min=constraint.min,
                        max=constraint.max,
                    )
                )
            elif constraint.kind == "npi":
                invariants.append(
                    Invariant(
                        id=f"npi_{field.name}_{index}",
                        type="domain",
                        field=field.name,
                        validator="npi",
                    )
                )
    return invariants


def verify_execution(
    rows_in: list[dict[str, Any]],
    execution: ExecutionResult,
    contract: TargetContract,
    program: TransformationProgram,
    warnings: list[str] | None = None,
) -> VerificationResult:
    failures: list[Counterexample] = []
    source_columns = set().union(*(row.keys() for row in rows_in)) if rows_in else set()
    analysis = static_check(program, contract, source_columns)
    lineage = analysis.lineage

    checked = 1
    accounted = execution.rows_output + execution.rows_quarantined + execution.rows_filtered
    if execution.rows_input != accounted:
        invariant = Invariant(
            id="row_conservation",
            type="required",
            field="__row_conservation__",
        )
        failures.append(
            _counterexample(
                invariant,
                None,
                rows_in,
                execution,
                lineage,
                {
                    "input": execution.rows_input,
                    "accepted": execution.rows_output,
                    "quarantined": execution.rows_quarantined,
                    "filtered": execution.rows_filtered,
                },
                "input = accepted + quarantined + filtered",
                "row conservation violated",
            )
        )

    invariants = expanded_invariants(contract)
    field_definitions = contract.field_map
    plugin = get_domain_plugin(contract.domain)
    for invariant in invariants:
        checked += 1
        if invariant.type == "required" and invariant.field:
            for index, row in enumerate(execution.rows):
                if row.get(invariant.field) in (None, ""):
                    failures.append(
                        _counterexample(
                            invariant,
                            index,
                            rows_in,
                            execution,
                            lineage,
                            {invariant.field: row.get(invariant.field)},
                            "non-null value",
                            f"{invariant.field} is required",
                        )
                    )
                    break
        elif invariant.type == "unique" and invariant.field:
            seen: set[Any] = set()
            for index, row in enumerate(execution.rows):
                value = row.get(invariant.field)
                if value in seen and value not in (None, ""):
                    failures.append(
                        _counterexample(
                            invariant,
                            index,
                            rows_in,
                            execution,
                            lineage,
                            {invariant.field: value},
                            "unique values",
                            f"{invariant.field} must be unique",
                        )
                    )
                    break
                seen.add(value)
        elif invariant.type == "enum" and invariant.field:
            field = field_definitions.get(invariant.field)
            allowed = set(field.values or []) if field else set()
            for index, row in enumerate(execution.rows):
                value = row.get(invariant.field)
                if value is not None and value not in allowed:
                    failures.append(
                        _counterexample(
                            invariant,
                            index,
                            rows_in,
                            execution,
                            lineage,
                            {invariant.field: value},
                            f"one of {sorted(allowed)}",
                            f"{value!r} not in enum",
                        )
                    )
                    break
        elif invariant.type == "email" and invariant.field:
            for index, row in enumerate(execution.rows):
                value = row.get(invariant.field)
                if value and not EMAIL.match(str(value)):
                    failures.append(
                        _counterexample(
                            invariant,
                            index,
                            rows_in,
                            execution,
                            lineage,
                            {invariant.field: value},
                            "valid email",
                            "invalid email",
                        )
                    )
                    break
        elif invariant.type == "regex" and invariant.field and invariant.pattern:
            pattern = re.compile(invariant.pattern)
            for index, row in enumerate(execution.rows):
                value = row.get(invariant.field)
                if value not in (None, "") and not pattern.search(str(value)):
                    failures.append(
                        _counterexample(
                            invariant,
                            index,
                            rows_in,
                            execution,
                            lineage,
                            {invariant.field: value},
                            invariant.pattern,
                            "regex invariant failed",
                        )
                    )
                    break
        elif invariant.type == "range" and invariant.field:
            for index, row in enumerate(execution.rows):
                value = row.get(invariant.field)
                if value in (None, ""):
                    continue
                try:
                    number = float(value)
                except (TypeError, ValueError):
                    number = float("nan")
                valid = (invariant.min is None or number >= invariant.min) and (
                    invariant.max is None or number <= invariant.max
                )
                if not valid:
                    failures.append(
                        _counterexample(
                            invariant,
                            index,
                            rows_in,
                            execution,
                            lineage,
                            {invariant.field: value},
                            f"range [{invariant.min}, {invariant.max}]",
                            "range invariant failed",
                        )
                    )
                    break
        elif (
            invariant.type == "comparison"
            and invariant.left
            and invariant.right
            and invariant.operator
        ):
            operators = {
                ">=": lambda left, right: left >= right,
                "<=": lambda left, right: left <= right,
                ">": lambda left, right: left > right,
                "<": lambda left, right: left < right,
                "==": lambda left, right: left == right,
                "!=": lambda left, right: left != right,
            }
            for index, row in enumerate(execution.rows):
                left = row.get(invariant.left)
                right: Any = (
                    datetime.now(UTC).date().isoformat()
                    if invariant.right == "$processing_date"
                    else row.get(invariant.right, invariant.right)
                )
                if left is None or right is None:
                    continue
                try:
                    valid = operators[invariant.operator](left, right)
                except TypeError:
                    valid = False
                if not valid:
                    failures.append(
                        _counterexample(
                            invariant,
                            index,
                            rows_in,
                            execution,
                            lineage,
                            {invariant.left: left, invariant.right: right},
                            f"{invariant.left} {invariant.operator} {invariant.right}",
                            "comparison invariant failed",
                            invariant.left,
                        )
                    )
                    break
        elif invariant.type == "domain" and invariant.field and invariant.validator:
            for index, row in enumerate(execution.rows):
                value = row.get(invariant.field)
                valid, message = plugin.validate(invariant.validator, value, row)
                if not valid:
                    failures.append(
                        _counterexample(
                            invariant,
                            index,
                            rows_in,
                            execution,
                            lineage,
                            {invariant.field: value},
                            invariant.validator,
                            message or "domain invariant failed",
                        )
                    )
                    break

    return VerificationResult(
        passed=not failures,
        invariants_checked=checked,
        failures=failures,
        warnings=warnings or [],
        rows_input=execution.rows_input,
        rows_output=execution.rows_output,
        rows_quarantined=execution.rows_quarantined,
        rows_filtered=execution.rows_filtered,
    )


def verify(
    rows_in: list[dict[str, Any]],
    rows_out: list[dict[str, Any]],
    contract: TargetContract,
) -> VerificationResult:
    """Backward-compatible verifier for callers that only have transformed rows."""
    execution = ExecutionResult(
        rows=rows_out,
        rows_input=len(rows_in),
        rows_output=len(rows_out),
        outcomes=[RowOutcome(row_index=index, status="ACCEPTED") for index in range(len(rows_out))],
    )
    output_fields = rows_out[0] if rows_out else {}
    mappings = {
        field.name: {"op": "source", "column": field.name}
        for field in contract.fields
        if field.name in output_fields
    }
    program = TransformationProgram.model_validate({"mappings": mappings})
    return verify_execution(rows_in, execution, contract, program)


def statistical_warnings(previous: SourceProfile | None, current: SourceProfile) -> list[str]:
    if previous is None:
        return []
    old = {column.normalized_name: column for column in previous.columns}
    new = {column.normalized_name: column for column in current.columns}
    warnings: list[str] = []
    for name in sorted(set(old) & set(new)):
        before, after = old[name], new[name]
        if abs(before.null_ratio - after.null_ratio) >= 0.25:
            warnings.append(
                f"{name}: null ratio changed {before.null_ratio:.2f} -> {after.null_ratio:.2f}"
            )
        if before.numeric_scale not in (None, 0) and after.numeric_scale not in (None, 0):
            ratio = max(
                abs(before.numeric_scale / after.numeric_scale),
                abs(after.numeric_scale / before.numeric_scale),
            )
            if ratio >= 5:
                warnings.append(f"{name}: numeric scale shifted by ~{ratio:.1f}x")
        if set(before.date_format_candidates) != set(after.date_format_candidates) and (
            before.date_format_candidates or after.date_format_candidates
        ):
            warnings.append(
                f"{name}: date formats changed "
                f"{before.date_format_candidates} -> {after.date_format_candidates}"
            )
    return warnings


def differential_verify(
    historical_rows: list[dict[str, Any]],
    old_program: TransformationProgram,
    new_program: TransformationProgram,
    allowed_changed_targets: set[str],
) -> tuple[bool, list[str]]:
    if not historical_rows:
        return True, []
    old = execute(historical_rows, old_program).rows
    new = execute(historical_rows, new_program).rows
    unexpected: set[str] = set()
    for before, after in zip(old, new):
        for field in set(before) | set(after):
            if before.get(field) != after.get(field) and field not in allowed_changed_targets:
                unexpected.add(field)
    return not unexpected, sorted(unexpected)
