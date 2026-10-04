from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Self

from .models import TransformationProgram


@dataclass
class RegistryHit:
    program: TransformationProgram
    version: int


class ProgramRegistry:
    def __init__(self, path: str | Path = ".kiln/registry.db") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS programs (
                source_family TEXT NOT NULL,
                contract_name TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                version INTEGER NOT NULL,
                program_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (source_family, contract_name, version)
            )
        """)
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def exact(self, source_family: str, contract_name: str, fingerprint: str) -> RegistryHit | None:
        row = self.db.execute(
            """SELECT program_json, version FROM programs
               WHERE source_family=? AND contract_name=? AND fingerprint=?
               ORDER BY version DESC LIMIT 1""",
            (source_family, contract_name, fingerprint),
        ).fetchone()
        return (
            RegistryHit(TransformationProgram.model_validate_json(row[0]), row[1]) if row else None
        )

    def latest(self, source_family: str, contract_name: str) -> RegistryHit | None:
        row = self.db.execute(
            """SELECT program_json, version FROM programs
               WHERE source_family=? AND contract_name=?
               ORDER BY version DESC LIMIT 1""",
            (source_family, contract_name),
        ).fetchone()
        return (
            RegistryHit(TransformationProgram.model_validate_json(row[0]), row[1]) if row else None
        )

    def save(
        self,
        source_family: str,
        contract_name: str,
        fingerprint: str,
        program: TransformationProgram,
    ) -> int:
        latest = self.db.execute(
            "SELECT COALESCE(MAX(version), 0) FROM programs WHERE source_family=? AND contract_name=?",
            (source_family, contract_name),
        ).fetchone()[0]
        version = latest + 1
        self.db.execute(
            "INSERT INTO programs(source_family, contract_name, fingerprint, version, program_json) VALUES(?,?,?,?,?)",
            (source_family, contract_name, fingerprint, version, program.model_dump_json()),
        )
        self.db.commit()
        return version
