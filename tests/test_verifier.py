from kiln.models import TargetContract
from kiln.verifier import verify


def test_row_conservation_and_required_field():
    c = TargetContract.model_validate(
        {
            "name": "t",
            "fields": [{"name": "id", "type": "string", "required": True}],
            "invariants": [],
        }
    )
    result = verify([{"x": 1}], [{"id": None}], c)
    assert not result.passed
    assert any(f.invariant_id == "required_id" for f in result.failures)
