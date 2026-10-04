from kiln.executor import execute_python
from kiln.models import TransformationProgram


def test_transform_program_executes_deterministically():
    program = TransformationProgram.model_validate(
        {
            "mappings": {
                "name": {
                    "op": "uppercase",
                    "value": {"op": "trim", "value": {"op": "source", "column": "n"}},
                },
                "status": {
                    "op": "map_values",
                    "value": {"op": "source", "column": "s"},
                    "mapping": {"1": "active", "0": "inactive"},
                },
            }
        }
    )
    out = execute_python([{"n": " ada ", "s": "1"}], program)
    assert out == [{"name": "ADA", "status": "active"}]
