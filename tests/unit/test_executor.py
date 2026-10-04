import pytest

from kiln.executor import FailRun, execute
from kiln.models import TransformationProgram


def test_executor_supports_target_refs_arithmetic_conditionals_and_units():
    program = TransformationProgram.model_validate(
        {
            "mappings": {
                "first": {
                    "op": "trim",
                    "value": {"op": "source", "column": "first"},
                },
                "last": {
                    "op": "uppercase",
                    "value": {"op": "source", "column": "last"},
                },
                "full": {
                    "op": "concat",
                    "values": [
                        {"op": "target", "field": "first"},
                        {"op": "target", "field": "last"},
                    ],
                    "separator": " ",
                },
                "kg": {
                    "op": "unit_convert",
                    "value": {"op": "source", "column": "lb"},
                    "factor": 0.45359237,
                },
                "initials": {
                    "op": "substring",
                    "value": {"op": "target", "field": "last"},
                    "start": 0,
                    "length": 3,
                },
                "country": {
                    "op": "lookup",
                    "value": {"op": "source", "column": "country_code"},
                    "table": {"GB": "United Kingdom"},
                },
                "sum": {
                    "op": "add",
                    "left": {"op": "source", "column": "age"},
                    "right": {"op": "literal", "value": 4},
                },
                "difference": {
                    "op": "subtract",
                    "left": {"op": "source", "column": "age"},
                    "right": {"op": "literal", "value": 6},
                },
                "product": {
                    "op": "multiply",
                    "left": {"op": "source", "column": "age"},
                    "right": {"op": "literal", "value": 2},
                },
                "quotient": {
                    "op": "divide",
                    "left": {"op": "source", "column": "age"},
                    "right": {"op": "literal", "value": 3},
                },
                "bucket": {
                    "op": "conditional",
                    "condition": {
                        "left": {"op": "source", "column": "age"},
                        "operator": ">=",
                        "right": {"op": "literal", "value": 18},
                    },
                    "if_true": {"op": "literal", "value": "adult"},
                    "if_false": {"op": "literal", "value": "minor"},
                },
            }
        }
    )

    result = execute(
        [
            {
                "first": " Ada ",
                "last": "lovelace",
                "lb": "100",
                "age": 36,
                "country_code": "GB",
            }
        ],
        program,
    )

    assert result.rows[0]["full"] == "Ada LOVELACE"
    assert round(result.rows[0]["kg"], 4) == 45.3592
    assert result.rows[0]["initials"] == "LOV"
    assert result.rows[0]["country"] == "United Kingdom"
    assert result.rows[0]["sum"] == 40
    assert result.rows[0]["difference"] == 30
    assert result.rows[0]["product"] == 72
    assert result.rows[0]["quotient"] == 12
    assert result.rows[0]["bucket"] == "adult"


def test_quarantine_row_error_policy_is_accounted():
    program = TransformationProgram.model_validate(
        {
            "mappings": {
                "date": {
                    "op": "parse_date",
                    "value": {"op": "source", "column": "d"},
                    "formats": ["%Y-%m-%d"],
                    "on_error": "quarantine_row",
                }
            }
        }
    )

    result = execute([{"d": "2026-01-01"}, {"d": "bad"}], program)

    assert result.rows_output == 1
    assert result.rows_quarantined == 1
    assert result.rows_input == result.rows_output + result.rows_quarantined + result.rows_filtered
    assert result.rejected_rows == [
        {
            "_row_index": 1,
            "_reason": "no date format matched 'bad'",
            "d": "bad",
        }
    ]
    assert [outcome.status for outcome in result.outcomes] == ["ACCEPTED", "QUARANTINED"]


def test_set_null_policy_preserves_the_row_and_records_acceptance():
    program = TransformationProgram.model_validate(
        {
            "mappings": {
                "date": {
                    "op": "parse_date",
                    "value": {"op": "source", "column": "d"},
                    "formats": ["%Y-%m-%d"],
                    "on_error": "set_null",
                }
            }
        }
    )

    result = execute([{"d": "bad"}], program)

    assert result.rows == [{"date": None}]
    assert result.rows_output == 1
    assert result.rows_quarantined == 0
    assert result.outcomes[0].status == "ACCEPTED"


def test_fail_run_policy_stops_execution_with_the_original_error():
    program = TransformationProgram.model_validate(
        {
            "mappings": {
                "ratio": {
                    "op": "divide",
                    "left": {"op": "source", "column": "value"},
                    "right": {"op": "literal", "value": 0},
                    "on_error": "fail_run",
                }
            }
        }
    )

    with pytest.raises(FailRun, match="division by zero"):
        execute([{"value": 10}], program)


def test_execution_is_deterministic_across_repeated_runs():
    program = TransformationProgram.model_validate(
        {
            "mappings": {
                "value": {
                    "op": "cast",
                    "type": "integer",
                    "value": {"op": "source", "column": "raw"},
                    "on_error": "quarantine_row",
                }
            }
        }
    )
    rows = [{"raw": "10"}, {"raw": "bad"}, {"raw": "20"}]

    first = execute(rows, program)
    second = execute(rows, program)

    assert first == second
