from __future__ import annotations

import csv
import json
from pathlib import Path

from .models import TargetContract, TransformationProgram


def read_csv_rows(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv_rows(path: str | Path, rows: list[dict[str, object]]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        p.write_text("", encoding="utf-8")
        return
    with p.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def load_contract(path: str | Path) -> TargetContract:
    return TargetContract.model_validate_json(Path(path).read_text(encoding="utf-8"))


def load_program(path: str | Path) -> TransformationProgram:
    return TransformationProgram.model_validate_json(Path(path).read_text(encoding="utf-8"))


def dump_json(path: str | Path, obj: object) -> None:
    data = obj.model_dump(mode="json") if hasattr(obj, "model_dump") else obj
    Path(path).write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
