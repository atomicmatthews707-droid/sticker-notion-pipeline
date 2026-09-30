import pytest
from engine.spec_generator import generate_spec

def test_dry_run_spec():
    # Verify mock output passes its own validation
    spec = generate_spec("Test Niche", dry_run=True)
    assert spec["template_name"] == "Test Niche Template"
    assert len(spec["databases"]) == 1
