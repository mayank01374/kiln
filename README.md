# Kiln

Kiln turns inconsistent source data into verified, reusable transformations. It profiles an input schema, builds a program in a restricted JSON DSL, runs it deterministically, and checks the output against a target contract.

Once a transformation has been verified, Kiln reuses it for matching schemas without another model call.

## Features

- Typed transformation DSL with no arbitrary code execution
- Static validation of source columns and target coverage
- Python and Polars execution backends
- Contract checks with concrete failure examples
- SQLite registry for verified transformations
- Deterministic offline synthesis and optional Claude integration
- CLI, FastAPI endpoint, Dockerfile, tests, and GitHub Actions CI

## Setup

Requires Python 3.11 or newer.

```bash
python -m venv .venv
```

Activate the environment:

```bash
# Windows
.venv\Scripts\activate

# macOS/Linux
source .venv/bin/activate
```

Install the project and development tools:

```bash
python -m pip install -e ".[dev]"
```

## Usage

Run the bundled demo:

```bash
kiln demo
```

Compile and verify an existing transformation:

```bash
kiln compile examples/customers_partner_a.csv examples/customer_contract.json examples/customer_program.json --output customers.csv
```

Infer a transformation with the offline synthesizer:

```bash
kiln run examples/customers_partner_a.csv examples/customer_contract.json --source-family partner-a
```

To use Claude for synthesis and repair, set `ANTHROPIC_API_KEY` and run the same command with `--llm`. The model can only propose DSL programs; validation, execution, and verification remain deterministic.

Start the API:

```bash
uvicorn kiln.api:app --reload
```

The API exposes `GET /health` and `POST /ingestions`.

## Development

```bash
pytest
ruff check .
```

## License

[MIT](LICENSE)
