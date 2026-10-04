from __future__ import annotations

import re

from .models import Counterexample, Invariant, TargetContract, VerificationResult

EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def verify(
    rows_in: list[dict[str, object]], rows_out: list[dict[str, object]], contract: TargetContract
) -> VerificationResult:
    failures: list[Counterexample] = []
    checked = 1
    if len(rows_in) != len(rows_out):
        failures.append(
            Counterexample(
                invariant_id="row_conservation",
                observed={"input": len(rows_in), "output": len(rows_out)},
                message="Rows were lost or created during transformation",
            )
        )

    invariants = list(contract.invariants)
    for field in contract.fields:
        if field.required and not any(
            i.type == "required" and i.field == field.name for i in invariants
        ):
            invariants.append(
                Invariant(id=f"required_{field.name}", type="required", field=field.name)
            )
        if (
            field.type == "enum"
            and field.values
            and not any(i.type == "enum" and i.field == field.name for i in invariants)
        ):
            invariants.append(Invariant(id=f"enum_{field.name}", type="enum", field=field.name))

    field_defs = {f.name: f for f in contract.fields}
    for inv in invariants:
        checked += 1
        if inv.type == "required" and inv.field:
            for idx, row in enumerate(rows_out):
                if row.get(inv.field) in (None, ""):
                    failures.append(
                        Counterexample(
                            invariant_id=inv.id,
                            row_index=idx,
                            observed={inv.field: row.get(inv.field)},
                            message=f"{inv.field} is required",
                        )
                    )
                    break
        elif inv.type == "unique" and inv.field:
            seen = set()
            for idx, row in enumerate(rows_out):
                value = row.get(inv.field)
                if value in seen:
                    failures.append(
                        Counterexample(
                            invariant_id=inv.id,
                            row_index=idx,
                            observed={inv.field: value},
                            message=f"{inv.field} must be unique",
                        )
                    )
                    break
                seen.add(value)
        elif inv.type == "enum" and inv.field:
            allowed = set(field_defs[inv.field].values or [])
            for idx, row in enumerate(rows_out):
                value = row.get(inv.field)
                if value is not None and value not in allowed:
                    failures.append(
                        Counterexample(
                            invariant_id=inv.id,
                            row_index=idx,
                            observed={inv.field: value},
                            message=f"{value!r} not in enum",
                        )
                    )
                    break
        elif inv.type == "email" and inv.field:
            for idx, row in enumerate(rows_out):
                value = row.get(inv.field)
                if value and not EMAIL.match(str(value)):
                    failures.append(
                        Counterexample(
                            invariant_id=inv.id,
                            row_index=idx,
                            observed={inv.field: value},
                            message="invalid email",
                        )
                    )
                    break
        elif inv.type == "comparison" and inv.left and inv.right and inv.operator:
            for idx, row in enumerate(rows_out):
                left, right = row.get(inv.left), row.get(inv.right)
                if left is None or right is None:
                    continue
                ok = {
                    ">=": left >= right,
                    "<=": left <= right,
                    ">": left > right,
                    "<": left < right,
                    "==": left == right,
                }[inv.operator]
                if not ok:
                    failures.append(
                        Counterexample(
                            invariant_id=inv.id,
                            row_index=idx,
                            observed={inv.left: left, inv.right: right},
                            message=f"expected {inv.left} {inv.operator} {inv.right}",
                        )
                    )
                    break

    return VerificationResult(
        passed=not failures,
        invariants_checked=checked,
        failures=failures,
        rows_input=len(rows_in),
        rows_output=len(rows_out),
    )
