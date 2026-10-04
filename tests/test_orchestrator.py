from pathlib import Path

from kiln.models import TargetContract
from kiln.orchestrator import Kiln
from kiln.registry import ProgramRegistry
from kiln.synthesis import HeuristicSynthesizer


def test_known_schema_reuses_program_without_synthesis(tmp_path: Path):
    rows = [
        {
            "cust_id": "1",
            "fname": "Ada",
            "lname": "Lovelace",
            "created": "09/18/2026",
            "status": "active",
        }
    ]
    c = TargetContract.model_validate(
        {
            "name": "customer",
            "fields": [
                {"name": "customer_id", "type": "string", "required": True},
                {"name": "first_name", "type": "string", "required": True},
                {"name": "last_name", "type": "string", "required": True},
                {"name": "signup_date", "type": "date", "required": True},
                {
                    "name": "status",
                    "type": "enum",
                    "required": True,
                    "values": ["active", "inactive"],
                },
            ],
            "invariants": [],
        }
    )
    db = tmp_path / "r.db"
    first_synth = HeuristicSynthesizer()
    first = Kiln(first_synth, ProgramRegistry(db)).run(rows, c, "partner")
    assert first.status == "VERIFIED"
    assert first_synth.calls == 1

    second_synth = HeuristicSynthesizer()
    second = Kiln(second_synth, ProgramRegistry(db)).run(rows, c, "partner")
    assert second.status == "VERIFIED"
    assert second.reused_program is True
    assert second_synth.calls == 0
