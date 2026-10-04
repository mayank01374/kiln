from kiln.compiler import dependency_order, execute_polars, static_check
from kiln.models import TargetContract, TransformationProgram


def _contract() -> TargetContract:
    return TargetContract.model_validate(
        {
            "name": "customer",
            "fields": [
                {"name": "first", "type": "string", "required": True},
                {"name": "last", "type": "string", "required": True},
                {"name": "full", "type": "string", "required": True},
                {"name": "score", "type": "float"},
            ],
        }
    )


def test_static_analysis_orders_dependencies_and_resolves_lineage():
    program = TransformationProgram.model_validate(
        {
            "mappings": {
                "full": {
                    "op": "concat",
                    "values": [
                        {"op": "target", "field": "first"},
                        {"op": "target", "field": "last"},
                    ],
                    "separator": " ",
                },
                "last": {"op": "source", "column": "family_name"},
                "first": {"op": "source", "column": "given_name"},
            }
        }
    )

    analysis = static_check(
        program,
        _contract(),
        {"given_name", "family_name"},
    )

    assert analysis.ok
    assert analysis.order.index("first") < analysis.order.index("full")
    assert analysis.order.index("last") < analysis.order.index("full")
    assert analysis.lineage == {
        "first": ["given_name"],
        "last": ["family_name"],
        "full": ["family_name", "given_name"],
    }
    assert analysis.inferred_types["full"] == "string"


def test_dependency_analysis_rejects_unknown_targets_and_cycles():
    unknown = TransformationProgram.model_validate(
        {"mappings": {"full": {"op": "target", "field": "missing"}}}
    )
    _, unknown_errors = dependency_order(unknown)
    assert "unknown target fields ['missing']" in unknown_errors[0]

    cyclic = TransformationProgram.model_validate(
        {
            "mappings": {
                "first": {"op": "target", "field": "last"},
                "last": {"op": "target", "field": "first"},
            }
        }
    )
    order, cycle_errors = dependency_order(cyclic)
    assert order == []
    assert "cyclic target dependencies" in cycle_errors[0]


def test_static_analysis_reports_coverage_references_and_type_errors():
    program = TransformationProgram.model_validate(
        {
            "mappings": {
                "first": {"op": "source", "column": "unknown_column"},
                "last": {"op": "literal", "value": "Lovelace"},
                "score": {"op": "literal", "value": "not numeric"},
            }
        }
    )

    analysis = static_check(program, _contract(), {"given_name"})
    codes = {diagnostic.code for diagnostic in analysis.diagnostics}

    assert not analysis.ok
    assert {"E001", "E101", "E102"} <= codes


def test_polars_execution_uses_dependency_order_for_target_references():
    program = TransformationProgram.model_validate(
        {
            "mappings": {
                "full": {
                    "op": "concat",
                    "values": [
                        {"op": "target", "field": "first"},
                        {"op": "literal", "value": "Lovelace"},
                    ],
                    "separator": " ",
                },
                "first": {"op": "source", "column": "given_name"},
            }
        }
    )

    assert execute_polars([{"given_name": "Ada"}], program) == [
        {"full": "Ada Lovelace", "first": "Ada"}
    ]
