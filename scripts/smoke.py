import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kiln.io import load_contract, read_csv_rows
from kiln.orchestrator import Kiln
from kiln.registry import ProgramRegistry
from kiln.synthesis import HeuristicSynthesizer

root = Path(__file__).resolve().parents[1]
rows = read_csv_rows(root / "examples/customers_partner_a.csv")
contract = load_contract(root / "examples/customer_contract.json")
reg = root / ".kiln-smoke.db"
if reg.exists():
    reg.unlink()
with ProgramRegistry(reg) as registry:
    result = Kiln(HeuristicSynthesizer(), registry).run(rows, contract, "partner-a")
assert result.status == "VERIFIED", result
print(result.model_dump_json(indent=2))
reg.unlink(missing_ok=True)
