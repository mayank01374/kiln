from __future__ import annotations

from copy import deepcopy
from typing import Any

from .models import Expression, ProgramPatch, TransformationProgram


def _rewrite_source(expr: Expression, old: str, new: str) -> dict[str, Any]:
    """Return a serialized expression with matching source nodes rewritten."""

    def rewrite(value: Any) -> Any:
        if isinstance(value, dict):
            rewritten = {key: rewrite(item) for key, item in value.items()}
            if rewritten.get("op") == "source" and rewritten.get("column") == old:
                rewritten["column"] = new
            return rewritten
        if isinstance(value, list):
            return [rewrite(item) for item in value]
        return value

    return rewrite(expr.model_dump(mode="python"))


def apply_patch(program: TransformationProgram, patch: ProgramPatch) -> TransformationProgram:
    """Apply an ordered patch without mutating the input program."""
    data = deepcopy(program.model_dump(mode="python"))
    mappings = data["mappings"]
    for op in patch.patches:
        if op.type in {"replace_expression", "add_mapping"}:
            mappings[op.target_field] = op.expression.model_dump(mode="python")
        elif op.type == "remove_mapping":
            mappings.pop(op.target_field, None)
        elif op.type == "change_date_formats":
            expr = mappings.get(op.target_field)
            if not expr or expr.get("op") != "parse_date":
                raise ValueError(f"{op.target_field} is not a parse_date expression")
            expr["formats"] = list(op.formats)
        elif op.type == "extend_enum_map":
            expr = mappings.get(op.target_field)
            if not expr or expr.get("op") != "map_values":
                raise ValueError(f"{op.target_field} is not a map_values expression")
            expr["mapping"].update(op.mapping)
        elif op.type == "change_source_reference":
            if op.target_field not in mappings:
                raise ValueError(f"{op.target_field} is not a mapped field")
            expr = TransformationProgram(mappings={"x": mappings[op.target_field]}).mappings["x"]
            mappings[op.target_field] = _rewrite_source(expr, op.old_column, op.new_column)
        elif op.type == "add_fallback":
            if op.target_field not in mappings:
                raise ValueError(f"{op.target_field} is not a mapped field")
            current = mappings[op.target_field]
            mappings[op.target_field] = {
                "op": "coalesce",
                "values": [current, op.fallback.model_dump(mode="python")],
            }
        else:  # pragma: no cover
            raise ValueError(f"Unsupported patch {op.type}")
    data["version"] = int(data.get("version", 1)) + 1
    data["unresolved_fields"] = [
        field for field in data.get("unresolved_fields", []) if field not in mappings
    ]
    return TransformationProgram.model_validate(data)


def changed_targets(patch: ProgramPatch) -> list[str]:
    """Return the sorted, de-duplicated fields affected by a patch."""
    return sorted({operation.target_field for operation in patch.patches})


def diff_programs(a: TransformationProgram, b: TransformationProgram) -> dict[str, Any]:
    """Produce an auditable, JSON-compatible mapping-level program diff."""
    keys = sorted(set(a.mappings) | set(b.mappings))
    changed: dict[str, Any] = {}
    for key in keys:
        before = a.mappings.get(key)
        after = b.mappings.get(key)
        before_data = before.model_dump(mode="json") if before else None
        after_data = after.model_dump(mode="json") if after else None
        if before_data != after_data:
            changed[key] = {"before": before_data, "after": after_data}
    return {"changed_fields": sorted(changed), "changes": changed}
