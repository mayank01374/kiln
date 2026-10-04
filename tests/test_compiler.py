from kiln.compiler import referenced_columns, static_check
from kiln.models import TargetContract, TransformationProgram


def contract():
    return TargetContract.model_validate(
        {
            "name": "t",
            "fields": [{"name": "id", "type": "string", "required": True}],
            "invariants": [],
        }
    )


def test_static_check_rejects_missing_source():
    p = TransformationProgram.model_validate(
        {"mappings": {"id": {"op": "source", "column": "missing"}}}
    )
    result = static_check(p, contract(), {"actual"})
    assert not result.ok


def test_lineage_columns_are_extractable():
    p = TransformationProgram.model_validate(
        {
            "mappings": {
                "id": {
                    "op": "concat",
                    "values": [{"op": "source", "column": "a"}, {"op": "source", "column": "b"}],
                    "separator": "-",
                }
            }
        }
    )
    assert referenced_columns(p.mappings["id"]) == {"a", "b"}
