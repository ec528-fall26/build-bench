"""Part E: the ablation switches, read from the environment.

Each AgentConfig field has one variable, so an experiment can turn a single
capability off without touching the code (docs/full-agent.md, "Ground rules"):

    BB_AGENT_USE_EVIDENCE_MANAGER   BB_AGENT_USE_TOOLS
    BB_AGENT_USE_BUILD_FEEDBACK     BB_AGENT_USE_HISTORY

Unset or empty means on: the competition runs the agent with no variables set.
A value that is not clearly on or off raises instead of falling back, because a
typo must not silently run the wrong arm of an ablation.

Standard library only.
"""

from __future__ import annotations

import dataclasses
import os
from typing import Mapping

from .contracts import AgentConfig


ENV_PREFIX = "BB_AGENT_"
_ON = {"1", "true", "yes", "on"}
_OFF = {"0", "false", "no", "off"}


def env_name(field: str) -> str:
    return ENV_PREFIX + field.upper()


def config_from_env(environ: Mapping[str, str] | None = None) -> AgentConfig:
    """Build the AgentConfig for this run from BB_AGENT_USE_* variables."""
    environ = os.environ if environ is None else environ
    values = {}
    for field in dataclasses.fields(AgentConfig):
        raw = (environ.get(env_name(field.name)) or "").strip().lower()
        if not raw:
            continue
        if raw not in _ON | _OFF:
            raise ValueError(
                f"{env_name(field.name)}={environ[env_name(field.name)]!r} is not a switch; "
                "use 1 or 0")
        values[field.name] = raw in _ON
    return AgentConfig(**values)


def config_env(config: AgentConfig) -> dict[str, str]:
    """The variables that reproduce ``config``: the inverse of config_from_env."""
    return {env_name(name): "1" if value else "0"
            for name, value in dataclasses.asdict(config).items()}


def config_record(config: AgentConfig) -> dict[str, bool]:
    """The switches as they ran, for agent-result.json and the harness run record.

    The comparison script checks this against the configuration a run was
    launched as, so a row cannot be reported under the wrong arm.
    """
    return dataclasses.asdict(config)
