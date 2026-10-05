from pathlib import Path

from kiln.fingerprint import schema_fingerprint
from kiln.models import (
    HumanDecision,
    RunResult,
    TargetContract,
    TransformationProgram,
    VerificationResult,
)
from kiln.orchestrator import Kiln
from kiln.profiler import profile_rows
from kiln.registry import ProgramRegistry
from kiln.synthesis import HeuristicSynthesizer


def _contract() -> TargetContract:
    return TargetContract.model_validate(
        {
            "name": "customer",
            "version": 2,
            "fields": [{"name": "customer_id", "type": "string", "required": True}],
        }
    )


def _program(column: str) -> TransformationProgram:
    return TransformationProgram.model_validate(
        {"mappings": {"customer_id": {"op": "source", "column": column}}}
    )


def test_registry_preserves_version_lineage_and_candidate_status(tmp_path: Path):
    profile = profile_rows([{"ID": "1"}])
    fingerprint = schema_fingerprint(profile)
    contract = _contract()

    with ProgramRegistry(tmp_path / "registry.db") as registry:
        first = registry.save_program(
            "partner",
            contract,
            fingerprint,
            profile,
            _program("ID"),
            status="production",
            sample_rows=[{"ID": "1"}],
        )
        second = registry.save_program(
            "partner",
            contract,
            fingerprint,
            profile,
            _program("Customer ID"),
            status="candidate",
            parent_program_id=first.program_id,
        )
        third = registry.save_program(
            "partner",
            contract,
            fingerprint,
            profile,
            _program("customer_id"),
            status="verified",
            parent_program_id=second.program_id,
        )

        history = registry.list_versions_for_program(third.program_id)
        reusable = registry.list_programs("partner", contract)

        assert [record.version for record in history] == [3, 2, 1]
        assert [record.parent_program_id for record in history] == [
            second.program_id,
            first.program_id,
            None,
        ]
        assert [record.version for record in reusable] == [3, 1]
        assert registry.get_program(first.program_id).sample_rows == [{"ID": "1"}]
        assert registry.find_best("partner", contract, profile).record.program_id == third.program_id


def test_registry_persists_contract_runs_verification_decisions_and_jobs(tmp_path: Path):
    contract = _contract()
    profile = profile_rows([{"ID": "1"}])
    with ProgramRegistry(tmp_path / "registry.db") as registry:
        record = registry.save_program(
            "partner",
            contract,
            schema_fingerprint(profile),
            profile,
            _program("ID"),
        )
        contract_id = registry.save_contract(contract)
        verification = VerificationResult(
            passed=True,
            invariants_checked=1,
            failures=[],
            rows_input=1,
            rows_output=1,
        )
        run = RunResult(
            status="VERIFIED",
            source_family="partner",
            structural_fingerprint=record.fingerprint.structural_hash,
            program=record.program,
            verification=verification,
        )
        registry.save_run(run, contract.name)
        verification_id = registry.save_verification(
            run.run_id, record.program_id, "input-hash", verification
        )
        decision_id = registry.save_human_decision(
            HumanDecision(
                program_id=record.program_id,
                field="customer_id",
                decision="approved",
                candidate_mapping={"op": "source", "column": "ID"},
                approved_mapping={"op": "source", "column": "ID"},
                reason="Validated against the source sample",
            )
        )
        job_id = registry.enqueue_job({"source_family": "partner"})
        claimed_id, payload = registry.claim_job()
        registry.finish_job(job_id, {"run_id": run.run_id})

        assert registry.get_contract(contract_id) == contract
        assert registry.get_run(run.run_id) == run
        assert verification_id
        assert decision_id
        assert (claimed_id, payload) == (job_id, {"source_family": "partner"})
        assert registry.get_job(job_id) == {
            "job_id": job_id,
            "status": "DONE",
            "result": {"run_id": run.run_id},
            "error": None,
        }


def test_existing_orchestrator_reuses_program_through_sqlalchemy_registry(tmp_path: Path):
    rows = [{"customer_id": "1"}]
    contract = _contract()
    database = tmp_path / "registry.db"

    first = Kiln(HeuristicSynthesizer(), ProgramRegistry(database)).run(rows, contract, "partner")
    second_synthesizer = HeuristicSynthesizer()
    second = Kiln(second_synthesizer, ProgramRegistry(database)).run(
        rows, contract, "partner"
    )

    assert first.status == "VERIFIED"
    assert second.status == "VERIFIED"
    assert second.reused_program is True
    assert second_synthesizer.calls == 0


def test_database_url_configures_zero_argument_registry(tmp_path: Path, monkeypatch):
    database = tmp_path / "from-environment.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database.as_posix()}")

    with ProgramRegistry() as registry:
        assert registry.url == f"sqlite:///{database.as_posix()}"

    assert database.exists()
