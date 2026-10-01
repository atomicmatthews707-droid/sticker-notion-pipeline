import pytest

from src.shared import config


def test_dotted_get_with_defaults():
    assert config.get("generator.max_workers") == 4
    assert config.get("nope.nothing", "fallback") == "fallback"
    assert config.get("generator.max_workers.deeper", 1) == 1


def test_config_path_override(tmp_path, monkeypatch):
    p = tmp_path / "c.yaml"
    p.write_text("budget:\n  daily_limit_usd: 3\n")
    monkeypatch.setenv("CONFIG_PATH", str(p))
    config.reload_config()
    assert config.daily_budget_usd() == 3.0


def test_budget_env_overrides_config(monkeypatch):
    monkeypatch.setenv("BUDGET_DAILY_LIMIT_USD", "1.5")
    assert config.daily_budget_usd() == pytest.approx(1.5)


def test_output_dir_follows_env(tmp_path):
    assert config.output_dir() == tmp_path / "out"


def test_price_keys_are_normalised_to_int():
    from src.main import _price_for_size

    assert list(_price_for_size({"25": 8, 10: 5, "50": 12})) == [10, 25, 50]
