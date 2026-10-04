from __future__ import annotations

from .compiler import static_check
from .executor import execute_python
from .fingerprint import structural_fingerprint
from .models import RunResult, TargetContract
from .patches import apply_patch
from .profiler import profile_rows
from .registry import ProgramRegistry
from .synthesis import Synthesizer
from .verifier import verify


class Kiln:
    def __init__(
        self, synthesizer: Synthesizer, registry: ProgramRegistry, max_repairs: int = 2
    ) -> None:
        self.synthesizer = synthesizer
        self.registry = registry
        self.max_repairs = max_repairs

    def run(
        self, rows: list[dict[str, object]], contract: TargetContract, source_family: str
    ) -> RunResult:
        profile = profile_rows(rows)
        fingerprint = structural_fingerprint(profile)
        exact = self.registry.exact(source_family, contract.name, fingerprint)
        reused = exact is not None
        program = exact.program if exact else self.synthesizer.synthesize(profile, contract)

        if program.unresolved_fields:
            return RunResult(
                status="HUMAN_REVIEW",
                source_family=source_family,
                structural_fingerprint=fingerprint,
                program=program,
                model_calls=self.synthesizer.calls,
                reused_program=reused,
            )

        source_columns = set(rows[0]) if rows else set()
        static = static_check(program, contract, source_columns)
        if not static.ok:
            return RunResult(
                status="FAILED",
                source_family=source_family,
                structural_fingerprint=fingerprint,
                program=program,
                model_calls=self.synthesizer.calls,
                reused_program=reused,
            )

        repair_count = 0
        while True:
            output = execute_python(rows, program)
            result = verify(rows, output, contract)
            if result.passed:
                if not reused:
                    self.registry.save(source_family, contract.name, fingerprint, program)
                return RunResult(
                    status="VERIFIED",
                    source_family=source_family,
                    structural_fingerprint=fingerprint,
                    program=program,
                    verification=result,
                    model_calls=self.synthesizer.calls,
                    repair_iterations=repair_count,
                    reused_program=reused,
                )

            if repair_count >= self.max_repairs:
                return RunResult(
                    status="HUMAN_REVIEW",
                    source_family=source_family,
                    structural_fingerprint=fingerprint,
                    program=program,
                    verification=result,
                    model_calls=self.synthesizer.calls,
                    repair_iterations=repair_count,
                    reused_program=reused,
                )

            patch = self.synthesizer.repair(program, result, profile, contract)
            if patch is None:
                return RunResult(
                    status="HUMAN_REVIEW",
                    source_family=source_family,
                    structural_fingerprint=fingerprint,
                    program=program,
                    verification=result,
                    model_calls=self.synthesizer.calls,
                    repair_iterations=repair_count,
                    reused_program=reused,
                )
            program = apply_patch(program, patch)
            repair_count += 1
