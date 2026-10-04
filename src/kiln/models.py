from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


ErrorPolicy = Literal["set_null", "quarantine_row", "fail_run"]
Scalar = str | int | float | bool | None


class SourceExpr(StrictModel):
    op: Literal["source"]
    column: str


class TargetExpr(StrictModel):
    op: Literal["target"]
    field: str


class LiteralExpr(StrictModel):
    op: Literal["literal"]
    value: Scalar


class CastExpr(StrictModel):
    op: Literal["cast"]
    type: Literal["string", "integer", "float", "boolean"]
    value: Expression
    on_error: ErrorPolicy = "set_null"


class ParseDateExpr(StrictModel):
    op: Literal["parse_date"]
    value: Expression
    formats: list[str] = Field(min_length=1)
    on_error: ErrorPolicy = "set_null"


class UnaryExpr(StrictModel):
    value: Expression


class TrimExpr(UnaryExpr):
    op: Literal["trim"]


class LowerExpr(UnaryExpr):
    op: Literal["lowercase"]


class UpperExpr(UnaryExpr):
    op: Literal["uppercase"]


class NormalizeWhitespaceExpr(UnaryExpr):
    op: Literal["normalize_whitespace"]


class SplitExpr(UnaryExpr):
    op: Literal["split"]
    delimiter: str
    index: int


class SubstringExpr(UnaryExpr):
    op: Literal["substring"]
    start: int
    length: int | None = None


class RegexExtractExpr(UnaryExpr):
    op: Literal["regex_extract"]
    pattern: str
    group: int = 1


class ConcatExpr(StrictModel):
    op: Literal["concat"]
    values: list[Expression] = Field(min_length=1)
    separator: str = ""


class MapValuesExpr(UnaryExpr):
    op: Literal["map_values"]
    mapping: dict[str, Scalar]
    default: Scalar = None


class CoalesceExpr(StrictModel):
    op: Literal["coalesce"]
    values: list[Expression] = Field(min_length=1)


class LookupExpr(UnaryExpr):
    op: Literal["lookup"]
    table: dict[str, Scalar]
    default: Scalar = None


class BinaryExpr(StrictModel):
    left: Expression
    right: Expression
    on_error: ErrorPolicy = "set_null"


class AddExpr(BinaryExpr):
    op: Literal["add"]


class SubtractExpr(BinaryExpr):
    op: Literal["subtract"]


class MultiplyExpr(BinaryExpr):
    op: Literal["multiply"]


class DivideExpr(BinaryExpr):
    op: Literal["divide"]


class Predicate(StrictModel):
    left: Expression
    operator: Literal["==", "!=", ">", ">=", "<", "<=", "in", "not_in", "is_null", "not_null"]
    right: Expression | None = None


class ConditionalExpr(StrictModel):
    op: Literal["conditional"]
    condition: Predicate
    if_true: Expression
    if_false: Expression


class UnitConvertExpr(UnaryExpr):
    op: Literal["unit_convert"]
    factor: float
    offset: float = 0.0
    on_error: ErrorPolicy = "set_null"


Expression = Annotated[
    SourceExpr
    | TargetExpr
    | LiteralExpr
    | CastExpr
    | ParseDateExpr
    | TrimExpr
    | LowerExpr
    | UpperExpr
    | NormalizeWhitespaceExpr
    | SplitExpr
    | SubstringExpr
    | RegexExtractExpr
    | ConcatExpr
    | MapValuesExpr
    | CoalesceExpr
    | LookupExpr
    | AddExpr
    | SubtractExpr
    | MultiplyExpr
    | DivideExpr
    | ConditionalExpr
    | UnitConvertExpr,
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

    @property
    def field_map(self) -> dict[str, TargetField]:
        return {field.name: field for field in self.fields}


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


class RowOutcome(StrictModel):
    row_index: int
    status: Literal["ACCEPTED", "QUARANTINED", "FILTERED"]
    reason_code: str | None = None


class ExecutionResult(StrictModel):
    rows: list[dict[str, object]] = Field(default_factory=list)
    rejected_rows: list[dict[str, object]] = Field(default_factory=list)
    outcomes: list[RowOutcome] = Field(default_factory=list)
    rows_input: int
    rows_output: int
    rows_quarantined: int = 0
    rows_filtered: int = 0


class CompilerDiagnostic(StrictModel):
    code: str
    target_field: str | None = None
    message: str


class StaticAnalysis(StrictModel):
    ok: bool
    diagnostics: list[CompilerDiagnostic] = Field(default_factory=list)
    order: list[str] = Field(default_factory=list)
    lineage: dict[str, list[str]] = Field(default_factory=dict)
    inferred_types: dict[str, str] = Field(default_factory=dict)

    @property
    def errors(self) -> list[str]:
        """Compatibility view for existing CLI error reporting."""
        return [diagnostic.message for diagnostic in self.diagnostics]


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
    UnaryExpr,
    BinaryExpr,
    CastExpr,
    ParseDateExpr,
    TrimExpr,
    LowerExpr,
    UpperExpr,
    NormalizeWhitespaceExpr,
    SplitExpr,
    SubstringExpr,
    RegexExtractExpr,
    ConcatExpr,
    MapValuesExpr,
    CoalesceExpr,
    LookupExpr,
    AddExpr,
    SubtractExpr,
    MultiplyExpr,
    DivideExpr,
    Predicate,
    ConditionalExpr,
    UnitConvertExpr,
    TransformationProgram,
    ReplaceExpressionPatch,
    AddMappingPatch,
]:
    model.model_rebuild()
