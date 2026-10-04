from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from .compiler import execute_polars, static_check
from .io import load_contract, load_program, read_csv_rows, write_csv_rows
from .orchestrator import Kiln
from .profiler import profile_rows
from .registry import ProgramRegistry
from .synthesis import AnthropicSynthesizer, HeuristicSynthesizer
from .verifier import verify

app = typer.Typer(help="Kiln - self-verifying data transformation compiler")
console = Console()
DEFAULT_REGISTRY = Path(".kiln/registry.db")


@app.command()
def profile(input: Path):
    """Profile a CSV without sending rows to a model."""
    console.print_json(profile_rows(read_csv_rows(input)).model_dump_json())


@app.command()
def compile(input: Path, contract: Path, program: Path, output: Path = Path("out.csv")):
    """Statically validate a DSL program, compile it to Polars, execute, and verify."""
    rows = read_csv_rows(input)
    c = load_contract(contract)
    p = load_program(program)
    check = static_check(p, c, set(rows[0]) if rows else set())
    if not check.ok:
        console.print({"status": "compile_error", "errors": check.errors})
        raise typer.Exit(2)
    transformed = execute_polars(rows, p)
    result = verify(rows, transformed, c)
    write_csv_rows(output, transformed)
    console.print(
        {
            "status": "verified" if result.passed else "verification_failed",
            "output": str(output),
            "verification": result.model_dump(mode="json"),
        }
    )


@app.command()
def run(
    input: Path,
    contract: Path,
    source_family: Annotated[str, typer.Option("--source-family")] = "demo",
    llm: Annotated[
        bool,
        typer.Option(
            "--llm", help="Use Claude for synthesis instead of the deterministic baseline"
        ),
    ] = False,
    registry: Annotated[Path, typer.Option("--registry")] = DEFAULT_REGISTRY,
):
    """Run the bounded profile, synthesis, verification, and repair loop."""
    synth = AnthropicSynthesizer() if llm else HeuristicSynthesizer()
    with ProgramRegistry(registry) as program_registry:
        result = Kiln(synth, program_registry).run(
            read_csv_rows(input), load_contract(contract), source_family
        )
    console.print_json(result.model_dump_json(indent=2))
    if result.status != "VERIFIED":
        raise typer.Exit(2)


@app.command()
def demo():
    """Run the bundled customer-ingestion demo twice to show zero-call reuse."""
    root = Path(__file__).resolve().parents[2]
    rows = read_csv_rows(root / "examples" / "customers_partner_a.csv")
    contract = load_contract(root / "examples" / "customer_contract.json")
    registry_path = Path(".kiln/demo.db")
    if registry_path.exists():
        registry_path.unlink()
    synth1 = HeuristicSynthesizer()
    with ProgramRegistry(registry_path) as registry:
        first = Kiln(synth1, registry).run(rows, contract, "partner-a")
    synth2 = HeuristicSynthesizer()
    with ProgramRegistry(registry_path) as registry:
        second = Kiln(synth2, registry).run(rows, contract, "partner-a")
    console.print("[bold]first ingestion[/bold]")
    console.print(json.dumps(first.model_dump(mode="json"), indent=2, default=str))
    console.print("\n[bold]same schema again[/bold]")
    console.print(json.dumps(second.model_dump(mode="json"), indent=2, default=str))


if __name__ == "__main__":
    app()
