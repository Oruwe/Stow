import pytest

from app.config import ConfigError, load


def test_defaults_when_no_config_present(tmp_path):
    config = load(tmp_path)
    assert config.fail_on == "medium"
    assert config.disabled_rules == []


def test_dedicated_file_is_loaded(tmp_path):
    (tmp_path / ".dockerfile-optimizer.toml").write_text(
        'disabled_rules = ["missing_healthcheck"]\nfail_on = "high"\nformat = "text"\n'
    )
    config = load(tmp_path)
    assert config.disabled_rules == ["MISSING_HEALTHCHECK"]  # normalised
    assert config.fail_on == "high"
    assert config.output_format == "text"


def test_pyproject_tool_table_is_loaded(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[tool.dockerfile-optimizer]\nfail_on = "critical"\n'
    )
    assert load(tmp_path).fail_on == "critical"


def test_config_is_discovered_from_a_subdirectory(tmp_path):
    (tmp_path / ".dockerfile-optimizer.toml").write_text('fail_on = "low"\n')
    nested = tmp_path / "services" / "api"
    nested.mkdir(parents=True)
    assert load(nested).fail_on == "low"


def test_dedicated_file_wins_over_pyproject(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[tool.dockerfile-optimizer]\nfail_on = "critical"\n')
    (tmp_path / ".dockerfile-optimizer.toml").write_text('fail_on = "low"\n')
    assert load(tmp_path).fail_on == "low"


def test_invalid_severity_is_rejected_loudly(tmp_path):
    """Silently ignoring a typo would leave a team believing they had a gate."""
    (tmp_path / ".dockerfile-optimizer.toml").write_text('fail_on = "blocker"\n')
    with pytest.raises(ConfigError, match="fail_on"):
        load(tmp_path)


def test_malformed_disabled_rules_is_rejected(tmp_path):
    (tmp_path / ".dockerfile-optimizer.toml").write_text('disabled_rules = "PINNED_VERSION"\n')
    with pytest.raises(ConfigError, match="disabled_rules"):
        load(tmp_path)


def test_unrelated_pyproject_does_not_stop_the_walk(tmp_path):
    (tmp_path / ".dockerfile-optimizer.toml").write_text('fail_on = "high"\n')
    nested = tmp_path / "pkg"
    nested.mkdir()
    (nested / "pyproject.toml").write_text('[project]\nname = "x"\n')
    assert load(nested).fail_on == "high"
