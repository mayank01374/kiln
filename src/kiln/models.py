from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceExpr(StrictModel):
    op: Literal["source"]
    column: str


class LiteralExpr(StrictModel):
    op: Literal["literal"]
    value: str | int | float | bool | None


class CastExpr(StrictModel):
    op: Literal["cast"]
    type: Literal["string", "integer", "float", "boolean"]
    value: Expression


class ParseDateExpr(StrictModel):
    op: Literal["parse_date"]
    value: Expression
    formats: list[str] = Field(min_length=1)


class TrimExpr(StrictModel):
    op: Literal["trim"]
    value: Expression


class LowerExpr(StrictModel):
    op: Literal["lowercase"]
    value: Expression


class UpperExpr(StrictModel):
    op: Literal["uppercase"]
    value: Expression


class NormalizeWhitespaceExpr(StrictModel):
    op: Literal["normalize_whitespace"]
    value: Expression


class SplitExpr(StrictModel):
    op: Literal["split"]
    value: Expression
    delimiter: str
    index: int


class ConcatExpr(StrictModel):
    op: Literal["concat"]
    values: list[Expression] = Field(min_length=1)
    separator: str = ""


class MapValuesExpr(StrictModel):
    op: Literal["map_values"]
    value: Expression
    mapping: dict[str, str | int | float | bool | None]
    default: str | int | float | bool | None = None


class CoalesceExpr(StrictModel):
    op: Literal["coalesce"]
    values: list[Expression] = Field(min_length=1)


class RegexExtractExpr(StrictModel):
    op: Literal["regex_extract"]
    value: Expression
    pattern: str
    group: int = 1


Expression = Annotated[
    SourceExpr
    | LiteralExpr
    | CastExpr
    | ParseDateExpr
    | TrimExpr
    | LowerExpr
    | UpperExpr
    | NormalizeWhitespaceExpr
    | SplitExpr
    | ConcatExpr
    | MapValuesExpr
    | CoalesceExpr
    | RegexExtractExpr,
    Field(discriminator="op"),
]


class FieldConstraint(StrictModel):
    kind: Literal["unique", "non_empty", "email"]


class TargetField(StrictModel):
    name: str
    type: Literal["string", "integer", "float", "boolean", "date", "enum"]
    required: bool = False
    description: str = ""
    values: list[str] | None = None
    constraints: list[FieldConstraint] = []


class Invariant(StrictModel):
    id: str
    type: Literal["required", "unique", "enum", "email", "comparison"]
    field: str | None = None
    left: str | None = None
    operator: Literal[">=", "<=", ">", "<", "=="] | None = None
    right: str | None = None


class TargetContract(StrictModel):
    name: str
    version: int = 1
    fields: list[TargetField]
    invariants: list[Invariant] = []

    @property
    def required_fields(self) -> set[str]:
        return {f.name for f in self.fields if f.required}


class TransformationProgram(StrictModel):
    version: int = 1
    mappings: dict[str, Expression]
    unresolved_fields: list[str] = []


class ReplaceExpressionPatch(StrictModel):
    type: Literal["replace_expression"]
    target_field: str
    expression: Expression


class AddMappingPatch(StrictModel):
    type: Literal["add_mapping"]
    target_field: str
    expression: Expression


ProgramPatchOperation = Annotated[
    ReplaceExpressionPatch | AddMappingPatch, Field(discriminator="type")
]


class ProgramPatch(StrictModel):
    patches: list[ProgramPatchOperation]


class ColumnProfile(StrictModel):
    name: str
    physical_type: str
    null_ratio: float
    uniqueness_ratio: float
    top_values: list[tuple[str, int]]
    sample_values: list[str]
    date_format_candidates: list[str]
    value_shape: str


class SourceProfile(StrictModel):
    row_count: int
    columns: list[ColumnProfile]


class Counterexample(StrictModel):
    invariant_id: str
    row_index: int | None = None
    observed: dict[str, object]
    message: str


class VerificationResult(StrictModel):
    passed: bool
    invariants_checked: int
    failures: list[Counterexample]
    rows_input: int
    rows_output: int


class RunResult(StrictModel):
    status: Literal["VERIFIED", "HUMAN_REVIEW", "FAILED"]
    source_family: str
    structural_fingerprint: str
    program: TransformationProgram | None = None
    verification: VerificationResult | None = None
    model_calls: int = 0
    repair_iterations: int = 0
    reused_program: bool = False


for model in [
    CastExpr,
    ParseDateExpr,
    TrimExpr,
    LowerExpr,
    UpperExpr,
    NormalizeWhitespaceExpr,
    SplitExpr,
    ConcatExpr,
    MapValuesExpr,
    CoalesceExpr,
    RegexExtractExpr,
    TransformationProgram,
    ReplaceExpressionPatch,
    AddMappingPatch,
]:
    model.model_rebuild()
