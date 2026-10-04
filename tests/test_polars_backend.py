import pytest

from kiln.compiler import execute_polars
from kiln.models import TransformationProgram

pytest.importorskip("polars")


def test_polars_backend_compiles_dsl():
    program = TransformationProgram.model_validate(
        {
            "mappings": {
                "name": {"op": "uppercase", "value": {"op": "source", "column": "name"}},
                "joined": {
                    "op": "concat",
                    "values": [{"op": "source", "column": "a"}, {"op": "source", "column": "b"}],
                    "separator": "-",
                },
            }
        }
    )
    assert execute_polars([{"name": "ada", "a": "x", "b": "y"}], program) == [
        {"name": "ADA", "joined": "x-y"}
    ]
