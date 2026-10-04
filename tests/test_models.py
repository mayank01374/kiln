import pytest
from pydantic import ValidationError

from kiln.models import TransformationProgram


def test_unknown_operator_is_rejected():
    with pytest.raises(ValidationError):
        TransformationProgram.model_validate(
            {"mappings": {"x": {"op": "python", "code": "rm -rf /"}}}
        )
