import pytest
from pydantic import ValidationError

from kiln.models import TransformationProgram


def test_arbitrary_python_operator_is_rejected():
    with pytest.raises(ValidationError):
        TransformationProgram.model_validate(
            {"mappings": {"x": {"op": "python", "code": "import os"}}}
        )


def test_extra_fields_are_rejected():
    with pytest.raises(ValidationError):
        TransformationProgram.model_validate(
            {
                "mappings": {
                    "x": {
                        "op": "source",
                        "column": "x",
                        "shell": "rm -rf /",
                    }
                }
            }
        )
