from pathlib import Path

from kiln.models import (
    ProgramPatch,
    SourceProfile,
    TargetContract,
    TransformationProgram,
    VerificationResult,
)
from kiln.orchestrator import Kiln
from kiln.registry import ProgramRegistry
from kiln.synthesis import Synthesizer


class ScriptedSynthesizer(Synthesizer):
    def __init__(self):
        self.calls = 0

    def synthesize(self, profile: SourceProfile, contract: TargetContract) -> TransformationProgram:
        self.calls += 1
        return TransformationProgram.model_validate(
            {
                "mappings": {
                    "id": {"op": "source", "column": "ID"},
                    "status": {
                        "op": "map_values",
                        "value": {"op": "source", "column": "S"},
                        "mapping": {"Y": "yes"},
                    },
                }
            }
        )

    def repair(
        self,
        program: TransformationProgram,
        verification: VerificationResult,
        profile: SourceProfile,
        contract: TargetContract,
    ) -> ProgramPatch:
        self.calls += 1
        return ProgramPatch.model_validate(
            {
                "patches": [
                    {
                        "type": "replace_expression",
                        "target_field": "status",
                        "expression": {
                            "op": "map_values",
                            "value": {"op": "source", "column": "S"},
                            "mapping": {"Y": "active", "N": "inactive"},
                        },
                    }
                ]
            }
        )


def test_counterexample_drives_bounded_patch(tmp_path: Path):
    contract = TargetContract.model_validate(
        {
            "name": "t",
            "fields": [
                {"name": "id", "type": "string", "required": True},
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
    synth = ScriptedSynthesizer()
    result = Kiln(synth, ProgramRegistry(tmp_path / "r.db"), max_repairs=2).run(
        [{"ID": "1", "S": "Y"}], contract, "sender"
    )
    assert result.status == "VERIFIED"
    assert result.repair_iterations == 1
    assert synth.calls == 2


def test_underdetermined_required_field_abstains(tmp_path: Path):
    from kiln.synthesis import HeuristicSynthesizer

    contract = TargetContract.model_validate(
        {
            "name": "t",
            "fields": [{"name": "amount_usd", "type": "float", "required": True}],
            "invariants": [],
        }
    )
    result = Kiln(HeuristicSynthesizer(), ProgramRegistry(tmp_path / "r.db")).run(
        [{"amt": "500"}], contract, "sender"
    )
    assert result.status == "HUMAN_REVIEW"
    assert result.program.unresolved_fields == ["amount_usd"]
