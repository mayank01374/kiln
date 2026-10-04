from __future__ import annotations

from copy import deepcopy

from .models import ProgramPatch, TransformationProgram


def apply_patch(program: TransformationProgram, patch: ProgramPatch) -> TransformationProgram:
    candidate = deepcopy(program)
    for op in patch.patches:
        if op.type in {"replace_expression", "add_mapping"}:
            candidate.mappings[op.target_field] = op.expression
    return candidate
