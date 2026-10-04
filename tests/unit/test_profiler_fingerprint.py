from kiln.fingerprint import (
    drift_distance,
    schema_fingerprint,
    semantic_fingerprint,
    structural_fingerprint,
)
from kiln.profiler import profile_rows


def test_profiler_collects_semantic_and_statistical_evidence():
    profile = profile_rows(
        [
            {
                "Customer ID": "1",
                "Amount": "10.0",
                "Created": "2026-01-10",
                "Email": "a@example.com",
            },
            {
                "Customer ID": "2",
                "Amount": "20.0",
                "Created": "2026-01-11",
                "Email": "b@example.com",
            },
            {
                "Customer ID": "3",
                "Amount": "",
                "Created": "2026-01-12",
                "Email": None,
            },
        ]
    )

    amount = profile.column_map["Amount"]
    created = profile.column_map["Created"]
    email = profile.column_map["Email"]

    assert profile.row_count == 3
    assert amount.normalized_name == "amount"
    assert amount.logical_types == ["integer"]
    assert amount.null_ratio == 1 / 3
    assert amount.approximate_cardinality == 2
    assert amount.min_value == 10
    assert amount.max_value == 20
    assert amount.mean == 15
    assert amount.quantiles["0.5"] == 15
    assert created.date_format_candidates == ["%Y-%m-%d"]
    assert "date" in created.logical_types
    assert email.logical_types == ["email"]
    assert email.min_length == 13
    assert any(
        relationship.kind == "candidate_key" and relationship.columns == ["Customer ID"]
        for relationship in profile.relationships
    )


def test_structural_and_semantic_fingerprints_are_separate_and_deterministic():
    iso = profile_rows(
        [
            {"Created": "2026-01-15"},
            {"Created": "2026-02-16"},
        ]
    )
    us = profile_rows(
        [
            {"Created": "01/15/2026"},
            {"Created": "02/16/2026"},
        ]
    )

    assert structural_fingerprint(iso) == structural_fingerprint(us)
    assert semantic_fingerprint(iso) != semantic_fingerprint(us)
    assert schema_fingerprint(iso) == schema_fingerprint(
        profile_rows(list(reversed([{"Created": "2026-01-15"}, {"Created": "2026-02-16"}])))
    )


def test_drift_scoring_classifies_exact_compatible_drifted_and_unknown():
    baseline = profile_rows(
        [
            {"ID": "1", "Created": "2026-01-15"},
            {"ID": "2", "Created": "2026-02-16"},
        ]
    )
    compatible = profile_rows(
        [
            {"ID": "1", "Created": "2026-01-15", "Note": "a"},
            {"ID": "2", "Created": "2026-02-16", "Note": "b"},
        ]
    )
    drifted = profile_rows(
        [
            {"ID": "1", "Created": "01/15/2026"},
            {"ID": "2", "Created": "02/16/2026"},
        ]
    )
    unrelated = profile_rows([{"Completely Different": "value"}])

    exact_distance = drift_distance(baseline, baseline)
    compatible_distance = drift_distance(baseline, compatible)
    drifted_distance = drift_distance(baseline, drifted)
    unknown_distance = drift_distance(baseline, unrelated)

    assert exact_distance.classification == "EXACT"
    assert exact_distance.total == 0
    assert compatible_distance.classification == "COMPATIBLE"
    assert "note" in compatible_distance.changed_columns
    assert drifted_distance.classification == "DRIFTED"
    assert drifted_distance.format_pattern > 0
    assert "created" in drifted_distance.changed_columns
    assert unknown_distance.classification == "UNKNOWN"
    assert unknown_distance.total == 1
    assert drift_distance(baseline, drifted) == drifted_distance
