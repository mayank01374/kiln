from __future__ import annotations

import re
from abc import ABC
from typing import Any


class DomainPlugin(ABC):
    name = "generic"

    def validate(self, validator: str, value: Any, row: dict[str, Any]) -> tuple[bool, str]:
        return True, ""

    def lookup_tables(self) -> dict[str, dict[str, Any]]:
        return {}


class CRMPlugin(DomainPlugin):
    name = "crm"

    def validate(self, validator: str, value: Any, row: dict[str, Any]) -> tuple[bool, str]:
        if validator == "crm_email":
            valid = value in (None, "") or bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", str(value)))
            return valid, "invalid CRM email" if not valid else ""
        return True, ""


class CommercePlugin(DomainPlugin):
    name = "commerce"

    def validate(self, validator: str, value: Any, row: dict[str, Any]) -> tuple[bool, str]:
        if validator == "positive_quantity":
            try:
                valid = float(value) >= 0
            except (TypeError, ValueError):
                valid = False
            return valid, "quantity must be non-negative" if not valid else ""
        if validator == "currency_code":
            valid = value in (None, "") or bool(re.match(r"^[A-Z]{3}$", str(value)))
            return valid, "currency must be an ISO-like 3-letter code" if not valid else ""
        return True, ""


def _luhn(number: str) -> bool:
    digits = [int(character) for character in number]
    total = 0
    parity = len(digits) % 2
    for index, digit in enumerate(digits):
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def valid_npi(value: Any) -> bool:
    """Validate an NPI using its 80840-prefixed Luhn checksum."""
    text = str(value or "").strip()
    return len(text) == 10 and text.isdigit() and _luhn("80840" + text)


class HealthcarePlugin(DomainPlugin):
    name = "healthcare"

    def validate(self, validator: str, value: Any, row: dict[str, Any]) -> tuple[bool, str]:
        if validator == "npi":
            valid = valid_npi(value)
            return valid, "invalid NPI checksum" if not valid else ""
        if validator == "taxonomy_code":
            valid = value in (None, "") or bool(re.match(r"^[0-9A-Z]{10}$", str(value)))
            return valid, "invalid taxonomy-code shape" if not valid else ""
        return True, ""


_PLUGINS = {
    plugin.name: plugin
    for plugin in [
        DomainPlugin(),
        CRMPlugin(),
        CommercePlugin(),
        HealthcarePlugin(),
    ]
}


def get_domain_plugin(name: str | None) -> DomainPlugin:
    return _PLUGINS.get(name or "generic", _PLUGINS["generic"])
