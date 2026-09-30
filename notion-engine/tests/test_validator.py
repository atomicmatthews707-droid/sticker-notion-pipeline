import pytest
from engine.spec_generator import _validate_spec

def test_validator():
    # Test board view without valid property
    spec = {
        "databases": [
            {
                "name": "DB",
                "properties": [{"name": "Text", "type": "rich_text"}],
                "views": [{"type": "board", "group_by_property": "Text"}]
            }
        ]
    }
    errors = _validate_spec(spec)
    assert len(errors) == 1
    assert "must be select, status, or multi_select" in errors[0]

    # Test relation targeting non-existent DB
    spec2 = {
        "databases": [
            {
                "name": "DB",
                "properties": [{"name": "Rel", "type": "relation", "target_db": "Missing"}]
            }
        ]
    }
    errors2 = _validate_spec(spec2)
    assert len(errors2) == 1
    assert "not found in spec databases" in errors2[0]
