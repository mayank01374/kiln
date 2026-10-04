from kiln.fingerprint import structural_fingerprint
from kiln.profiler import profile_rows


def test_same_schema_has_same_fingerprint():
    a = profile_rows([{"ID": "1", "Created": "2026-01-01"}])
    b = profile_rows([{"ID": "2", "Created": "2026-01-02"}])
    assert structural_fingerprint(a) == structural_fingerprint(b)
