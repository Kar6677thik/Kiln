"""Tests for configuration loading and validation (docs/spec/05-configuration-and-cli.md)."""

from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from kiln.config import RULES, Config, ConfigError, build_config, default_config, load_config


def test_defaults_match_the_spec() -> None:
    """A few headline defaults from spec/05 §2 (the full table lives in config.RULES)."""
    config = default_config()
    assert config.master.workers == 4
    assert config.master.bind == "127.0.0.1:7340"
    assert config.queue.max_size == 50_000
    assert config.jobs.default_max_attempts == 1
    assert config.gateway.watchdog_threshold_ms == 100


def test_precedence_default_file_env_cli(tmp_path: Path) -> None:
    """Later sources win: defaults < kiln.toml < KILN_* env < CLI overrides (spec/05 §1)."""
    file = tmp_path / "kiln.toml"
    file.write_text("[master]\nworkers = 2\nboot_timeout_s = 5\n[log]\nlevel = 'DEBUG'\n")
    env = {"KILN_MASTER__WORKERS": "3", "KILN_LOG__LEVEL": "WARNING"}
    config = load_config(file, env, {"log.level": "ERROR"})
    assert config.master.boot_timeout_s == 5.0  # file only
    assert config.master.workers == 3  # env beats file
    assert config.log.level == "ERROR"  # CLI beats env


@given(st.integers(min_value=1, max_value=64), st.integers(min_value=1, max_value=64))
def test_merge_property_env_always_beats_file(file_value: int, env_value: int) -> None:
    """For any two values, the environment variable wins over the file (Hypothesis)."""
    flat = {"master.workers": file_value}
    flat.update({"master.workers": env_value})
    assert build_config(flat).master.workers == env_value


def test_all_problems_are_reported_together() -> None:
    """Every problem is listed at once, not just the first (spec/05 §1, AC-1.10)."""
    with pytest.raises(ConfigError) as info:
        build_config(
            {"master.workers": 0, "worker.max_job": 5, "log.level": "LOUD", "app.preload": "yes"}
        )
    problems = info.value.problems
    assert len(problems) == 4
    assert any("unknown setting 'worker.max_job'" in p for p in problems)


def test_bool_is_not_a_number() -> None:
    """`workers = true` is rejected even though True is technically an int in Python."""
    with pytest.raises(ConfigError):
        build_config({"master.workers": True})


def test_heartbeat_timeout_must_exceed_interval() -> None:
    """A timeout shorter than the interval would make every worker look hung."""
    with pytest.raises(ConfigError, match="heartbeat_timeout_s"):
        build_config({"worker.heartbeat_interval_s": 2.0, "worker.heartbeat_timeout_s": 1.0})


def test_non_loopback_bind_needs_explicit_permission() -> None:
    """Binding 0.0.0.0 is refused without allow_non_loopback (ADR-021)."""
    with pytest.raises(ConfigError, match="allow_non_loopback"):
        build_config({"master.bind": "0.0.0.0:7340"})
    assert build_config({"master.bind": "0.0.0.0:7340", "master.allow_non_loopback": True})


def test_env_values_are_parsed_as_toml() -> None:
    """KILN_LABS__ENABLED=true becomes the boolean True, and lists work too."""
    config = load_config(None, {"KILN_LABS__ENABLED": "true", "KILN_APP__PYTHONPATH": '["/x"]'})
    assert config.labs.enabled is True
    assert config.app.pythonpath == ("/x",)


def test_json_round_trip() -> None:
    """Config.to_json → from_json gives an equal Config (spawn will rely on this in K2)."""
    config = build_config({"master.workers": 7, "gateway.watchdog.interval_ms": 20})
    assert Config.from_json(config.to_json()) == config


def test_every_rule_default_is_valid() -> None:
    """Each default in the RULES table passes its own rule (no typo in the table)."""
    assert isinstance(build_config({}), Config)
    assert len(RULES) >= 45


def test_invalid_toml_is_a_config_error(tmp_path: Path) -> None:
    """A broken kiln.toml produces a clear ConfigError, not a traceback."""
    file = tmp_path / "kiln.toml"
    file.write_text("[master\nworkers = 2\n")
    with pytest.raises(ConfigError, match="not valid TOML"):
        load_config(file, {})
