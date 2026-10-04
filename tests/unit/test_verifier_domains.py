from kiln.domains.base import valid_npi
from kiln.executor import execute
from kiln.models import ExecutionResult, TargetContract, TransformationProgram
from kiln.verifier import verify_execution


def _healthcare_contract() -> TargetContract:
    return TargetContract.model_validate(
        {
            "name": "provider",
            "domain": "healthcare",
            "fields": [
                {
                    "name": "id",
                    "type": "string",
                    "required": True,
                    "constraints": [{"kind": "unique"}],
                },
                {
                    "name": "email",
                    "type": "string",
                    "constraints": [{"kind": "email"}],
                },
                {
                    "name": "status",
                    "type": "enum",
                    "values": ["active", "inactive"],
                },
                {
                    "name": "age",
                    "type": "integer",
                    "constraints": [{"kind": "range", "min": 0, "max": 120}],
                },
                {
                    "name": "code",
                    "type": "string",
                    "constraints": [{"kind": "regex", "pattern": "^[A-Z]{3}-[0-9]{2}$"}],
                },
                {"name": "start_date", "type": "date"},
                {"name": "end_date", "type": "date"},
                {
                    "name": "npi",
                    "type": "string",
                    "constraints": [{"kind": "npi"}],
                },
            ],
            "invariants": [
                {
                    "id": "date_order",
                    "type": "comparison",
                    "left": "end_date",
                    "operator": ">=",
                    "right": "start_date",
                }
            ],
        }
    )


def _program() -> TransformationProgram:
    fields = [
        "id",
        "email",
        "status",
        "age",
        "code",
        "start_date",
        "end_date",
        "npi",
    ]
    return TransformationProgram.model_validate(
        {"mappings": {field: {"op": "source", "column": f"raw_{field}"} for field in fields}}
    )


def test_verification_returns_actionable_counterexamples_and_lineage():
    rows = [
        {
            "raw_id": "provider-1",
            "raw_email": "ada@example.com",
            "raw_status": "active",
            "raw_age": 36,
            "raw_code": "ABC-12",
            "raw_start_date": "2026-01-01",
            "raw_end_date": "2026-02-01",
            "raw_npi": "1234567893",
        },
        {
            "raw_id": "provider-1",
            "raw_email": "not-an-email",
            "raw_status": "unknown",
            "raw_age": 200,
            "raw_code": "broken",
            "raw_start_date": "2026-03-01",
            "raw_end_date": "2026-02-01",
            "raw_npi": "1234567890",
        },
    ]
    program = _program()

    result = verify_execution(
        rows,
        execute(rows, program),
        _healthcare_contract(),
        program,
    )

    assert not result.passed
    failure_ids = {failure.invariant_id for failure in result.failures}
    assert {
        "unique_id_0",
        "email_email_0",
        "enum_status",
        "range_age_0",
        "regex_code_0",
        "date_order",
        "npi_npi_0",
    } <= failure_ids

    email_failure = next(
        failure for failure in result.failures if failure.invariant_id == "email_email_0"
    )
    assert email_failure.row_index == 1
    assert email_failure.source_row == rows[1]
    assert email_failure.transformed["email"] == "not-an-email"
    assert email_failure.observed == {"email": "not-an-email"}
    assert email_failure.expected == "valid email"
    assert email_failure.dependency_slice == {"email": ["raw_email"]}


def test_npi_is_validated_by_the_healthcare_domain_plugin():
    assert valid_npi("1234567893")
    assert not valid_npi("1234567890")


def test_row_conservation_failure_contains_accounting_evidence():
    contract = TargetContract.model_validate({"name": "empty", "fields": []})
    program = TransformationProgram.model_validate({"mappings": {}})
    execution = ExecutionResult(rows_input=1, rows_output=0)

    result = verify_execution([{"source": "row"}], execution, contract, program)

    assert not result.passed
    failure = result.failures[0]
    assert failure.invariant_id == "row_conservation"
    assert failure.observed == {
        "input": 1,
        "accepted": 0,
        "quarantined": 0,
        "filtered": 0,
    }
    assert failure.expected == "input = accepted + quarantined + filtered"
