"""Shared helpers for MCP tool modules."""

import os

from utils.runtime_config_loader import RuntimeConfig


def get_sessions_dir() -> str:
    project_config = RuntimeConfig.get_section("Project")
    return os.path.join(
        project_config.get("location", "storage"),
        project_config.get("name", "smart-classroom"),
    )
