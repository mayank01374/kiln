from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Self

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    delete,
    desc,
    insert,
    select,
    update,
)
from sqlalchemy.engine import Engine

from .fingerprint import drift_distance
from .models import (
    HumanDecision,
    ProgramMatch,
    RegisteredProgram,
    RunResult,
    SchemaFingerprint,
    SourceProfile,
    TargetContract,
    TransformationProgram,
    VerificationResult,
)

metadata = MetaData()

programs = Table(
    "programs",
    metadata,
    Column("program_id", String(64), primary_key=True),
    Column("source_family", String(255), nullable=False, index=True),
    Column("contract_name", String(255), nullable=False, index=True),
    Column("contract_version", Integer, nullable=False),
    Column("version", Integer, nullable=False),
    Column("structural_hash", String(64), nullable=False, index=True),
    Column("semantic_hash", String(64), nullable=False),
    Column("fingerprint_json", Text, nullable=False),
    Column("profile_json", Text, nullable=False),
    Column("program_json", Text, nullable=False),
    Column("sample_rows_json", Text, nullable=False, default="[]"),
    Column("status", String(32), nullable=False, default="candidate", index=True),
    Column("parent_program_id", String(64), ForeignKey("programs.program_id"), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("promoted_at", DateTime(timezone=True), nullable=True),
    UniqueConstraint(
        "source_family",
        "contract_name",
        "contract_version",
        "version",
        name="uq_program_version",
    ),
)

verification_runs = Table(
    "verification_runs",
    metadata,
    Column("verification_id", String(64), primary_key=True),
    Column("run_id", String(64), nullable=False, index=True),
    Column("program_id", String(64), ForeignKey("programs.program_id"), nullable=True, index=True),
    Column("input_hash", String(64), nullable=False),
    Column("rows_input", Integer, nullable=False),
    Column("rows_output", Integer, nullable=False),
    Column("rows_quarantined", Integer, nullable=False),
    Column("passed", Boolean, nullable=False),
    Column("verification_json", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

runs = Table(
    "runs",
    metadata,
    Column("run_id", String(64), primary_key=True),
    Column("source_family", String(255), nullable=False, index=True),
    Column("contract_name", String(255), nullable=False),
    Column("result_json", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

human_decisions = Table(
    "human_decisions",
    metadata,
    Column("decision_id", String(64), primary_key=True),
    Column("program_id", String(64), ForeignKey("programs.program_id"), nullable=False, index=True),
    Column("field", String(255), nullable=False),
    Column("decision", String(32), nullable=False),
    Column("candidate_json", Text, nullable=True),
    Column("approved_json", Text, nullable=True),
    Column("reason", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

contracts = Table(
    "contracts",
    metadata,
    Column("contract_id", String(320), primary_key=True),
    Column("name", String(255), nullable=False, index=True),
    Column("version", Integer, nullable=False),
    Column("contract_json", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

jobs = Table(
    "jobs",
    metadata,
    Column("job_id", String(64), primary_key=True),
    Column("status", String(32), nullable=False, index=True),
    Column("payload_json", Text, nullable=False),
    Column("result_json", Text, nullable=True),
    Column("error", Text, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _json(value: Any) -> str:
    if hasattr(value, "model_dump_json"):
        return value.model_dump_json()
    return json.dumps(value, default=str, separators=(",", ":"))


def _database_url(database: str | Path | None) -> str:
    value: str | Path = database or os.getenv("DATABASE_URL") or Path(".kiln/kiln.db")
    if isinstance(value, Path) or "://" not in str(value):
        path = Path(value)
        path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{path.resolve().as_posix()}"
    url = str(value)
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url.removeprefix("postgres://")
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url.removeprefix("postgresql://")
    return url


class ProgramRegistry:
    """Versioned SQLAlchemy registry for local SQLite and PostgreSQL deployments."""

    def __init__(self, database: str | Path | None = None) -> None:
        self.url = _database_url(database)
        self.engine: Engine = create_engine(self.url, future=True)
        metadata.create_all(self.engine)

    def close(self) -> None:
        self.engine.dispose()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        self.close()

    @staticmethod
    def _record(row: Any) -> RegisteredProgram:
        values = row._mapping if hasattr(row, "_mapping") else row
        return RegisteredProgram(
            program_id=values["program_id"],
            source_family=values["source_family"],
            contract_name=values["contract_name"],
            contract_version=values["contract_version"],
            version=values["version"],
            fingerprint=SchemaFingerprint.model_validate_json(values["fingerprint_json"]),
            profile=SourceProfile.model_validate_json(values["profile_json"]),
            program=TransformationProgram.model_validate_json(values["program_json"]),
            status=values["status"],
            parent_program_id=values["parent_program_id"],
            sample_rows=json.loads(values["sample_rows_json"] or "[]"),
            created_at=values["created_at"],
            promoted_at=values["promoted_at"],
        )

    def save_program(
        self,
        source_family: str,
        contract: TargetContract,
        fingerprint: SchemaFingerprint,
        profile: SourceProfile,
        program: TransformationProgram,
        *,
        status: str = "production",
        parent_program_id: str | None = None,
        sample_rows: list[dict[str, Any]] | None = None,
    ) -> RegisteredProgram:
        with self.engine.begin() as connection:
            latest = connection.execute(
                select(programs.c.version)
                .where(programs.c.source_family == source_family)
                .where(programs.c.contract_name == contract.name)
                .where(programs.c.contract_version == contract.version)
                .order_by(desc(programs.c.version))
                .limit(1)
            ).scalar_one_or_none()
            version = (latest or 0) + 1
            program_id = uuid.uuid4().hex
            now = _now()
            connection.execute(
                insert(programs).values(
                    program_id=program_id,
                    source_family=source_family,
                    contract_name=contract.name,
                    contract_version=contract.version,
                    version=version,
                    structural_hash=fingerprint.structural_hash,
                    semantic_hash=fingerprint.semantic_hash,
                    fingerprint_json=_json(fingerprint),
                    profile_json=_json(profile),
                    program_json=_json(program),
                    sample_rows_json=json.dumps(sample_rows or [], default=str),
                    status=status,
                    parent_program_id=parent_program_id,
                    created_at=now,
                    promoted_at=now if status == "production" else None,
                )
            )
        record = self.get_program(program_id)
        if record is None:  # pragma: no cover
            raise RuntimeError(f"Program {program_id} was not persisted")
        return record

    def get_program(self, program_id: str) -> RegisteredProgram | None:
        with self.engine.connect() as connection:
            row = connection.execute(
                select(programs).where(programs.c.program_id == program_id)
            ).first()
        return self._record(row) if row else None

    def list_programs(
        self, source_family: str, contract: TargetContract, limit: int = 30
    ) -> list[RegisteredProgram]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(programs)
                .where(programs.c.source_family == source_family)
                .where(programs.c.contract_name == contract.name)
                .where(programs.c.contract_version == contract.version)
                .where(programs.c.status.in_(["verified", "production"]))
                .order_by(desc(programs.c.version))
                .limit(limit)
            ).all()
        return [self._record(row) for row in rows]

    def find_best(
        self, source_family: str, contract: TargetContract, current: SourceProfile
    ) -> ProgramMatch:
        candidates = self.list_programs(source_family, contract)
        if not candidates:
            return ProgramMatch()
        scored = [(drift_distance(record.profile, current), record) for record in candidates]
        scored.sort(key=lambda pair: (pair[0].total, -pair[1].version))
        distance, record = scored[0]
        return ProgramMatch(record=record, distance=distance)

    def save_verification(
        self,
        run_id: str,
        program_id: str | None,
        input_hash: str,
        result: VerificationResult,
    ) -> str:
        verification_id = uuid.uuid4().hex
        with self.engine.begin() as connection:
            connection.execute(
                insert(verification_runs).values(
                    verification_id=verification_id,
                    run_id=run_id,
                    program_id=program_id,
                    input_hash=input_hash,
                    rows_input=result.rows_input,
                    rows_output=result.rows_output,
                    rows_quarantined=result.rows_quarantined,
                    passed=result.passed,
                    verification_json=_json(result),
                    created_at=_now(),
                )
            )
        return verification_id

    def save_run(self, result: RunResult, contract_name: str) -> None:
        with self.engine.begin() as connection:
            connection.execute(delete(runs).where(runs.c.run_id == result.run_id))
            connection.execute(
                insert(runs).values(
                    run_id=result.run_id,
                    source_family=result.source_family,
                    contract_name=contract_name,
                    result_json=_json(result),
                    created_at=_now(),
                )
            )

    def get_run(self, run_id: str) -> RunResult | None:
        with self.engine.connect() as connection:
            value = connection.execute(
                select(runs.c.result_json).where(runs.c.run_id == run_id)
            ).scalar_one_or_none()
        return RunResult.model_validate_json(value) if value else None

    def save_human_decision(self, decision: HumanDecision) -> str:
        decision_id = uuid.uuid4().hex
        with self.engine.begin() as connection:
            connection.execute(
                insert(human_decisions).values(
                    decision_id=decision_id,
                    program_id=decision.program_id,
                    field=decision.field,
                    decision=decision.decision,
                    candidate_json=json.dumps(decision.candidate_mapping, default=str),
                    approved_json=json.dumps(decision.approved_mapping, default=str),
                    reason=decision.reason,
                    created_at=_now(),
                )
            )
        return decision_id

    def save_contract(self, contract: TargetContract) -> str:
        contract_id = f"{contract.name}:v{contract.version}"
        with self.engine.begin() as connection:
            connection.execute(delete(contracts).where(contracts.c.contract_id == contract_id))
            connection.execute(
                insert(contracts).values(
                    contract_id=contract_id,
                    name=contract.name,
                    version=contract.version,
                    contract_json=_json(contract),
                    created_at=_now(),
                )
            )
        return contract_id

    def get_contract(self, contract_id: str) -> TargetContract | None:
        with self.engine.connect() as connection:
            value = connection.execute(
                select(contracts.c.contract_json).where(contracts.c.contract_id == contract_id)
            ).scalar_one_or_none()
        return TargetContract.model_validate_json(value) if value else None

    def enqueue_job(self, payload: dict[str, Any]) -> str:
        job_id = uuid.uuid4().hex
        now = _now()
        with self.engine.begin() as connection:
            connection.execute(
                insert(jobs).values(
                    job_id=job_id,
                    status="QUEUED",
                    payload_json=json.dumps(payload, default=str),
                    created_at=now,
                    updated_at=now,
                )
            )
        return job_id

    def claim_job(self) -> tuple[str, dict[str, Any]] | None:
        # One local worker is supported portably. PostgreSQL fleets can extend this
        # with SELECT ... FOR UPDATE SKIP LOCKED without changing the repository API.
        with self.engine.begin() as connection:
            row = connection.execute(
                select(jobs)
                .where(jobs.c.status == "QUEUED")
                .order_by(jobs.c.created_at)
                .limit(1)
            ).first()
            if not row:
                return None
            job_id = row._mapping["job_id"]
            changed = connection.execute(
                update(jobs)
                .where(jobs.c.job_id == job_id)
                .where(jobs.c.status == "QUEUED")
                .values(status="RUNNING", updated_at=_now())
            )
            if changed.rowcount != 1:
                return None
            return job_id, json.loads(row._mapping["payload_json"])

    def finish_job(
        self,
        job_id: str,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                update(jobs)
                .where(jobs.c.job_id == job_id)
                .values(
                    status="FAILED" if error else "DONE",
                    result_json=json.dumps(result, default=str) if result is not None else None,
                    error=error,
                    updated_at=_now(),
                )
            )

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self.engine.connect() as connection:
            row = connection.execute(select(jobs).where(jobs.c.job_id == job_id)).first()
        if not row:
            return None
        values = row._mapping
        return {
            "job_id": values["job_id"],
            "status": values["status"],
            "result": json.loads(values["result_json"]) if values["result_json"] else None,
            "error": values["error"],
        }

    # Compatibility wrappers for callers using the original registry API.
    def save(
        self,
        source_family: str,
        contract_name: str,
        fingerprint_hash: str,
        program: TransformationProgram,
    ) -> int:
        contract = TargetContract(name=contract_name, fields=[])
        fingerprint = SchemaFingerprint(
            structural_hash=fingerprint_hash,
            semantic_hash=fingerprint_hash,
            structural_payload={},
            semantic_payload={},
        )
        record = self.save_program(
            source_family,
            contract,
            fingerprint,
            SourceProfile(row_count=0, columns=[]),
            program,
        )
        return record.version

    def exact(
        self, source_family: str, contract_name: str, fingerprint_hash: str
    ) -> RegisteredProgram | None:
        with self.engine.connect() as connection:
            row = connection.execute(
                select(programs)
                .where(programs.c.source_family == source_family)
                .where(programs.c.contract_name == contract_name)
                .where(programs.c.structural_hash == fingerprint_hash)
                .where(programs.c.status.in_(["verified", "production"]))
                .order_by(desc(programs.c.version))
                .limit(1)
            ).first()
        return self._record(row) if row else None

    def latest(self, source_family: str, contract_name: str) -> RegisteredProgram | None:
        with self.engine.connect() as connection:
            row = connection.execute(
                select(programs)
                .where(programs.c.source_family == source_family)
                .where(programs.c.contract_name == contract_name)
                .order_by(desc(programs.c.version))
                .limit(1)
            ).first()
        return self._record(row) if row else None

    def set_program_status(self, program_id: str, status: str) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                update(programs)
                .where(programs.c.program_id == program_id)
                .values(
                    status=status,
                    promoted_at=_now() if status == "production" else None,
                )
            )

    def list_versions_for_program(self, program_id: str) -> list[RegisteredProgram]:
        record = self.get_program(program_id)
        if record is None:
            return []
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(programs)
                .where(programs.c.source_family == record.source_family)
                .where(programs.c.contract_name == record.contract_name)
                .where(programs.c.contract_version == record.contract_version)
                .order_by(desc(programs.c.version))
            ).all()
        return [self._record(row) for row in rows]
