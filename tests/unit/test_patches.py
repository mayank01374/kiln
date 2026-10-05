import pytest

from kiln.models import ProgramPatch, TransformationProgram
from kiln.patches import apply_patch, changed_targets, diff_programs


def _program() -> TransformationProgram:
    return TransformationProgram.model_validate(
        {
            "version": 3,
            "mappings": {
                "date": {
                    "op": "parse_date",
                    "value": {"op": "source", "column": "old_date"},
                    "formats": ["%Y-%m-%d"],
                },
                "status": {
                    "op": "map_values",
                    "value": {"op": "source", "column": "status_code"},
                    "mapping": {"Y": "active"},
                },
                "name": {"op": "source", "column": "full_name"},
                "obsolete": {"op": "literal", "value": "remove me"},
            },
            "unresolved_fields": ["new_field", "still_missing"],
        }
    )


def test_apply_all_patch_operations_without_mutating_original():
    original = _program()
    patch = ProgramPatch.model_validate(
        {
            "patches": [
                {
                    "type": "change_date_formats",
                    "target_field": "date",
                    "formats": ["%d/%m/%Y", "%Y-%m-%d"],
                },
                {
                    "type": "extend_enum_map",
                    "target_field": "status",
                    "mapping": {"N": "inactive"},
                },
                {
                    "type": "change_source_reference",
                    "target_field": "name",
                    "old_column": "full_name",
                    "new_column": "display_name",
                },
                {
                    "type": "add_fallback",
                    "target_field": "name",
                    "fallback": {"op": "literal", "value": "Unknown"},
                },
                {"type": "remove_mapping", "target_field": "obsolete"},
                {
                    "type": "add_mapping",
                    "target_field": "new_field",
                    "expression": {"op": "source", "column": "new_value"},
                },
                {
                    "type": "replace_expression",
                    "target_field": "status",
                    "expression": {"op": "source", "column": "normalized_status"},
                },
            ]
        }
    )

    updated = apply_patch(original, patch)

    assert updated.version == 4
    assert original.version == 3
    assert original.mappings["date"].formats == ["%Y-%m-%d"]
    assert updated.mappings["date"].formats == ["%d/%m/%Y", "%Y-%m-%d"]
    assert updated.mappings["name"].model_dump(mode="json") == {
        "op": "coalesce",
        "values": [
            {"op": "source", "column": "display_name"},
            {"op": "literal", "value": "Unknown"},
        ],
    }
    assert updated.mappings["status"].model_dump(mode="json") == {
        "op": "source",
        "column": "normalized_status",
    }
    assert "obsolete" not in updated.mappings
    assert updated.unresolved_fields == ["still_missing"]


def test_change_source_reference_rewrites_nested_expression_only():
    program = TransformationProgram.model_validate(
        {
            "mappings": {
                "label": {
                    "op": "conditional",
                    "condition": {
                        "left": {"op": "source", "column": "old"},
                        "operator": "==",
                        "right": {"op": "literal", "value": "Y"},
                    },
                    "if_true": {"op": "source", "column": "old"},
                    "if_false": {"op": "source", "column": "untouched"},
                }
            }
        }
    )
    patch = ProgramPatch.model_validate(
        {
            "patches": [
                {
                    "type": "change_source_reference",
                    "target_field": "label",
                    "old_column": "old",
                    "new_column": "new",
                }
            ]
        }
    )

    expression = apply_patch(program, patch).mappings["label"].model_dump(mode="json")
    assert expression["condition"]["left"]["column"] == "new"
    assert expression["if_true"]["column"] == "new"
    assert expression["if_false"]["column"] == "untouched"


def test_changed_targets_and_diff_are_stable_and_auditable():
    original = _program()
    patch = ProgramPatch.model_validate(
        {
            "patches": [
                {"type": "remove_mapping", "target_field": "obsolete"},
                {
                    "type": "replace_expression",
                    "target_field": "name",
                    "expression": {"op": "source", "column": "preferred_name"},
                },
                {
                    "type": "add_fallback",
                    "target_field": "name",
                    "fallback": {"op": "literal", "value": None},
                },
            ]
        }
    )

    updated = apply_patch(original, patch)
    program_diff = diff_programs(original, updated)

    assert changed_targets(patch) == ["name", "obsolete"]
    assert program_diff["changed_fields"] == ["name", "obsolete"]
    assert program_diff["changes"]["obsolete"] == {
        "before": {"op": "literal", "value": "remove me"},
        "after": None,
    }
    assert program_diff["changes"]["name"]["before"] == {
        "op": "source",
        "column": "full_name",
    }


@pytest.mark.parametrize(
    ("operation", "message"),
    [
        (
            {
                "type": "change_date_formats",
                "target_field": "name",
                "formats": ["%Y"],
            },
            "name is not a parse_date expression",
        ),
        (
            {
                "type": "extend_enum_map",
                "target_field": "name",
                "mapping": {"x": "y"},
            },
            "name is not a map_values expression",
        ),
    ],
)
def test_specialized_patch_rejects_wrong_expression_type(operation, message):
    patch = ProgramPatch.model_validate({"patches": [operation]})

    with pytest.raises(ValueError, match=message):
        apply_patch(_program(), patch)
