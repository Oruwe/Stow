"""Configuration loading.

A team needs to silence a rule that does not apply to them without forking the
tool, so settings come from a file in the repo and are overridden by CLI flags.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CONFIG_FILENAME = ".dockerfile-optimizer.toml"
PYPROJECT = "pyproject.toml"
TOOL_TABLE = "dockerfile-optimizer"
VALID_SEVERITIES = ("low", "medium", "high", "critical")


@dataclass
class Config:
    disabled_rules: list[str] = field(default_factory=list)
    fail_on: str = "medium"
    output_format: str = "json"
    source: str | None = None


class ConfigError(ValueError):
    """Raised when a config file exists but cannot be honoured."""


def _coerce(table: dict[str, Any], source: str) -> Config:
    disabled = table.get("disabled_rules", [])
    if not isinstance(disabled, list) or not all(isinstance(r, str) for r in disabled):
        raise ConfigError(f"{source}: 'disabled_rules' must be a list of rule IDs.")

    fail_on = table.get("fail_on", "medium")
    if fail_on not in VALID_SEVERITIES:
        raise ConfigError(
            f"{source}: 'fail_on' must be one of {', '.join(VALID_SEVERITIES)}, got '{fail_on}'."
        )

    output_format = table.get("format", "json")
    if output_format not in ("json", "text", "sarif"):
        raise ConfigError(f"{source}: 'format' must be json, text or sarif.")

    return Config([r.upper() for r in disabled], fail_on, output_format, source)


def load(start: Path | None = None) -> Config:
    """Find and load config, walking up from `start` to the filesystem root."""
    current = (start or Path.cwd()).resolve()
    for directory in [current, *current.parents]:
        dedicated = directory / CONFIG_FILENAME
        if dedicated.is_file():
            with dedicated.open("rb") as handle:
                return _coerce(tomllib.load(handle), str(dedicated))

        pyproject = directory / PYPROJECT
        if pyproject.is_file():
            with pyproject.open("rb") as handle:
                table = tomllib.load(handle).get("tool", {}).get(TOOL_TABLE)
            if table is not None:
                return _coerce(table, f"{pyproject} [tool.{TOOL_TABLE}]")
    return Config()
