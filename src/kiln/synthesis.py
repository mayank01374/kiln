from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod

import httpx

from .models import (
    ProgramPatch,
    SourceProfile,
    TargetContract,
    TransformationProgram,
    VerificationResult,
)


class Synthesizer(ABC):
    calls: int = 0

    @abstractmethod
    def synthesize(self, profile: SourceProfile, contract: TargetContract) -> TransformationProgram:
        raise NotImplementedError

    def repair(
        self,
        program: TransformationProgram,
        verification: VerificationResult,
        profile: SourceProfile,
        contract: TargetContract,
    ) -> ProgramPatch | None:
        return None


class HeuristicSynthesizer(Synthesizer):
    """Deterministic baseline used for demos/tests. It intentionally handles only obvious mappings."""

    def __init__(self) -> None:
        self.calls = 0

    def synthesize(self, profile: SourceProfile, contract: TargetContract) -> TransformationProgram:
        self.calls += 1
        cols = {c.name.lower().replace(" ", "_"): c.name for c in profile.columns}
        mappings = {}
        aliases = {
            "customer_id": ["customer_id", "cust_id", "customer_number", "id"],
            "email": ["email", "email_address", "contact"],
            "signup_date": ["signup_date", "date_created", "created", "created_at", "registered"],
            "status": ["status", "active", "ac_st", "active_ind"],
            "first_name": ["first_name", "fname"],
            "last_name": ["last_name", "lname"],
        }
        for field in contract.fields:
            for alias in aliases.get(field.name, [field.name]):
                if alias in cols:
                    source = cols[alias]
                    expr: dict = {"op": "source", "column": source}
                    if field.type == "date":
                        formats = next(
                            (c.date_format_candidates for c in profile.columns if c.name == source),
                            [],
                        ) or ["%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"]
                        expr = {"op": "parse_date", "value": expr, "formats": formats}
                    mappings[field.name] = expr
                    break
        # Common full-name split.
        full = cols.get("full_name") or cols.get("name")
        if full:
            mappings.setdefault(
                "first_name",
                {
                    "op": "split",
                    "value": {"op": "source", "column": full},
                    "delimiter": " ",
                    "index": 0,
                },
            )
            mappings.setdefault(
                "last_name",
                {
                    "op": "split",
                    "value": {"op": "source", "column": full},
                    "delimiter": " ",
                    "index": -1,
                },
            )
        # Common boolean/enum status encodings.
        if "status" in mappings:
            target = next((f for f in contract.fields if f.name == "status"), None)
            if target and target.values:
                src = mappings["status"]
                mappings["status"] = {
                    "op": "map_values",
                    "value": src,
                    "mapping": {
                        "Y": "active",
                        "N": "inactive",
                        "A": "active",
                        "I": "inactive",
                        "1": "active",
                        "0": "inactive",
                        "active": "active",
                        "inactive": "inactive",
                    },
                }
        unresolved = sorted(contract.required_fields - set(mappings))
        return TransformationProgram(mappings=mappings, unresolved_fields=unresolved)


class AnthropicSynthesizer(Synthesizer):
    """Optional real-model synthesizer. The model can only return Kiln DSL JSON; it never executes code."""

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self.model = model or os.environ.get("KILN_MODEL", "claude-sonnet-5")
        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY is required")
        self.calls = 0

    def _call(self, prompt: str) -> str:
        self.calls += 1
        response = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": self.model,
                "max_tokens": 4000,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=60,
        )
        response.raise_for_status()
        blocks = response.json()["content"]
        return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")

    @staticmethod
    def _json(text: str) -> dict:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end < start:
            raise ValueError("Model did not return JSON")
        return json.loads(text[start : end + 1])

    def synthesize(self, profile: SourceProfile, contract: TargetContract) -> TransformationProgram:
        prompt = f"""You are Kiln's bounded transformation synthesis agent.
Return ONLY one JSON object matching TransformationProgram.
You may use only these ops: source,literal,cast,parse_date,trim,lowercase,uppercase,normalize_whitespace,split,concat,map_values,coalesce,regex_extract.
Never invent source columns. If a required mapping is underdetermined, list it in unresolved_fields instead of guessing.
SOURCE PROFILE:\n{profile.model_dump_json(indent=2)}\nTARGET CONTRACT:\n{contract.model_dump_json(indent=2)}"""
        return TransformationProgram.model_validate(self._json(self._call(prompt)))

    def repair(
        self,
        program: TransformationProgram,
        verification: VerificationResult,
        profile: SourceProfile,
        contract: TargetContract,
    ) -> ProgramPatch | None:
        prompt = f"""You repair a Kiln transformation program using concrete verifier failures.
Return ONLY ProgramPatch JSON. Make the smallest patch possible. Never invent source columns.
PROGRAM:\n{program.model_dump_json(indent=2)}\nFAILURES:\n{verification.model_dump_json(indent=2)}\nPROFILE:\n{profile.model_dump_json(indent=2)}\nCONTRACT:\n{contract.model_dump_json(indent=2)}"""
        return ProgramPatch.model_validate(self._json(self._call(prompt)))
